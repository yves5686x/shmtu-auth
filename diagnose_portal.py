#!/usr/bin/env python3
"""门户认证诊断工具。

在报「Query String is Invalid」的机器上跑这个脚本，它会把 queryString 抓取的
每一步都打出来，直接定位卡在哪一环 —— 不用靠猜。

用法::

    # 完整诊断（探测 + 结论）
    python diagnose_portal.py

    # 已有认证页 URL 时，直接解析并生成可粘贴的配置
    python diagnose_portal.py --url "https://ismu.shmtu.edu.cn:8443/eportal/index.jsp?wlanuserip=..."

只依赖 requests，不依赖项目其他模块，方便单独拷到出问题的机器上跑。
"""

import argparse
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

# Windows 控制台默认可能是 GBK，打印中文/符号会抛 UnicodeEncodeError。
# 必须在任何输出之前设置好。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import requests
except ImportError:
    print("缺少依赖：请先 pip install requests")
    sys.exit(1)

# 与主程序 get_query_string_requests.py 保持一致的探测列表
PROBE_URLS = [
    "http://1.1.1.1",
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://neverssl.com",
    "http://example.com",
    "http://www.shmtu.edu.cn",
]

# 连通性探测目标：(URL, 期望域名, 响应体特征串)
# 与主程序 CONNECTIVITY_TARGETS 一致
CONNECTIVITY_TARGETS = [
    ("http://www.baidu.com", ("baidu.com",), ("baidu", "百度")),
    ("https://www.bilibili.com/", ("bilibili.com",), ("bilibili",)),
    ("https://www.qq.com/", ("qq.com",), ("qq.com",)),
]

# DNS 检查用的域名（挑不同注册商/不同用途，方便看出是不是被统一劫持）
DNS_CHECK_HOSTS = [
    "www.baidu.com",
    "www.example.com",
    "www.qq.com",
    "www.shmtu.edu.cn",
]

# 门户域名特征（与主程序一致）
PORTAL_HOST_MARKERS = (
    "ismu.shmtu.edu.cn",
    "hwifi.shmtu.edu.cn",
)

# 门户路径特征
PORTAL_PATH_MARKERS = (
    "/eportal/",
    "auth.html",
    "portalpage",
    "portalauth",
)

# 响应体里出现这些词，说明拿到的是认证/拦截页而不是真正的网页
PORTAL_BODY_MARKERS = (
    "eportal",
    "wlanuserip",
    "portalpage",
    "上网认证",
    "认证页面",
    "请先认证",
    "网络认证",
)

# queryString 里应当出现的关键字段，用于校验手工粘贴的 URL 是否完整
REQUIRED_QS_FIELDS = ("wlanuserip", "mac", "nasip")

# 与主程序一致的超时。探测全部并发，所以总耗时取决于最慢的那一个。
CONNECT_TIMEOUT = 2
READ_TIMEOUT = 4

LINE = "=" * 62
SUB = "-" * 62

# 会让 requests 走代理的环境变量
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def looks_like_portal(final_url: str, original_url: str = "") -> bool:
    """与主程序同款三层判据：域名 / 路径 / 跨域跳转带参数。"""
    final_url = (final_url or "").strip()
    if not final_url:
        return False

    host = (urlparse(final_url).hostname or "").lower()
    lowered = final_url.lower()

    if host and any(
        host == marker or host.endswith(f".{marker}") for marker in PORTAL_HOST_MARKERS
    ):
        return True

    if any(marker in lowered for marker in PORTAL_PATH_MARKERS):
        return True

    if original_url:
        original_host = (urlparse(original_url).hostname or "").lower()
        if original_host and host and host != original_host and "?" in final_url:
            return True

    return False


def _extract_query_string(any_url: str) -> str:
    """与主程序一致：取 ? 后面的部分，并把 & = 编码成表单需要的样子。"""
    q_index = (any_url or "").find("?")
    if q_index <= 0:
        return ""
    qs = any_url[q_index + 1 :].strip()
    return qs.replace("&", "%26").replace("=", "%3D")


