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
from urllib.parse import parse_qs, urlparse

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

# queryString 里应当出现的关键字段，用于校验手工粘贴的 URL 是否完整
REQUIRED_QS_FIELDS = ("wlanuserip", "mac", "nasip")

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 5

LINE = "=" * 62

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


def _session() -> requests.Session:
    """与主程序一致：绕过系统代理。"""
    s = requests.Session()
    s.trust_env = False
    return s


def step_environment() -> None:
    print("\n[1/4] 环境检查")
    print("-" * 62)

    ip = _local_ip()
    print(f"  本机出口 IP : {ip or '(获取失败)'}")

    # 代理是「探测不到门户」最常见的元凶
    found = {k: v for k, v in os.environ.items() if k in PROXY_ENV_KEYS and v}
    if found:
        print("  系统代理     : 检测到以下代理环境变量 ⚠")
        for k, v in found.items():
            print(f"      {k}={v}")
        print()
        print("      → 程序探测时会**绕过**这些代理（因为请求一旦走代理，")
        print("        网关就劫持不到，永远拿不到认证页跳转）。")
        print("        如果你的浏览器能跳到认证页而这里探测不到，多半是代理在起作用。")
    else:
        print("  系统代理     : 无")
    print()


def probe_one(url: str) -> dict:
    """探测单个 URL，返回状态码 / 跳转链 / 最终 URL / 抓到的 queryString。"""
    result = {
        "url": url,
        "status": 0,
        "final_url": "",
        "hops": [],
        "query_string": "",
        "error": "",
    }

    try:
        resp = _session().get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    result["status"] = resp.status_code
    result["final_url"] = resp.url
    result["hops"] = [h.url for h in resp.history]

    if looks_like_portal(resp.url, original_url=url):
        result["query_string"] = _extract_query_string(resp.url)

    return result


def step_connectivity() -> bool:
    print("\n[2/4] 网络连通性探测")
    print("-" * 62)

    online = False
    for target in ("http://www.baidu.com", "https://www.bilibili.com/"):
        try:
            r = _session().get(target, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            host = target.split("/")[2]
            ok = r.status_code > 0 and host in r.url
            print(f"  {target}")
            print(f"      状态 {r.status_code}  最终 {r.url}")
            if ok:
                online = True
        except Exception as e:  # noqa: BLE001
            print(f"  {target}")
            print(f"      失败：{type(e).__name__}: {e}")

    print()
    if online:
        print("  → 已联网。这台机器现在能正常上网，")
        print("    多半已经通过认证，或所在网络根本不需要认证。")
    else:
        print("  → 不通外网，符合「待认证」状态，继续看下一步。")

    return online


def step_probe() -> str:
    print("\n[3/4] 门户重定向探测")
    print("-" * 62)
    print("  逐个访问探测地址，看网关会不会把请求劫持到认证页。\n")

    found = ""
    for url in PROBE_URLS:
        r = probe_one(url)
        print(f"  探测 {url}")

        if r["error"]:
            print(f"      请求失败：{r['error']}")
            print("      → 未跳转 ✗\n")
            continue

        print(f"      状态 {r['status']}")
        if r["hops"]:
            print(f"      跳转 {len(r['hops'])} 次：")
            for hop in r["hops"]:
                print(f"        - {hop}")
        print(f"      最终 {r['final_url']}")

        if r["query_string"]:
            shown = r["query_string"]
            if len(shown) > 120:
                shown = shown[:120] + "..."
            print("      → 命中门户 ✓")
            print(f"      → queryString: {shown}")
            if not found:
                found = r["query_string"]
        else:
            print("      → 未跳转 ✗")
        print()

    return found


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

    # 容错：只粘了裸 queryString 的情况
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


def step_conclusion(online: bool, query_string: str) -> None:
    print("\n[4/4] 结论")
    print("-" * 62)

    if query_string:
        print("  ✓ queryString 抓取正常。")
        print()
        print("  如果程序仍然报「Query String is Invalid」，那说明问题不在这台机器")
        print("  的网络，而是上游调用把空值传了进来 —— 请把完整日志一并发来排查。")
        return

    print("  ✗ 没抓到 queryString，网关没有把这台机器重定向到认证页。")
    print()
    print("  按可能性排查：")
    print()
    if online:
        print("  1. 【最可能】这台机器已经能上网了。程序会认为「无需认证」而直接")
        print("     返回空 queryString —— 这是预期行为，不是故障。")
        print("     如果你确实需要重新认证，先断开网络再试。")
    else:
        print("  1. 确认这台机器接的是校园网（有线网口 或 i-SHMU 无线），")
        print("     而不是手机热点、其他 WiFi。")

    print("  2. 用浏览器手动打开 http://www.shmtu.edu.cn （注意是 http 不是 https）：")
    print("     - 会跳到认证页 → 网关正常，是探测地址被放行/缓存了。")
    print("       把跳转后地址栏的完整 URL 粘回来：")
    print("         python diagnose_portal.py --url \"<粘贴的URL>\"")
    print("       脚本会校验字段并生成可直接用的配置行。")
    print("     - 不跳转       → 当前网络不需要认证，或不在校园网内。")
    print()
    print("  3. 校园网通常不对 HTTPS 做劫持，务必用 http:// 开头的地址测试。")
    print("  4. 若上面第 1 步提示检测到系统代理，先临时关掉代理再测。")


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
        online = step_connectivity()
        query_string = step_probe()
        step_conclusion(online, query_string)

    print()
    print(LINE)


if __name__ == "__main__":
    main()
