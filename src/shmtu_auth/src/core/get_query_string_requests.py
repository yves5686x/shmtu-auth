import ipaddress
import os
import queue
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, NamedTuple, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 全局 Session 复用连接
_session: requests.Session | None = None

# 超时配置（秒）
CONNECT_TIMEOUT = 2  # 连接超时
READ_TIMEOUT = 4     # 读取超时

# 探测并发度。串行走 5 个地址最坏要 5×(2+4)=30 秒，慢到用户以为卡死。
PROBE_WORKERS = 8
PROBE_TOTAL_TIMEOUT = 8.0
PROBE_BODY_LIMIT = 64 * 1024
# requests 无法取消正在阻塞的系统 DNS 调用。限制遗留任务数量，防止多轮堆积。
_PROBE_SLOTS = threading.BoundedSemaphore(PROBE_WORKERS)
_DNS_SLOTS = threading.BoundedSemaphore(PROBE_WORKERS)

# DNS 预检超时（秒）。见 probe_dns 的说明。
DNS_TIMEOUT = 2

# 连通性探测结果缓存。
#
# 一次「开始认证 / 程序启动」里，同一套 3 站点探测会被多处各自触发：
#   启动自检、AsyncNetworkTester、AuthThread 每轮、login 成功后的复核
# 以前每处都是独立打一轮网络，慢网下每轮都吃满超时，叠起来好几秒。
# 这里给结果加一个短 TTL，窗口内直接复用，不再重复探测。
# 用 SHMTU_AUTH_CONNECT_CACHE_TTL 覆盖（秒，<=0 表示禁用缓存）。
#
# ⚠️ 缓存的是「上一次探测的结论」，它不区分这个结论是动作**之前**还是**之后**
# 得到的。所以凡是「验证自己刚做过的动作」的调用（登录提交后的复核）都必须
# 传 force=True 跳过缓存，否则会把动作前的旧结论当成复核结果。
#
# ts 用 -inf 而不是 0.0：time.monotonic() 在部分平台是「开机以来的秒数」，
# 进程若在开机后极短时间内启动，0.0 会让首次调用误命中缓存。
_CONNECT_RESULT_CACHE: dict = {"ts": float("-inf"), "value": False}


