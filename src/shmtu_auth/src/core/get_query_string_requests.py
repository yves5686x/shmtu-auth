from typing import Tuple

import re
from urllib.parse import urlparse

import requests

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 全局 Session 复用连接
_session: requests.Session | None = None

# 超时配置（秒）
CONNECT_TIMEOUT = 3  # 连接超时
READ_TIMEOUT = 5     # 读取超时

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


def get_text_code(url: str, timeout: float = READ_TIMEOUT) -> Tuple[str, int, str]:
    # noinspection PyBroadException
    try:
        response = _get_session().get(url, timeout=(CONNECT_TIMEOUT, timeout))
        # 自动识别编码，防止中文乱码
        response.encoding = response.apparent_encoding
        return response.text, response.status_code, response.url
    except Exception as e:
        logger.debug(f"Request Error: {e}")
        return "", 0, ""


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
)


def is_connect_by_sites() -> bool:
    """用百度 / B 站探测联网状态，任一真正拿到内容即认为已联网。"""
    logger.info("Starting connectivity probe...")

    for url, expected_hosts, body_markers in CONNECTIVITY_TARGETS:
        logger.info(f"Probing: {url}")
        text, status_code, final_url = get_text_code(url)
        logger.info(f"Result: status={status_code}, final_url={final_url}")

        # 只有 2xx 才算真的拿到了内容。
        # 4xx / 5xx 说明请求被中间设备挡了回来 —— 典型如代理返回的
        # 407（需代理认证）、502（坏网关）。这些以前都会被当成「已联网」。
        if not 200 <= status_code < 300:
            logger.info(f"Connectivity probe rejected: {url}, status={status_code}")
            continue

        # 被 captive portal 劫持时会跳到门户域名，这里必须排除
        if not _is_expected_host(final_url, expected_hosts):
            logger.info(f"Connectivity probe redirected: {url} -> {final_url}")
            continue

        # 内容校验：挡住代理 / 网关 / DNS 劫持返回的伪页面
        lowered = (text or "").lower()
        if body_markers and not any(m.lower() in lowered for m in body_markers):
            logger.info(
                f"Connectivity probe got unexpected body from {url} "
                f"(no marker of {body_markers}); treat as offline"
            )
            continue

        logger.info(f"Connectivity probe success: {url}")
        return True

    logger.info("All connectivity probes failed, network offline.")
    return False


def is_connect_by_google() -> bool:
    """Compatibility wrapper: now uses site-based probe instead of Google 204."""
    return is_connect_by_sites()


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

    hit: tuple[str, str, str] | None = None   # (探测地址, 最终URL, 响应体)
    last: tuple[str, str, str] | None = None  # 最后一次有效响应，meta refresh 兜底用

    for check_url in check_urls:
        logger.debug(f"尝试访问 {check_url} 获取认证跳转")
        res_string, res_code, final_url = get_text_code(check_url)
        logger.debug(f"URL: {check_url} -> Status: {res_code} -> Final: {final_url}")

        if not final_url:
            continue

        last = (check_url, final_url, res_string)

        # 命中就立刻停，否则后面探测地址返回的正常页面会把结果覆盖掉
        if looks_like_portal(final_url, original_url=check_url):
            logger.debug(f"命中认证页: {check_url} -> {final_url}")
            hit = (check_url, final_url, res_string)
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
        r = _get_session().get(url, allow_redirects=False, timeout=(CONNECT_TIMEOUT, 3))
        if 300 <= r.status_code < 400:
            redirect_location = r.headers.get("Location", "")
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
            logger.error(f"URL 格式不正确，无法解析 query string")

    logger.error("无法从响应中提取 query string")
    return ""


def get_query_string_by_baidu(url="http://www.baidu.com"):
    return get_query_string_by_url(url)


if __name__ == "__main__":
    print(get_query_string_by_url("http://www.shmtu.edu.cn"))
