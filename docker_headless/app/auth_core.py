import json
import logging
from urllib.parse import parse_qs, urlparse

import requests
import urllib3

from app.captcha_solver import (
    CaptchaProvider,
    get_available_solvers,
    normalize_code,
    solve_captcha,
)
from app.config import get_env_int, get_env_str
from app.eportal_protocol import (
    DEFAULT_PORTAL_BASE,
    EPortalClient,
    PortalPageInfo,
    encode_service_param,
    is_valid_code_error,
)
from app.portal_crypto import (
    DEFAULT_MAC,
    ENCRYPTED_PASSWORD_MIN_LENGTH,
    encrypt_password,
)

LOGGER = logging.getLogger("shmtu_auth_headless")

# 超时配置（秒）
CONNECT_TIMEOUT = 3  # 连接超时
READ_TIMEOUT = 5     # 读取超时


class ServiceType:
    # 有线「校园网」。门户在表单里把它再编码一层，所以这里保留一次编码后的字面值，
    # requests 用 data= 提交时会得到与浏览器完全一致的 %25E6%25A0... 字节。
    EDU = "%E6%A0%A1%E5%9B%AD%E7%BD%91"
    # 无线 i-SHMU，门户直接下发裸串，不做编码
    ISMU = "iSMU"


# 门户的两种接入类型：(显示名, service 提交值)
#
# 门户的 service 由服务端下发，本应二选一：有线「校园网」/ 无线 i-SHMU。但容器里的
# 网络类型判断经常不准（拿到的可能是宿主机或上一跳的类型），选错的代价只是一轮多余
# 的请求，所以这里直接两个都试，不再猜。详见 _portal_service_candidates()。
DEFAULT_PORTAL_SERVICES: tuple[tuple[str, str], ...] = (
    ("校园网(有线)", ServiceType.EDU),
    ("iSMU(无线)", ServiceType.ISMU),
)

# 验证码自动重试次数的默认值。
#
# 无头环境**没有弹窗兜底**，单次 OCR 失败就只能重来，而服务端每 GET 一次就换一张新
# 图，所以重试次数是提升无人值守成功率的主要手段。可用
# SHMTU_AUTH_CAPTCHA_MAX_RETRY 覆盖。
DEFAULT_CAPTCHA_MAX_RETRY = 6

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


def looks_like_portal(final_url, original_url=""):
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

    host = (urlparse(final_url).hostname or "").lower()
    lowered = final_url.lower()

    if host and any(
        host == marker or host.endswith("." + marker) for marker in PORTAL_HOST_MARKERS
    ):
        return True

    if any(marker in lowered for marker in PORTAL_PATH_MARKERS):
        return True

    if original_url:
        original_host = (urlparse(original_url).hostname or "").lower()
        if original_host and host and host != original_host and "?" in final_url:
            return True

    return False