def _connect_cache_ttl() -> float:
    raw = (os.environ.get("SHMTU_AUTH_CONNECT_CACHE_TTL", "") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(f"SHMTU_AUTH_CONNECT_CACHE_TTL 不是数字: {raw!r}，使用默认值")
    return 3.0


def reset_connect_cache() -> None:
    """清空连通性探测结果缓存。

    正常流程不需要主动调用；单测里每个用例各自 mock 网络，必须在用例
    开头清一次，否则会把上一个用例的结论带过来。
    """
    _CONNECT_RESULT_CACHE["ts"] = float("-inf")
    _CONNECT_RESULT_CACHE["value"] = False


# 门户域名特征。
#
# 2026-09 门户改版后统一到 ismu.shmtu.edu.cn:8443/eportal/，
# 旧的 hwifi.shmtu.edu.cn（解析到内网 10.32.45.5）是另一套门户，仍然保留，
# 因为部分无线网络环境还在用它。
PORTAL_HOST_MARKERS = (
    "ismu.shmtu.edu.cn",
    "hwifi.shmtu.edu.cn",
)

# 门户路径特征（不依赖域名，门户再换域名也能认出来）
PORTAL_PATH_MARKERS = (
    "/eportal/",
    "auth.html",
    "portalpage",
    "portalauth",
)


def looks_like_portal(final_url: str, original_url: str = "") -> bool:
    """判断最终 URL 是不是被网关劫持到了认证页。

    三层判据，从强到弱：

    1. 明确的门户域名（ismu / hwifi）
    2. 明确的门户路径（/eportal/、auth.html、portalpage、portalauth）
    3. 通用劫持特征：请求 A 却跳到了**完全不同的域名** B，且带了 queryString

    第 3 条是兜底，用来覆盖「门户又换域名 / 换路径」的情况 ——
    正常访问一个 http 明文地址不会跳到别的域名还带一串参数，
    会这么干的只有 captive portal。
    """
    final_url = (final_url or "").strip()
    if not final_url:
        return False

    parsed = urlparse(final_url)
    host = (parsed.hostname or "").lower()
    lowered = final_url.lower()

    # 1) 明确的门户域名
    if host and any(
        host == marker or host.endswith(f".{marker}") for marker in PORTAL_HOST_MARKERS
    ):
        return True

    # 2) 明确的门户路径
    if any(marker in lowered for marker in PORTAL_PATH_MARKERS):
        return True

    # 3) 通用劫持特征：跨域跳转 + 带 queryString
    if original_url:
        original_host = (urlparse(original_url).hostname or "").lower()
        if (
            original_host
            and host
            and host != original_host
            and "?" in final_url
        ):
            return True

    return False


class ProbeResult(NamedTuple):
    """一次探测的结果。text 保留下来是为了做响应体特征校验。"""

    url: str            # 原始请求地址
    text: str = ""      # 响应体（已按表观编码解码）
    status: int = 0     # 状态码，0 表示请求异常
    final_url: str = ""  # 重定向后的最终地址


def _get_session() -> requests.Session:
    """获取全局 Session，复用连接"""
    global _session
    if _session is None:
        _session = requests.Session()
        # 校园网探测必须绕过系统代理。
        #
        # requests 默认 trust_env=True，会读 HTTP_PROXY / HTTPS_PROXY 等环境变量。
        # 一旦机器（尤其是 Windows / 装了科学上网工具的机器）设了代理，
        # 探测请求会直接发给代理并拿到真实页面，网关的劫持跳转根本不会发生，
        # 于是永远抓不到 queryString —— 表现就是「没抓到门户地址」。
        _session.trust_env = False
    return _session


def _is_ip_literal(host: str) -> bool:
    """host 是不是裸 IP（如 1.1.1.1）—— 裸 IP 不需要 DNS 解析。"""
    try:
        ipaddress.ip_address((host or "").strip())
        return True
    except ValueError:
        return False


def _dns_resolve_one(host: str, timeout: float = DNS_TIMEOUT) -> Optional[bool]:
    """带超时的单次 DNS 解析：True/False 是明确结果，None 表示超时未知。

    ``socket.getaddrinfo`` **不支持超时参数**，而 Windows 在 DNS 服务器不可达时
    会反复重试（还带 NetBIOS 回退），实测能把一次解析拖到几十秒。
    这段时间完全不受 requests 的 timeout 约束 —— 因为 DNS 发生在建立连接
    **之前**，(connect, read) 两个超时值管不着它。

    实测某台机器：connect timeout 设的是 2 秒，实际等了 48 秒才报
    ConnectTimeout，整轮探测耗时 56 秒。

    所以放到 daemon 线程里跑，超时就放弃，不再干等。
    """
    if not _DNS_SLOTS.acquire(blocking=False):
        return None
    box: List[bool] = []

    def _worker() -> None:
        try:
            socket.getaddrinfo(host, None, socket.AF_INET)
            box.append(True)
        except Exception:  # noqa: BLE001
            box.append(False)
        finally:
            _DNS_SLOTS.release()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout)
    return box[0] if box else None


def probe_dns(hosts: List[str], timeout: float = DNS_TIMEOUT) -> dict:
    """并发做 DNS 预检，返回 {host: True/False/None}。

    None 表示超时没结论（既不能说通也不能说不通）。
    """
    hosts = [h for h in (hosts or []) if h]
    if not hosts:
        return {}

    with ThreadPoolExecutor(max_workers=min(len(hosts), 4)) as pool:
        futures = {pool.submit(_dns_resolve_one, h, timeout): h for h in hosts}
        return {futures[f]: f.result() for f in futures}


