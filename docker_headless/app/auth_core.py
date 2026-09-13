import json
import logging
from urllib.parse import parse_qs, urlparse

import requests
import urllib3

from app.captcha_solver import CaptchaProvider, normalize_code, solve_captcha
from app.config import get_env_int, get_env_str
from app.eportal_protocol import (
    DEFAULT_PORTAL_BASE,
    PLACEHOLDER_SERVICE_PREFIX,
    EPortalClient,
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
        self.session = requests.Session()  # 复用连接
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
        targets = [
            ("http://www.baidu.com", ("baidu.com",)),
            ("https://www.bilibili.com/", ("bilibili.com",)),
        ]
        for url, expected_hosts in targets:
            _, status_code, final_url = self._get_text_code(url)
            if status_code <= 0:
                continue
            if not self._is_expected_host(final_url, expected_hosts):
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
        if not skip_connectivity_check:
            if self.is_connected():
                return ""

        # 优先使用配置的探测URL，失败后再尝试备选
        primary_url = self.probe_url
        fallback_urls = [
            "http://www.msftconnecttest.com/connecttest.txt",
            "http://www.shmtu.edu.cn",
        ]

        final_url = ""
        response_text = ""

        # 先尝试主URL
        response_text, _, final_url = self._get_text_code(primary_url)
        if "hwifi.shmtu.edu.cn" not in final_url and "auth.html" not in final_url and "portalpage" not in final_url:
            # 主URL没找到，尝试备选URL
            for check_url in fallback_urls:
                response_text, _, final_url = self._get_text_code(check_url)
                if "hwifi.shmtu.edu.cn" in final_url or "auth.html" in final_url or "portalpage" in final_url:
                    break

        if not final_url:
            return ""

        if "auth.html" in final_url or "portalpage" in final_url or "hwifi" in final_url:
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

        if "hwifi" in auth_result and "?" in auth_result:
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

    def _resolve_portal_service(self, client: EPortalClient, query_string: str, user: str) -> str:
        """确定登录用的 ``service`` 参数（返回可直接放进 payload 的值）。

        门户页面上这是一个下拉框，值由服务端下发，所以不要写死。取值优先级：

        1. 配置项 ``SHMTU_AUTH_PORTAL_SERVICE``（显式覆盖）
        2. 账号绑定服务 —— ``userV2.do?method=getServices``，即用户输入学号后门户
           自动选中下拉框的那次查询。**会随网络环境返回 ``校园网`` 或 ``iSMU``**，
           是最可靠的来源
        3. 门户下发的下拉项（唯一项 / 首项）
        4. 兜底 :attr:`ServiceType.EDU`

        注意 ``service`` 的编码：门户 JS 对隐藏域做了两次 ``encodeURIComponent``，
        而 requests 的 ``data=`` 还会再编码一次，因此这里统一只编码一次，
        见 :func:`encode_service_param`。
        """
        configured = get_env_str("SHMTU_AUTH_PORTAL_SERVICE", "")
        if configured:
            LOGGER.info("Portal service from config: %s", configured)
            return encode_service_param(configured)

        account_service = client.query_account_service(query_string, user)
        if account_service:
            resolved = encode_service_param(account_service)
            LOGGER.info(
                "Portal service auto-detected (account bound): %s -> %s", account_service, resolved
            )
            return resolved

        candidates = [
            item["value"]
            for item in client.query_services(query_string)
            if item.get("value") and not item["value"].startswith(PLACEHOLDER_SERVICE_PREFIX)
        ]
        if candidates:
            scope = "single option" if len(candidates) == 1 else "first option"
            LOGGER.info("Portal service auto-detected (%s): %s", scope, candidates[0])
            return encode_service_param(candidates[0])

        LOGGER.warning(
            "Portal service not resolved (portal offered no real option), "
            "fallback to EDU: %s. "
            "If login keeps failing, set SHMTU_AUTH_PORTAL_SERVICE explicitly.",
            ServiceType.EDU,
        )
        return ServiceType.EDU

    def _login_eportal(
        self,
        user: str,
        password: str,
        query_string: str,
        password_encrypt: bool = False,
        captcha_provider: CaptchaProvider | None = None,
    ) -> tuple[bool, str]:
        """门户主流程：index.jsp → pageInfo → validcode → login（带验证码与 RSA 加密）。"""
        query_string = (query_string or "").strip()
        if not query_string:
            return False, "Query string is invalid"

        client = EPortalClient(self.session, self.portal_base)
        client.open_entry(query_string)

        page_info = client.query_page_info(query_string)
        if not page_info.raw:
            return False, "pageInfo request failed"

        service = self._resolve_portal_service(client, query_string, user)
        mac = self._extract_mac(query_string)

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

        # 验证码是一次性的：命中验证码类错误就用新的 validCodeUrl 重取重试
        max_attempts = 1
        if page_info.need_valid_code:
            max_attempts = max(1, get_env_int("SHMTU_AUTH_CAPTCHA_MAX_RETRY", 3) or 3)
        last_message = ""

        for attempt in range(1, max_attempts + 1):
            valid_code = ""
            if page_info.need_valid_code:
                image = client.fetch_valid_code(page_info.valid_code_url)
                if not image:
                    return False, "验证码图片下载失败"

                valid_code = self._obtain_valid_code(image, captcha_provider)
                if not valid_code:
                    return False, "验证码识别失败，需要人工输入"

                LOGGER.info("Login attempt %s/%s with validcode", attempt, max_attempts)

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

            if not is_valid_code_error(last_message):
                return False, last_message

            # 验证码问题：换新图重试
            LOGGER.warning("Valid code rejected: %s", last_message)
            refreshed = str(result.get("validCodeUrl") or "").strip()
            if refreshed:
                page_info.valid_code_url = refreshed
            else:
                page_info = client.query_page_info(query_string)
                if not page_info.need_valid_code:
                    return False, last_message

        return False, f"验证码连续 {max_attempts} 次未通过: {last_message}"

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