class HeadlessNetAuth:
    def __init__(self) -> None:
        login_api = get_env_str("SHMTU_AUTH_LOGIN_URL", "")
        self.portal_base = login_api or DEFAULT_PORTAL_BASE
        self.login_api = f"{self.portal_base}InterFace.do?method="
        self.probe_url = get_env_str("SHMTU_AUTH_PROBE_URL", "http://1.1.1.1")
        self.info = ""
        self.user_index = ""
        self.is_login = False
        self.data: dict[str, str] = {}
        # 上一次成功登录时生效的 service 值。仅内存记忆，用于把优先尝试顺序提前，
        # 不会改变"两个都试"的语义（见 _portal_service_candidates）。
        self._last_ok_service = ""
        self.session = requests.Session()  # 复用连接
        # 校园网探测必须绕过系统代理。
        #
        # requests 默认 trust_env=True，会读 HTTP_PROXY / HTTPS_PROXY 等环境变量。
        # 一旦宿主机或容器设了代理，探测请求会直接发给代理并拿到真实页面，
        # 网关的劫持跳转根本不会发生，于是永远抓不到 queryString。
        self.session.trust_env = False
        self.headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": get_env_str(
                "SHMTU_AUTH_USER_AGENT",
                (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/145.0.0.0 Safari/537.36"
                ),
            ),
            "Accept-Encoding": "identify",
        }
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @staticmethod
    def _is_expected_host(final_url: str, expected_hosts: tuple[str, ...]) -> bool:
        host = urlparse((final_url or "").strip()).hostname or ""
        host = host.lower()
        return any(host == expected or host.endswith(f".{expected}") for expected in expected_hosts)

    def _get_text_code(self, url: str) -> tuple[str, int, str]:
        try:
            response = self.session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), verify=False)
            response.encoding = response.apparent_encoding
            return response.text, response.status_code, response.url
        except Exception as exc:
            LOGGER.debug("Probe request failed for %s: %s", url, exc)
            return "", 0, ""

    def is_connected(self) -> bool:
        # 特征串是必须的，用来识别「代理 / 网关返回的错误页」：那种响应状态码
        # 可能是 200（甚至 502），但内容根本不是目标网站的页面。只凭状态码判断，
        # 会把「上不了网」误判成「已联网」，进而认为无需认证、返回空 queryString。
        targets = [
            ("http://www.baidu.com", ("baidu.com",), ("baidu", "百度")),
            ("https://www.bilibili.com/", ("bilibili.com",), ("bilibili",)),
        ]
        for url, expected_hosts, body_markers in targets:
            text, status_code, final_url = self._get_text_code(url)
            LOGGER.debug("Connectivity probe %s -> status=%s final=%s", url, status_code, final_url)

            # 只有 2xx 才算真的拿到了内容。4xx / 5xx 说明请求被中间设备挡了回来，
            # 典型如代理返回的 407（需代理认证）、502（坏网关）。
            if not 200 <= status_code < 300:
                continue
            if not self._is_expected_host(final_url, expected_hosts):
                continue
            # 内容校验：挡住代理 / 网关 / DNS 劫持返回的伪页面
            lowered = (text or "").lower()
            if body_markers and not any(m.lower() in lowered for m in body_markers):
                LOGGER.debug("Unexpected body from %s; treat as offline", url)
                continue
            return True
        return False

    def _extract_query_string_from_url(self, any_url: str) -> str:
        any_url = (any_url or "").strip()
        q_index = any_url.find("?")
        if q_index <= 0:
            return ""
        return any_url[q_index + 1 :].strip()

    def _encode_query_string_for_form(self, query_string: str) -> str:
        query_string = (query_string or "").strip()
        if not query_string:
            return ""
        return query_string.replace("&", "%26").replace("=", "%3D")

    def _extract_meta_refresh_url(self, html: str) -> str:
        import re

        meta_match = re.search(
            r"<meta[^>]*http-equiv\s*=\s*['\"]?refresh['\"]?[^>]*>",
            html or "",
            flags=re.IGNORECASE,
        )
        if not meta_match:
            return ""

        meta_tag = meta_match.group(0)
        content_match = re.search(
            r"content\s*=\s*(['\"])(.*?)\1",
            meta_tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
        content_value = content_match.group(2) if content_match else meta_tag
        url_match = re.search(r"url\s*=\s*([^\s'\"<>]+)", content_value, flags=re.IGNORECASE)
        return url_match.group(1).strip() if url_match else ""

    def get_auth_result(self, skip_connectivity_check: bool = False) -> str:
        """获取认证URL和query string。

        Args:
            skip_connectivity_check: 是否跳过网络连通性检测（外部已检测过时设为True）

        Returns:
            格式: 'portal_url|query_string' 或者空字符串（已在线）
        """
        # 手动指定的优先级最高，配了就完全跳过自动探测。
        # 接受的两种写法：完整认证页 URL，或裸 queryString。
        manual = (get_env_str("SHMTU_AUTH_QUERY_STRING", "") or "").strip()
        if manual:
            LOGGER.info("Using manually configured SHMTU_AUTH_QUERY_STRING, skip probing")
            return manual

        if not skip_connectivity_check:
            if self.is_connected():
                return ""

        # 优先使用配置的探测URL，失败后再尝试备选。
        #
        # 网关通常只劫持「未缓存的 http 明文请求」，不同网络放行的地址不一样，
        # 单靠一个探测点很容易在换机器 / 换接入方式后抓不到跳转。
        #   - neverssl.com 专门保证不会被升级成 https，最容易被劫持
        #   - example.com / msftconnecttest.com 是各家系统自带的连通性探测地址
        check_urls = [
            self.probe_url,
            "http://www.msftconnecttest.com/connecttest.txt",
            "http://neverssl.com",
            "http://example.com",
            "http://www.shmtu.edu.cn",
        ]

        hit = None   # (探测地址, 最终URL, 响应体)
        last = None  # 最后一次有效响应，meta refresh 兜底用

        for check_url in check_urls:
            response_text, _, final_url = self._get_text_code(check_url)
            LOGGER.debug("Probe %s -> %s", check_url, final_url)
            if not final_url:
                continue

            last = (check_url, final_url, response_text)

            # 命中就立刻停，否则后面探测地址返回的正常页面会把结果覆盖掉
            if looks_like_portal(final_url, check_url):
                hit = (check_url, final_url, response_text)
                break

        if hit is None:
            if last is not None:
                LOGGER.warning("No probe hit the portal page, fallback to meta refresh parsing")
            hit = last

        if hit is None:
            return ""

        _, final_url, response_text = hit

        if looks_like_portal(final_url):
            query_string = self._extract_query_string_from_url(final_url)
            encoded = self._encode_query_string_for_form(query_string)
            return f"{final_url}|{encoded}"

        try:
            res = self.session.get(self.probe_url, allow_redirects=False, timeout=(CONNECT_TIMEOUT, 3), verify=False)
            if 300 <= res.status_code < 400:
                redirect_location = res.headers.get("Location", "")
                query_string = self._extract_query_string_from_url(redirect_location)
                encoded = self._encode_query_string_for_form(query_string)
                if encoded:
                    return f"{redirect_location}|{encoded}"
        except Exception as exc:
            LOGGER.debug("Redirect probe failed: %s", exc)

        meta_url = self._extract_meta_refresh_url(response_text)
        if meta_url:
            query_string = self._extract_query_string_from_url(meta_url)
            encoded = self._encode_query_string_for_form(query_string)
            if encoded:
                return f"{meta_url}|{encoded}"

        return ""

    @staticmethod
    def _split_auth_result(auth_result: str) -> tuple[str, str]:
        auth_result = (auth_result or "").strip()
        if not auth_result:
            return "", ""

        if "|" in auth_result:
            portal_url, query_string = auth_result.split("|", 1)
            return portal_url.strip(), query_string.strip()

        # 不限定门户域名：ismu（新）/ hwifi（旧）以及任何未来换域名的门户都要能拆。
        if auth_result.lower().startswith(("http://", "https://")) and "?" in auth_result:
            parsed = urlparse(auth_result)
            encoded_query = parsed.query.replace("&", "%26").replace("=", "%3D")
            return auth_result, encoded_query

        return "", auth_result

    def _confirm_login_success(self, stage: str) -> tuple[bool, str] | None:
        if self.is_connected():
            LOGGER.warning("%s response looked failed, but network is online now", stage)
            self.is_login = True
            return True, f"Login Success ({stage} Confirmed)"
        return None

    # -------------------------------------------------------- eportal 主流程

    @staticmethod
    def _extract_mac(query_string: str) -> str:
        """从 queryString 中取 mac，门户 JS 取不到时会退化成 DEFAULT_MAC。"""
        try:
            values = parse_qs((query_string or "").lstrip("?"))
            mac = (values.get("mac") or [""])[0].strip()
        except Exception:
            mac = ""
        return mac or DEFAULT_MAC

    def _obtain_valid_code(self, image: bytes, provider: CaptchaProvider | None = None) -> str:
        """先 OCR，识别不出来再交给上层回调兜底。

        无头环境通常没有 provider，此时直接返回空串，由调用方按失败处理并在下个
        轮询周期重试（验证码每次都是新图，重试本身是廉价的）。
        """
        code = solve_captcha(image)
        if code:
            return code

        if provider is None:
            return ""

        try:
            manual = normalize_code(provider(image))
        except Exception as exc:
            LOGGER.warning("Captcha provider failed: %s", exc)
            return ""

        if manual:
            LOGGER.info("Captcha provided by user input")
            return manual
        return ""

    def _captcha_capable(self, provider: CaptchaProvider | None = None) -> bool:
        """当前环境有没有办法拿到验证码：OCR 后端可用，或上层提供了人工输入通道。

        无头容器里两者都没有的话，就没必要往门户发登录请求了 —— 直接报「装 OCR」比
        跑满重试次数后给一句含糊的失败有用得多。
        """
        if provider is not None:
            return True
        return bool(get_available_solvers())

    def _portal_service_candidates(self) -> list[tuple[str, str]]:
        """返回本次登录要**依次尝试**的 ``(显示名, service 提交值)`` 列表。

        注意这里刻意不做「按当前网络类型二选一」的判断：门户的 ``service`` 虽然由
        服务端下发，但容器 / Docker / 网关代拨场景下网络类型经常判断错，拿到的是宿主
        机或上一跳的类型。两种都试一遍的代价很小（每轮约一次取图 + 一次 POST），却能
        让有线、无线、以及判断错误的场景全部自愈。

        顺序：

        1. 配置项 ``SHMTU_AUTH_PORTAL_SERVICE`` 非空 → **只试它**（显式定死，
           不再兜底，方便排查）
        2. 否则 → ``校园网(有线)`` 与 ``iSMU(无线)`` 依次尝试
        3. 若本进程上一次登录成功过，把当时生效的那个提到最前面（守护进程从第二轮起
           就能一次命中；这只是内存记忆、不落盘，两个候选仍然都会被尝试）

        :return: ``[(显示名, 可直接放进 payload 的 service 值), ...]``，至少一项
        """
        configured = get_env_str("SHMTU_AUTH_PORTAL_SERVICE", "")
        if configured:
            resolved = encode_service_param(configured)
            LOGGER.info("Portal service pinned by config: %s -> %s", configured, resolved)
            return [(configured, resolved)]

        candidates = [(name, encode_service_param(value)) for name, value in DEFAULT_PORTAL_SERVICES]

        remembered = getattr(self, "_last_ok_service", "")
        if remembered:
            hit = next((item for item in candidates if item[1] == remembered), None)
            if hit is not None:
                candidates.remove(hit)
                candidates.insert(0, hit)
                LOGGER.info("Portal service candidates: %s first (worked last time)", hit[0])

        LOGGER.info(
            "Portal service candidates (try in order): %s",
            ", ".join(name for name, _ in candidates),
        )
        return candidates

    def _login_portal_once(
        self,
        client: EPortalClient,
        page_info: PortalPageInfo,
        service: str,
        user: str,
        submit_pwd: str,
        encrypt_flag: bool,
        query_string: str,
        captcha_provider: CaptchaProvider | None = None,
    ) -> tuple[bool, str]:
        """针对单个 ``service`` 跑完整的「取验证码 → 识别 → 提交」重试循环。"""
        max_attempts = 1
        if page_info.need_valid_code:
            configured = get_env_int("SHMTU_AUTH_CAPTCHA_MAX_RETRY", DEFAULT_CAPTCHA_MAX_RETRY)
            max_attempts = max(1, configured or DEFAULT_CAPTCHA_MAX_RETRY)

        last_message = ""
        unrecognized = 0

        for attempt in range(1, max_attempts + 1):
            valid_code = ""
            if page_info.need_valid_code:
                # 每次 fetch 服务端都会换一张新图，所以重试是真的换了个验证码
                image = client.fetch_valid_code(page_info.valid_code_url)
                if not image:
                    last_message = "验证码图片下载失败"
                    LOGGER.warning("%s（第 %s/%s 次）", last_message, attempt, max_attempts)
                    continue

                valid_code = self._obtain_valid_code(image, captcha_provider)
                if not valid_code:
                    # 无头环境识别不出来是常态，不能当成致命错误直接放弃，
                    # 换一张图继续试才是正解
                    unrecognized += 1
                    last_message = "验证码识别失败"
                    LOGGER.warning(
                        "验证码识别失败（第 %s/%s 次），换一张重试", attempt, max_attempts
                    )
                    continue

                LOGGER.info(
                    "Login attempt %s/%s with validcode, service=%s", attempt, max_attempts, service
                )

            payload = {
                "userId": user,
                "password": submit_pwd,
                "service": service,
                "queryString": query_string,
                "operatorPwd": "",
                "operatorUserId": "",
                "validcode": valid_code,
                "passwordEncrypt": str(encrypt_flag).lower(),
            }
            self.data = payload.copy()

            result = client.submit_login(payload)
            self.user_index = result.get("userIndex") or ""
            self.info = result.get("message") or ""
            LOGGER.info("Portal login response: %s", result)

            if result.get("result") == "success":
                self.is_login = True
                return True, "Login Success (Portal)"

            confirmed = self._confirm_login_success("Portal")
            if confirmed is not None:
                return confirmed

            last_message = self.info or "Portal login failed"

            # 非验证码类错误（账号密码错、服务不对、被风控……）：重试同样的图没意义，
            # 交给上层决定是否换一种接入类型
            if not is_valid_code_error(last_message):
                return False, last_message

            # 验证码被服务端拒绝：换新图重试
            LOGGER.warning("Valid code rejected: %s", last_message)
            refreshed = str(result.get("validCodeUrl") or "").strip()
            if refreshed:
                page_info.valid_code_url = refreshed
            else:
                page_info = client.query_page_info(query_string)
                if not page_info.need_valid_code:
                    return False, last_message

        if unrecognized == max_attempts:
            return False, f"验证码连续 {max_attempts} 次未能识别，请检查 OCR 后端是否可用"
        return False, f"验证码连续 {max_attempts} 次未通过: {last_message}"

    def _login_eportal(
        self,
        user: str,
        password: str,
        query_string: str,
        password_encrypt: bool = False,
        captcha_provider: CaptchaProvider | None = None,
    ) -> tuple[bool, str]:
        """门户主流程：index.jsp → pageInfo → validcode → login（带验证码与 RSA 加密）。

        会对 :meth:`_portal_service_candidates` 返回的每种接入类型各跑一遍完整流程
        （见 :meth:`_login_portal_once`），因此有线 / 无线都能自动命中，不依赖运行
        环境上报的网络类型。
        """
        query_string = (query_string or "").strip()
        if not query_string:
            return False, "Query string is invalid"

        client = EPortalClient(self.session, self.portal_base)
        client.open_entry(query_string)

        page_info = client.query_page_info(query_string)
        if not page_info.raw:
            return False, "pageInfo request failed"

        if page_info.need_valid_code and not self._captcha_capable(captcha_provider):
            LOGGER.error("Portal requires a captcha but no OCR backend or manual input is available")
            return False, (
                "门户要求图形验证码，但当前环境既没有可用的 OCR 后端，也没有人工输入通道；"
                "请安装 OCR 依赖（pip install -r requirements-ocr.txt）"
            )

        mac = self._extract_mac(query_string)
        if mac == DEFAULT_MAC:
            # 门户 JS 在拿不到 mac 时也用这个值兜底，所以行为一致；但密码会用这个错误的
            # mac 参与加密，服务端解密必然失败，最终只表现为含糊的「认证失败」。
            LOGGER.warning(
                "queryString 中没有 mac 参数，已退化成门户默认值 %s；"
                "密码将用该值参与加密，门户多半解不开。"
                "请检查 queryString 是否完整，容器需使用 host 网络",
                DEFAULT_MAC,
            )

        submit_pwd = password
        encrypt_flag = bool(password_encrypt)
        if not encrypt_flag and page_info.password_encrypt and len(password) < ENCRYPTED_PASSWORD_MIN_LENGTH:
            try:
                submit_pwd = encrypt_password(
                    password,
                    mac,
                    page_info.public_key_modulus,
                    page_info.public_key_exponent,
                )
                encrypt_flag = True
            except Exception as exc:
                LOGGER.exception("Password encryption failed: %s", exc)
                return False, f"Password encryption failed: {exc}"

        candidates = self._portal_service_candidates()
        last_message = ""

        for index, (display_name, service) in enumerate(candidates):
            LOGGER.info(
                "Portal login trying %s (%s) [%s/%s]",
                display_name,
                service,
                index + 1,
                len(candidates),
            )

            ok, message = self._login_portal_once(
                client,
                page_info,
                service,
                user,
                submit_pwd,
                encrypt_flag,
                query_string,
                captcha_provider,
            )
            if ok:
                # 记住这次生效的类型，供本进程后续登录优先尝试
                self._last_ok_service = service
                return True, message

            last_message = message

            # 验证码类问题换接入类型也没用，直接返回，别白白多跑一轮
            if is_valid_code_error(message) or "验证码" in message:
                return False, message

            if index + 1 < len(candidates):
                LOGGER.warning("%s 登录失败（%s），换下一种接入类型重试", display_name, message)

        if len(candidates) > 1:
            return False, f"两种接入类型均登录失败（{last_message}）"
        return False, last_message

    # ------------------------------------------------------------- H3C 兜底

    def _login_h3c(self, user: str, password: str, portal_url: str = "") -> tuple[bool, str]:
        try:
            entry_url = portal_url.strip() or self.probe_url
            session = requests.Session()

            browser_headers = {
                "User-Agent": self.headers["User-Agent"],
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            response = session.get(
                entry_url,
                headers=browser_headers,
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=True,
            )

            if "authSuccess" in response.url or "success" in response.url.lower():
                self.is_login = True
                return True, "Login Success (H3C)"

            if "auth.html" not in response.url and "hwifi.shmtu.edu.cn" not in response.url:
                return False, f"Unknown H3C flow: {response.url}"

            parsed = urlparse(response.url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            query = parse_qs(parsed.query)

            post_headers = {
                **browser_headers,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": response.url,
                "Origin": base_url,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            }

            xsrf_token = session.cookies.get("XSRF-TOKEN")
            if xsrf_token:
                post_headers["X-XSRF-TOKEN"] = xsrf_token

            payload = {
                "userName": user,
                "userPass": password,
                "pushPageId": query.get("pushPageId", [""])[0],
                "esn": "",
                "apmac": query.get("apmac", [""])[0],
                "armac": "",
                "authType": query.get("authType", ["1"])[0],
                "ssid": query.get("ssid", [""])[0],
                "uaddress": query.get("uaddress", [""])[0],
                "umac": query.get("umac", [""])[0],
                "accessMac": "",
                "businessType": "",
                "acip": "",
                "agreed": "1",
                "registerCode": "",
                "questions": "",
                "dynamicValidCode": "",
                "dynamicRSAToken": "",
                "validCode": "",
            }

            result_response = session.post(
                f"{base_url}/portalauth/login",
                data=payload,
                headers=post_headers,
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=False,
            )

            if result_response.status_code == 200:
                try:
                    result = result_response.json()
                    if result.get("success") or result.get("result") == "success":
                        self.is_login = True
                        return True, "Login Success (H3C)"
                    confirmed = self._confirm_login_success("H3C")
                    if confirmed is not None:
                        return confirmed
                    return False, result.get("msg") or result.get("message") or "Unknown H3C error"
                except Exception:
                    confirmed = self._confirm_login_success("H3C")
                    if confirmed is not None:
                        return confirmed
                    return False, "H3C response parse failed"

            if 300 <= result_response.status_code < 400:
                self.is_login = True
                return True, "Login Success (H3C Redirect)"

            confirmed = self._confirm_login_success("H3C")
            if confirmed is not None:
                return confirmed
            return False, f"H3C login failed status={result_response.status_code}"
        except Exception as exc:
            LOGGER.exception("H3C login flow failed: %s", exc)
            confirmed = self._confirm_login_success("H3C")
            if confirmed is not None:
                return confirmed
            return False, "H3C network error"

    def login(
        self,
        user: str,
        password: str,
        password_encrypt: bool = False,
        skip_network_check: bool = False,
        captcha_provider: CaptchaProvider | None = None,
    ) -> tuple[bool, str]:
        """登录校园网。

        门户现已要求图形验证码，完整时序为：
        index.jsp（建会话）→ pageInfo（取验证码地址 / RSA 公钥）
        → validcode（取图）→ 识别验证码 → login（带 validcode 与密文密码）

        Args:
            user: 用户名
            password: 密码
            password_encrypt: 密码是否已加密
            skip_network_check: 是否跳过网络检测（外部已检测过时设为True）
            captcha_provider: 验证码兜底回调，OCR 失败时调用，签名 (image: bytes) -> str | None

        Returns:
            (是否成功, 消息)
        """
        if not skip_network_check:
            if self.is_connected():
                self.is_login = True
                return True, "Already online"

        if not user or not password:
            return False, "Username or password is empty"

        auth_result = self.get_auth_result(skip_connectivity_check=skip_network_check).strip()
        portal_url, query_string = self._split_auth_result(auth_result)

        # 1) 门户主流程（含验证码 + 密码加密）
        portal_ok, portal_msg = self._login_eportal(
            user, password, query_string, password_encrypt, captcha_provider
        )
        if portal_ok:
            return True, portal_msg

        LOGGER.warning("Portal login failed: %s", portal_msg)

        # 验证码问题换门户也没用，直接返回，避免无谓的等待
        if is_valid_code_error(portal_msg) or "验证码" in portal_msg:
            return False, portal_msg

        # 2) 兜底：另一套 H3C portalauth 门户
        if not portal_url:
            return False, portal_msg

        LOGGER.warning("Switch to H3C portalauth fallback...")
        h3c_ok, h3c_msg = self._login_h3c(user, password, portal_url)
        if h3c_ok:
            return True, h3c_msg

        return False, f"Portal failed: {portal_msg}; H3C failed: {h3c_msg}"