def _local_ip() -> str:
    """本机对外出口 IP（不发包，只让内核选一条路由）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except Exception:  # noqa: BLE001
        return ""
    finally:
        sock.close()


def _resolve(host: str) -> tuple[str, str]:
    """解析一个域名，返回 (IP, 错误描述)。"""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET)
        return infos[0][4][0], ""
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}: {e}"


def _snippet(text: str, limit: int = 300) -> str:
    """把响应体压成一行摘要，方便在终端里看清到底返回了什么。"""
    if not text:
        return "(空)"
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[:limit] + " ...(略)"
    return flat


def _fetch(url: str) -> dict:
    """探测单个 URL。绕过系统代理（与主程序一致）。"""
    result = {
        "url": url,
        "status": 0,
        "final_url": "",
        "text": "",
        "query_string": "",
        "error": "",
    }

    session = requests.Session()
    session.trust_env = False
    try:
        resp = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        resp.encoding = resp.apparent_encoding
        result["status"] = resp.status_code
        result["final_url"] = resp.url
        result["text"] = resp.text or ""
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    finally:
        session.close()

    if looks_like_portal(resp.url, original_url=url):
        result["query_string"] = _extract_query_string(resp.url)

    return result


def _fetch_many(urls: list[str]) -> list[dict]:
    """并发探测，返回顺序与输入一致。"""
    slots: list[dict | None] = [None] * len(urls)

    def _one(index: int, url: str) -> None:
        slots[index] = _fetch(url)

    with ThreadPoolExecutor(max_workers=min(len(urls), 8)) as pool:
        for index, url in enumerate(urls):
            pool.submit(_one, index, url)

    return [item for item in slots if item is not None]


# ---------------------------------------------------------------- 步骤 1

def step_environment() -> None:
    print("\n[1/5] 环境检查")
    print(SUB)

    ip = _local_ip()
    print(f"  本机出口 IP : {ip or '(获取失败)'}")

    found = {k: v for k, v in os.environ.items() if k in PROXY_ENV_KEYS and v}
    if found:
        print("  系统代理     : 检测到以下代理环境变量 ⚠")
        for k, v in found.items():
            print(f"      {k}={v}")
        print()
        print("      → 程序探测时会绕过这些代理（请求一旦走代理，")
        print("        网关就劫持不到，永远拿不到认证页跳转）。")
    else:
        print("  系统代理     : 无")
    print()


# ---------------------------------------------------------------- 步骤 2

def step_dns() -> tuple[bool, bool]:
    """DNS 检查。返回 (dns_可用, 疑似劫持)。

    这一步很关键：网关劫持的前提是「DNS 放行 + TCP 可达」。
    DNS 都解析不了的话，任何基于域名的探测必然失败，
    连被劫持的资格都没有 —— 那就不是认证问题，是网络配置问题。
    """
    print("\n[2/5] DNS 解析检查")
    print(SUB)

    resolved: dict[str, str] = {}
    failed: list[str] = []

    for host in DNS_CHECK_HOSTS:
        ip, err = _resolve(host)
        if ip:
            resolved[host] = ip
            print(f"  {host:22s} -> {ip}")
        else:
            failed.append(host)
            print(f"  {host:22s} -> 解析失败  {err}")

    print()

    if not resolved:
        print("  ⚠ DNS 完全不可用：所有域名都解析不出 IP。")
        print()
        print("    这是比「没认证」更基础的问题 —— 网关劫持页面的前提是")
        print("    DNS 能解析出 IP，DNS 都不通的话程序不可能抓到 queryString，")
        print("    浏览器同样打不开任何网站。")
        print()
        print("    请检查：")
        print("      1. 网卡的 DNS 是否设成了「自动获得 DNS 服务器地址」")
        print("      2. 是否连对了网络（校园网有线网口 / i-SHMU 无线）")
        print("      3. 换个网口 / 重连一次 Wi-Fi，重新拿 DHCP")
        return False, False

    if failed:
        print(f"  ⚠ 部分域名解析失败：{', '.join(failed)}")

    unique_ips = set(resolved.values())
    hijack = len(resolved) >= 2 and len(unique_ips) == 1
    if hijack:
        print(f"  ⚠ 所有域名都解析到同一个 IP：{unique_ips.pop()}")
        print()
        print("    这是典型的 DNS 劫持 —— 请求被统一送到一台代理/拦截服务器上，")
        print("    所以下面会看到「每个网站都返回 200 但浏览器都打不开」。")
    else:
        print(f"  → DNS 正常（{len(resolved)}/{len(DNS_CHECK_HOSTS)} 个解析成功，"
              f"{len(unique_ips)} 个不同 IP）")

    return True, hijack


# ---------------------------------------------------------------- 步骤 3

def step_connectivity() -> tuple[bool, str]:
    """http + https 双通道连通性探测。返回 (是否联网, 判定理由)。"""
    print("\n[3/5] 连通性探测（http + https 双通道）")
    print(SUB)
    print("  校园网只劫持 http 明文、不碰 https。所以")
    print("  「http 全 200、https 全超时」是一眼假的组合 —— 那些 200")
    print("  只可能是透明代理/缓存伪造的，浏览器同样打不开。\n")

    urls = [t[0] for t in CONNECTIVITY_TARGETS]
    results = {r["url"]: r for r in _fetch_many(urls)}

    https_ok: list[str] = []
    http_ok: list[str] = []
    https_reachable: list[str] = []

    for url, hosts, markers in CONNECTIVITY_TARGETS:
        r = results.get(url, {"status": 0, "final_url": "", "text": "", "error": "无结果"})
        scheme = urlparse(url).scheme
        print(f"  {url}")

        if r.get("error"):
            print(f"      请求失败：{r['error']}")
            print("      （连不上，没有拿到任何响应）")
            print()
            continue

        print(f"      状态 {r['status']}  长度 {len(r['text'])}  最终 {r['final_url']}")
        print(f"      响应体: {_snippet(r['text'])}")

        host = (urlparse(r["final_url"]).hostname or "").lower()
        if not any(host == h or host.endswith(f".{h}") for h in hosts):
            print("      ✗ 被劫持到别的域名")
            print()
            continue

        # 有响应 ⇒ 服务器真的响应了，外网是通的
        if scheme == "https":
            https_reachable.append(url)

        if not 200 <= r["status"] < 300:
            # 4xx/5xx 说明 TLS 握手成功、服务器真响应了（常见于 WAF 拦爬虫）。
            # 这跟「连不上」是两回事，外网其实是通的。
            print(f"      ~ 状态码非 2xx，但服务器有响应 → 外网可达")
        elif not any(m.lower() in r["text"].lower() for m in markers):
            print("      ✗ 响应体里没有该站点的特征内容（伪造的页面）")
        else:
            print("      ✓ 通过")
            (https_ok if scheme == "https" else http_ok).append(url)
        print()

    if https_ok:
        verdict = True
        reason = f"https 通道正常（{https_ok[0]}）"
        print(f"  → 已联网。{reason}。")
        print("    这台机器现在能正常上网，多半已通过认证，或该网络不需要认证。")
    elif http_ok and https_reachable:
        verdict = True
        reason = "http 拿到真实内容，且 https 目标有响应（外网可达）"
        print(f"  → 已联网。{reason}。")
        print()
        print("    https 那几个虽然状态码不是 2xx，但服务器确实响应了")
        print("    （常见于 WAF 拦爬虫），这跟「连不上」是两回事。")
    elif http_ok:
        verdict = False
        reason = "只有 http 通过、https 全部连不上（状态码 0）"
        print(f"  → 未联网。{reason}。")
        print()
        print("    ⚠ 这是典型的「假联网」：透明代理 / 缓存 / DNS 劫持对所有 http")
        print("    请求返回 200，但 https 根本连不出去。看上面「响应体」一栏，")
        print("    如果内容空白或不是该网站，就坐实了是伪造的。")
        print("    程序会按「需要认证」处理，继续下一步。")
    else:
        verdict = False
        reason = "http / https 均未拿到预期内容"
        print(f"  → 未联网。{reason}，符合待认证状态。")

    return verdict, reason


# ---------------------------------------------------------------- 步骤 4

def step_probe() -> str:
    print("\n[4/5] 门户重定向探测")
    print(SUB)
    print("  并发访问探测地址，看网关会不会把请求劫持到认证页。\n")

    found = ""
    for r in _fetch_many(PROBE_URLS):
        print(f"  探测 {r['url']}")

        if r["error"]:
            print(f"      请求失败：{r['error']}")
            print("      → 未跳转 ✗\n")
            continue

        print(f"      状态 {r['status']}  最终 {r['final_url']}")
        print(f"      响应体: {_snippet(r['text'], 200)}")

        if r["query_string"]:
            shown = r["query_string"]
            if len(shown) > 120:
                shown = shown[:120] + "..."
            print("      → 命中门户 ✓")
            print(f"      → queryString: {shown}")
            if not found:
                found = r["query_string"]
        else:
            body = (r["text"] or "").lower()
            if any(m.lower() in body for m in PORTAL_BODY_MARKERS):
                print("      → 响应体像认证页，但 URL 里没有 queryString ⚠")
                print("        这种情况请把上面「响应体」的内容一并发来排查")
            else:
                print("      → 未跳转 ✗")
        print()

    return found


# ---------------------------------------------------------------- 手工 URL

def explain_url(url: str) -> None:
    """解析用户粘贴的认证页 URL，校验字段并给出可直接用的配置行。"""
    print(LINE)
    print(" 解析手工粘贴的认证页 URL")
    print(LINE)

    url = (url or "").strip()
    if not url:
        print("  --url 参数为空")
        sys.exit(1)

    print(f"\n  原始输入：{url}\n")

    if "?" in url:
        portal_url, raw_qs = url.split("?", 1)
    elif url.lower().startswith(("http://", "https://")):
        portal_url, raw_qs = url, ""
    else:
        portal_url, raw_qs = "", url

    print(f"  门户地址   : {portal_url or '(未提供)'}")
    if not raw_qs:
        print()
        print("  ✗ 这个 URL 里没有 queryString（? 后面没有内容）。")
        print("    请确认你复制的是**跳转后**地址栏里的完整 URL。")
        sys.exit(1)

    fields = parse_qs(raw_qs)
    print(f"  解析字段   : {', '.join(fields.keys()) or '(无)'}")

    missing = [f for f in REQUIRED_QS_FIELDS if f not in fields]
    print()

    if missing:
        print(f"  ⚠ 缺少关键字段：{', '.join(missing)}")
        print("    这些字段缺失会导致门户解不开密码（尤其是 mac）。")
        print("    请回到浏览器重新复制一次完整 URL。")
    else:
        print("  ✓ 关键字段齐全（wlanuserip / mac / nasip）")

    encoded = _extract_query_string(url)
    print()
    print("  把下面这一行写进 config.toml 的 [Portal] 段（或设同名环境变量）：")
    print()
    print(f'      SHMTU_AUTH_QUERY_STRING = "{url}"')
    print()
    print("  程序检测到该配置后会跳过自动探测，直接用它。")
    print("  ⚠ 注意 queryString 由网关现生成、绑定本机当前 IP，")
    print("    换机器或重连网络后会失效，需要重新粘贴。")


# ---------------------------------------------------------------- 结论

def step_conclusion(online: bool, query_string: str, dns_ok: bool, dns_hijack: bool) -> None:
    print("\n[5/5] 结论")
    print(SUB)

    if query_string:
        print("  ✓ queryString 抓取正常。")
        print()
        print("  如果程序仍然报「Query String is Invalid」，那说明问题不在这台机器")
        print("  的网络，而是上游调用把空值传了进来 —— 请把完整日志一并发来排查。")
        return

    print("  ✗ 没抓到 queryString。")
    print()

    if not dns_ok:
        print("  【首要原因】DNS 完全不可用（见第 [2/5] 步）。")
        print()
        print("  DNS 解析不出 IP 时，程序**不可能**抓到 queryString ——")
        print("  因为网关把浏览器劫持到认证页，前提就是先要解析出 IP。")
        print("  这不是认证问题，是网络配置问题，先按第 [2/5] 步的建议修 DNS。")
        return

    print("  按可能性排查：")
    print()
    if online:
        print("  1. 【最可能】这台机器已经能上网了。程序会认为「无需认证」而直接")
        print("     返回空 queryString —— 这是预期行为，不是故障。")
    elif dns_hijack:
        print("  1. 【最可能】存在 DNS 劫持（见第 [2/5] 步，所有域名解析到同一个 IP）。")
        print("     请求被统一送到拦截服务器，所以探测地址全部返回 200 却不跳转。")
    else:
        print("  1. 确认这台机器接的是校园网（有线网口 或 i-SHMU 无线），")
        print("     而不是手机热点、其他 WiFi。")

    print("  2. 看第 [3/5] 步每个地址的「响应体」一栏：")
    print("     - 空白 / 很短 / 不是该网站的内容 → 响应是伪造的，机器其实上不了网")
    print("     - 内容正常 → 确实能上网，参考上面第 1 条")
    print()
    print("  3. 用浏览器手动打开 http://www.shmtu.edu.cn （注意是 http 不是 https）：")
    print("     - 会跳到认证页 → 网关正常，是探测地址被放行/缓存了。")
    print("       把跳转后地址栏的完整 URL 粘回来：")
    print("         python diagnose_portal.py --url \"<粘贴的URL>\"")
    print("       脚本会校验字段并生成可直接用的配置行。")
    print("     - 不跳转       → 当前网络不需要认证，或不在校园网内。")
    print()
    print("  4. 若第 [1/5] 步提示检测到系统代理，先临时关掉代理再测。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="shmtu-auth 门户认证诊断工具",
    )
    parser.add_argument(
        "--url",
        metavar="PORTAL_URL",
        help="解析手工粘贴的认证页 URL，校验字段并生成配置行",
    )
    args = parser.parse_args()

    print(LINE)
    print(" shmtu-auth 门户认证诊断")
    print(LINE)

    if args.url:
        explain_url(args.url)
    else:
        step_environment()
        dns_ok, dns_hijack = step_dns()
        online, _reason = step_connectivity()
        query_string = step_probe()
        step_conclusion(online, query_string, dns_ok, dns_hijack)

    print()
    print(LINE)


if __name__ == "__main__":
    main()