def probe_many(
    urls: List[str],
    connect_timeout: float = CONNECT_TIMEOUT,
    read_timeout: float = READ_TIMEOUT,
    dns_precheck: bool = True,
    total_timeout: float = PROBE_TOTAL_TIMEOUT,
    allow_redirects: bool = True,
) -> List[ProbeResult]:
    """并发探测，整轮共享总时限，按输入顺序返回（超时项 status=0）。

    requests 的 connect/read timeout 不约束系统 DNS、整个重定向链和
    持续滴流响应。守护线程在总时限后不再阻塞调用方；信号量限制尚未结束
    的底层请求数量。线程只往本轮队列交结果，迟到结果不会污染后续轮次。
    """
    urls = list(urls or [])
    if not urls:
        return []
    deadline = time.monotonic() + max(0.0, total_timeout)
    completed = queue.Queue()
    results = [ProbeResult(url=url) for url in urls]
    slots = _PROBE_SLOTS

    def _one(index: int, url: str) -> None:
        result = ProbeResult(url=url)
        session = None
        try:
            host = urlparse(url).hostname or ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if dns_precheck and host and not _is_ip_literal(host):
                if _dns_resolve_one(host, min(DNS_TIMEOUT, remaining)) is not True:
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            session = requests.Session()
            session.trust_env = False
            session.max_redirects = 3
            with session.get(
                url, timeout=(min(connect_timeout, remaining), min(read_timeout, remaining)),
                stream=True, allow_redirects=allow_redirects,
            ) as response:
                body = bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    if time.monotonic() >= deadline:
                        return
                    body.extend(chunk[:PROBE_BODY_LIMIT - len(body)])
                    if len(body) >= PROBE_BODY_LIMIT:
                        break
                text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                final_url = response.url
                if not allow_redirects and 300 <= response.status_code < 400:
                    final_url = urljoin(response.url, response.headers.get("Location", ""))
                result = ProbeResult(url, text, response.status_code, final_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Probe failed: {url} -> {type(exc).__name__}: {exc}")
        finally:
            try:
                if session is not None:
                    session.close()
            finally:
                slots.release()
                completed.put((index, result))

    pending = 0
    for index, url in enumerate(urls):
        if time.monotonic() >= deadline:
            break
        if not slots.acquire(blocking=False):
            logger.warning(f"探测任务仍在等待底层网络返回，跳过本轮目标: {url}")
            continue
        worker = threading.Thread(target=_one, args=(index, url), daemon=True)
        try:
            worker.start()
        except Exception:
            slots.release()
            raise
        pending += 1

    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            index, result = completed.get(timeout=remaining)
        except queue.Empty:
            break
        results[index] = result
        pending -= 1
    if pending:
        logger.warning(f"整轮探测达到 {total_timeout:g} 秒时限，{pending} 个未完成目标按未通过处理")
    return results


def get_text_code(url: str, timeout: float = READ_TIMEOUT) -> Tuple[str, int, str]:
    """单地址探测，返回 (响应体, 状态码, 最终URL)。失败时状态码为 0。"""
    results = probe_many([url], read_timeout=timeout)
    if not results:
        return "", 0, ""
    item = results[0]
    return item.text, item.status, item.final_url


def _is_expected_host(final_url: str, expected_hosts: tuple[str, ...]) -> bool:
    host = urlparse((final_url or "").strip()).hostname or ""
    host = host.lower()
    return any(host == expected or host.endswith(f".{expected}") for expected in expected_hosts)


# 连通性探测目标：(探测URL, 期望域名, 响应体特征串)
#
# 特征串是必须的，用来识别「代理 / 网关返回的错误页」：那种响应状态码可能是
# 200（甚至是 502），但内容根本不是目标网站的页面。只凭状态码判断，会把
# 「上不了网」误判成「已联网」，进而让程序认为无需认证、直接返回空 queryString。
CONNECTIVITY_TARGETS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("http://www.baidu.com", ("baidu.com",), ("baidu", "百度")),
    ("https://www.bilibili.com/", ("bilibili.com",), ("bilibili",)),
    ("https://www.qq.com/", ("qq.com",), ("qq.com",)),
)


