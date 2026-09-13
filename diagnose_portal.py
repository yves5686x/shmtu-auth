#!/usr/bin/env python3
"""门户认证诊断工具。

在报「Query String is Invalid」的机器上跑这个脚本，它会把 queryString 抓取的
每一步都打出来，直接定位卡在哪一环 —— 不用靠猜。

用法::

    PYTHONPATH=src python diagnose_portal.py

只依赖 requests，不依赖项目其他模块，方便单独拷到出问题的机器上跑。
"""

import sys

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

# 命中这些字样说明跳转到了门户
PORTAL_MARKERS = ("hwifi.shmtu.edu.cn", "auth.html", "portalpage")

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 5

LINE = "=" * 62


def _hits_portal(url: str) -> bool:
    return any(marker in (url or "") for marker in PORTAL_MARKERS)


def _extract_query_string(any_url: str) -> str:
    """与主程序一致：取 ? 后面的部分，并把 & = 编码成表单需要的样子。"""
    q_index = (any_url or "").find("?")
    if q_index <= 0:
        return ""
    qs = any_url[q_index + 1 :].strip()
    return qs.replace("&", "%26").replace("=", "%3D")


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
        resp = requests.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    result["status"] = resp.status_code
    result["final_url"] = resp.url
    result["hops"] = [h.url for h in resp.history]

    if _hits_portal(resp.url):
        result["query_string"] = _extract_query_string(resp.url)

    return result


def step_connectivity() -> bool:
    print(f"\n[1/3] 网络连通性探测")
    print("-" * 62)

    online = False
    for target in ("http://www.baidu.com", "https://www.bilibili.com/"):
        try:
            r = requests.get(target, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            ok = r.status_code > 0 and target.split("/")[2] in r.url
            print(f"  {target}")
            print(f"      状态 {r.status_code}  最终 {r.url}")
            if ok:
                online = True
        except Exception as e:  # noqa: BLE001
            print(f"  {target}")
            print(f"      失败：{type(e).__name__}")

    print()
    if online:
        print("  → 已联网。这台机器现在能正常上网，")
        print("    多半已经通过认证，或所在网络根本不需要认证。")
    else:
        print("  → 不通外网，符合「待认证」状态，继续看下一步。")

    return online


def step_probe() -> str:
    print(f"\n[2/3] 门户重定向探测")
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
            print(f"      → 命中门户 ✓")
            print(f"      → queryString: {shown}")
            if not found:
                found = r["query_string"]
        else:
            print("      → 未跳转 ✗")
        print()

    return found


def step_conclusion(online: bool, query_string: str) -> None:
    print(f"\n[3/3] 结论")
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

    print("  2. 用浏览器手动打开 http://www.shmtu.edu.cn ：")
    print("     - 会跳到认证页 → 网关正常，是探测地址被放行/缓存了。")
    print("       试试换探测地址：SHMTU_AUTH_PROBE_URL=http://example.com")
    print("     - 不跳转       → 当前网络不需要认证，或不在校园网内。")
    print()
    print("  3. 校园网常对 HTTPS 不做劫持，务必用 http:// 开头的地址测试。")


def main() -> None:
    print(LINE)
    print(" shmtu-auth 门户认证诊断")
    print(LINE)

    online = step_connectivity()
    query_string = step_probe()
    step_conclusion(online, query_string)

    print()
    print(LINE)


if __name__ == "__main__":
    main()