def judge_connectivity(
    observations: List[tuple[str, tuple[str, ...], tuple[str, ...], ProbeResult]]
) -> Tuple[bool, str]:
    """根据探测结果判断是否真的能上网。

    ``observations`` 每项为 ``(探测URL, 期望域名, 响应体特征串, 探测结果)``。

    逐条看三条硬指标，任一不满足就不算通：

    1. 状态码必须是 2xx —— 4xx/5xx 说明被中间设备挡回来了
    2. 最终 URL 必须还在期望域名上 —— 跳走了说明被劫持
    3. 响应体必须含该站点的特征串 —— 挡住伪造的空壳页面

    最后还有一条**决定性**的：必须证明外网真的可达。

    校园网（以及绝大多数 captive portal）只劫持 http 明文，不碰 https。
    所以「http 全 200、https 全部**连不上**」是一眼假的组合 —— 那些 http 200
    只可能是透明代理 / 缓存服务器 / DNS 劫持伪造的，真机上浏览器同样打不开。

    注意要区分 https 的两种失败，判据完全不同：

      * 状态码 0（超时 / 连接失败）→ 根本没到服务器 → 外网不通
      * 状态码 4xx / 5xx         → TLS 握手成功、服务器真的响应了
                                  （常见于 WAF 拦爬虫，如 B 站 412、腾讯 501）
                                  → 外网其实是通的，只是这个目标不待见我们

    只凭 http 的 200 就判「已联网」，程序会认为无需认证，用户就彻底上不了网了。

    判错的代价是不对称的：
      把「未联网」误判成「已联网」→ 不认证 → 用户上不了网（严重）
      把「已联网」误判成「未联网」→ 多试一次认证，门户多半直接通过（无害）
    所以拿不准时一律判未联网。
    """
    https_passed: List[str] = []
    http_passed: List[str] = []
    https_reachable: List[str] = []
    rejections: List[str] = []

    for url, expected_hosts, body_markers, result in observations:
        if result.status == 0:
            rejections.append(f"{url} 请求失败")
            continue

        # 有响应且没被劫持 ⇒ 这个目标够得着，外网是通的
        if _is_expected_host(result.final_url, expected_hosts):
            if urlparse(url).scheme == "https":
                https_reachable.append(url)
        else:
            rejections.append(f"{url} 被劫持到 {result.final_url}")
            continue

        if not 200 <= result.status < 300:
            rejections.append(f"{url} 状态 {result.status}")
            continue
        if body_markers and not any(
            (marker or "").lower() in (result.text or "").lower()
            for marker in body_markers
        ):
            rejections.append(f"{url} 响应体不是预期内容")
            continue

        if urlparse(url).scheme == "https":
            https_passed.append(url)
        else:
            http_passed.append(url)

    if https_passed:
        return True, f"https 目标 {https_passed[0]} 正常返回预期内容"

    if http_passed and https_reachable:
        return True, (
            "http 目标 {} 返回预期内容，且 https 目标有响应（{}），外网可达"
        ).format(", ".join(http_passed), ", ".join(https_reachable))

    if http_passed:
        return False, (
            "只有 http 通道通过（{}），https 目标全部连不上（状态码 0）。"
            "http 的 200 很可能是透明代理 / 缓存 / DNS 劫持伪造的，不能算已联网"
        ).format(", ".join(http_passed))

    return False, "所有探测目标都失败：" + "；".join(rejections or ["无探测结果"])


def is_connect_by_sites(force: bool = False) -> bool:
    """用百度 / B 站 / QQ 探测联网状态，只有真正拿到内容才算已联网。

    :param force: 跳过 TTL 缓存，强制重新探测一次。**复核自己刚做过的动作时
        必须传 True**（例如登录提交后确认是否真的通了）：那一刻缓存里很可能
        还留着动作之前的结论，复用会把「刚登录成功」误判成「还没通」。
    """
    # 短 TTL 缓存：窗口内复用上次结论，避免一次流程里多轮重复探测。
    # 慢网下每轮都可能吃满 DNS/连接超时，是「测网络很慢」的主要来源。
    ttl = _connect_cache_ttl()
    now = time.monotonic()
    if not force and ttl > 0 and now - _CONNECT_RESULT_CACHE["ts"] <= ttl:
        cached = _CONNECT_RESULT_CACHE["value"]
        logger.debug(f"Reuse cached connectivity result (ttl={ttl}s): {cached}")
        return cached

    logger.info("Starting connectivity probe...")
    urls = [target[0] for target in CONNECTIVITY_TARGETS]
    results = probe_many(urls)

    by_url = {item.url: item for item in results}
    observations = [
        (url, hosts, markers, by_url.get(url, ProbeResult(url=url)))
        for url, hosts, markers in CONNECTIVITY_TARGETS
    ]

    online, reason = judge_connectivity(observations)
    for url, _hosts, _markers, result in observations:
        logger.info(
            f"Probe {url}: status={result.status}, final={result.final_url}"
        )

    logger.info(f"Connectivity verdict: online={online} ({reason})")

    _CONNECT_RESULT_CACHE["ts"] = time.monotonic()
    _CONNECT_RESULT_CACHE["value"] = online
    return online


def is_connect_by_google(force: bool = False) -> bool:
    """Compatibility wrapper: now uses site-based probe instead of Google 204."""
    return is_connect_by_sites(force=force)


def get_query_string_by_url(url: str = "http://1.1.1.1", skip_connectivity_check: bool = False) -> str:
    """获取认证URL和query string。

    Args:
        url: 探测URL，默认 http://1.1.1.1
        skip_connectivity_check: 是否跳过网络连通性检测（外部已检测过时设为True）

    Returns:
        格式: 'portal_url|query_string' 或者空字符串（已在线）
    """
    logger.debug("开始获取 query string...")

    # 只有外部没检测过才检测网络
    if not skip_connectivity_check:
        if is_connect_by_sites():
            logger.debug("网络已连接，无需认证")
            return ""

    # 尝试列表 - 优先使用传入的URL，失败后再尝试备选。
    #
    # 网关通常只劫持「未缓存的 http 明文请求」，而不同网络放行的地址不一样，
    # 单靠一个探测点很容易在换机器 / 换接入方式（有线↔无线）后抓不到跳转。
    # 所以这里多备几个对 captive portal 更敏感的地址：
    #   - neverssl.com 专门保证不会被升级成 https，最容易被劫持
    #   - example.com / msftconnecttest.com 是各家系统自带的连通性探测地址
    check_urls = [
        url,
        "http://www.msftconnecttest.com/connecttest.txt",
        "http://neverssl.com",
        "http://example.com",
        "http://www.shmtu.edu.cn",
    ]

    # 并发探测：待认证状态下每个地址都要等到超时，串行下来是几十秒，
    # 慢到让人以为程序卡死。probe_many 保证返回顺序与 check_urls 一致，
    # 所以「按顺序取第一个命中」的语义跟原来的串行写法完全相同。
    results = probe_many(check_urls)

    hit: tuple[str, str, str] | None = None   # (探测地址, 最终URL, 响应体)
    last: tuple[str, str, str] | None = None  # 最后一次有效响应，meta refresh 兜底用

    for item in results:
        logger.debug(
            f"URL: {item.url} -> Status: {item.status} -> Final: {item.final_url}"
        )

        if not item.final_url:
            continue

        last = (item.url, item.final_url, item.text)

        # 命中就立刻停，否则后面探测地址返回的正常页面会把结果覆盖掉
        if looks_like_portal(item.final_url, original_url=item.url):
            logger.debug(f"命中认证页: {item.url} -> {item.final_url}")
            hit = (item.url, item.final_url, item.text)
            break

    # 一个都没命中门户时，退化用最后一次有效响应去试 meta refresh
    if hit is None:
        if last is not None:
            logger.warning(
                "所有探测地址都没有跳到认证页，改用最后一次响应尝试 meta refresh 解析"
            )
        hit = last

    if hit is None:
        logger.error("所有探测URL均未返回有效响应")
        return ""

    _, final_url, res_string = hit

    # 检查 Final URL 是否直接即为认证页面
    def _encode_query_string_for_form(qs: str) -> str:
        qs = (qs or "").strip()
        if not qs:
            return ""
        return qs.replace("&", "%26").replace("=", "%3D")

    def _extract_query_string_from_url(any_url: str) -> str:
        any_url = (any_url or "").strip()
        q_index = any_url.find("?")
        if q_index <= 0:
            return ""
        return any_url[q_index + 1 :].strip()

    # 如果最终URL看起来像认证页面（新门户 ismu / 旧门户 hwifi / 通用劫持特征）
    if looks_like_portal(final_url):
         logger.info(f"直接定位到认证页面: {final_url}")
         qs = _extract_query_string_from_url(final_url)
         if qs:
            qs_encoded = _encode_query_string_for_form(qs)
            return f"{final_url}|{qs_encoded}"
         else:
             # 有可能没有queryString，但是是认证页
             return f"{final_url}|"

    logger.debug(f"响应内容前500字符: {res_string[:500]}")

    def _extract_meta_refresh_url(html: str) -> str:
        html = html or ""
        # 典型格式：<meta http-equiv="refresh" content="1; URL=https://...">
        meta_match = re.search(
            r"<meta[^>]*http-equiv\s*=\s*['\"]?refresh['\"]?[^>]*>",
            html,
            flags=re.IGNORECASE,
        )
        if not meta_match:
            return ""
        meta_tag = meta_match.group(0)
        # 从 content 里提取 url=
        content_match = re.search(
            r"content\s*=\s*(['\"])(.*?)\1",
            meta_tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
        content_value = content_match.group(2) if content_match else meta_tag
        url_match = re.search(r"url\s*=\s*([^\s'\"<>]+)", content_value, flags=re.IGNORECASE)
        return url_match.group(1).strip() if url_match else ""

    # 兼容：若是 302/301 之类重定向（某些网关会这样返回）
    try:
        redirect_location = ""
        # 这里不改 get_text_code 的签名，直接再探测一次 header
        r = probe_many([url], read_timeout=3, allow_redirects=False)[0]
        if 300 <= r.status < 400:
            redirect_location = r.final_url
        if redirect_location:
            logger.debug(f"检测到 HTTP 重定向 Location: {redirect_location}")
            qs = _extract_query_string_from_url(redirect_location)
            qs = _encode_query_string_for_form(qs)
            if qs:
                logger.info(f"成功获取 query string(redirect): {qs[:100]}...")
                return qs
    except Exception as e:
        logger.debug(f"重定向探测失败(可忽略): {e}")

    # 尝试从 META refresh 标签中提取 URL（大小写不敏感 / 支持单双引号）
    meta_url = _extract_meta_refresh_url(res_string)
    if meta_url:
        logger.debug(f"检测到 META refresh URL: {meta_url}")
        qs = _extract_query_string_from_url(meta_url)
        qs = _encode_query_string_for_form(qs)
        if qs:
            logger.info(f"成功获取认证URL(meta): {meta_url}")
            # 返回格式: 完整URL|编码后的query_string
            return f"{meta_url}|{qs}"

    # 尝试旧的解析方式（兼容旧格式）
    list_spilt = res_string.split("'")
    logger.debug(f"分割后的列表长度: {len(list_spilt)}")
    if len(list_spilt) > 1:
        login_page_url = list_spilt[1]
        logger.debug(f"登录页面URL: {login_page_url}")
        list_spilt_url = login_page_url.split("?")
        if len(list_spilt_url) > 1 and list_spilt_url[0].index("index.jsp") > 0:
            query_string = _encode_query_string_for_form(list_spilt_url[1])
            # github上其他学校的锐捷都是下面这样操作的，不清楚以哪个为准。
            # query_string = query_string.replace("&", "%2526").replace("=", "%253D")
            logger.info(f"成功获取 query string: {query_string}")
            return query_string
        else:
            logger.error("URL 格式不正确，无法解析 query string")

    logger.error("无法从响应中提取 query string")
    return ""


def get_query_string_by_baidu(url="http://www.baidu.com"):
    return get_query_string_by_url(url)


if __name__ == "__main__":
    print(get_query_string_by_url("http://www.shmtu.edu.cn"))
