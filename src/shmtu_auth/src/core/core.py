import json
from urllib.parse import parse_qs, urlparse

import requests
from urllib3 import __version__ as urllib3_version

from shmtu_auth.src.core.captcha_solver import CaptchaProvider, normalize_code, solve_captcha
from shmtu_auth.src.core.core_exp import check_is_connected_retry, get_query_string
from shmtu_auth.src.core.eportal_protocol import (
    DEFAULT_PORTAL_BASE,
    EPortalClient,
    PLACEHOLDER_SERVICE_PREFIX,
    encode_service_param,
    is_valid_code_error,
)
from shmtu_auth.src.core.portal_crypto import (
    DEFAULT_MAC,
    ENCRYPTED_PASSWORD_MIN_LENGTH,
    encrypt_password,
)
from shmtu_auth.src.core.shmtu_auth_const_value import ServiceType
from shmtu_auth.src.utils.env import get_env_int, get_env_str
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

if urllib3_version.startswith("2."):
    logger.warning(
        "You are using urllib3 version 2.x, which is not fully compatible with this module. "
        "Please use urllib3 version 1.x for better compatibility.",
        stacklevel=2,
    )
    logger.warning(
        'Please run: pip install "urllib3<2"',
        stacklevel=2,
    )

# 超时配置（秒）
CONNECT_TIMEOUT = 3  # 连接超时
READ_TIMEOUT = 5     # 读取超时


class ShmtuNetAuthCore:
    userIndex: str
    info: str
    data: dict
    url: str
    header: dict
    isLogin: bool
    allData: dict
    session: requests.Session

    def __init__(self):
        self.userIndex = ""
        self.info = ""
        self.data = {}
        self.portal_base: str = DEFAULT_PORTAL_BASE
        self.url: str = f"{self.portal_base}InterFace.do?method="
        self.header: dict = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.2.1 Safari/605.1.15",
            "Accept-Encoding": "identify",
        }
        self.isLogin: bool = False
        self.allData: dict = {}
        self.session = requests.Session()  # 复用连接

        env_ua = get_env_str("SHMTU_AUTH_USER_AGENT", "")
        if env_ua != "":
            self.header["User-Agent"] = env_ua
        logger.info("ShmtuNetAuthCore initialization complete!")

    def test_net(self) -> bool:
        """
        测试网络是否认证
        :return: 是否已经认证
        """
        self.isLogin = check_is_connected_retry(retry_times=3, wait_time=5)
        if not self.isLogin:
            logger.info(f"Network Auth Status: {self.isLogin}")
        return self.isLogin

    def test_net_by_ismu(self) -> bool:
        """
        测试网络是否认证(通过ismu的认证界面)
        会有一个问题，就是他系统有bug，可能不跳转！
        :return: 是否已经认证
        """
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # noinspection PyBroadException
        try:
            res = requests.get("http://ismu.shmtu.edu.cn/", headers=self.header, verify=False)
            # print(res.geturl())
            if res.url.find("success.jsp") > 0:
                self.isLogin = True
            else:
                self.isLogin = False
        except Exception:
            self.isLogin = False
        return self.isLogin

    @staticmethod
    def _split_auth_result(auth_result: str) -> tuple[str, str]:
        """Split auth probe result into portal_url and query_string."""
        auth_result = (auth_result or "").strip()
        if not auth_result:
            return "", ""

        if "|" in auth_result:
            portal_url, query_string = auth_result.split("|", 1)
            return portal_url.strip(), query_string.strip()

        # Fallback for full URL returned without "|" separator.
        if "hwifi" in auth_result and "?" in auth_result:
            parsed = urlparse(auth_result)
            encoded_query = parsed.query.replace("&", "%26").replace("=", "%3D")
            return auth_result, encoded_query

        # Legacy format (query string only).
        return "", auth_result

    def _confirm_login_success(self, stage: str) -> tuple[bool, str] | None:
        """Double check actual connectivity when portal responses are ambiguous."""
        try:
            if check_is_connected_retry(retry_times=1, wait_time=0):
                logger.warning(
                    f"{stage} response looked failed, but connectivity is online now; treat as success."
                )
                self.isLogin = True
                return True, f"Login Success ({stage} Confirmed)"
        except Exception as e:
            logger.debug(f"{stage} connectivity confirmation skipped: {e}")
        return None

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
        """先 OCR，识别不出来再交给上层（GUI 弹窗）兜底。"""
        code = solve_captcha(image)
        if code:
            return code

        if provider is None:
            return ""

        try:
            manual = normalize_code(provider(image))
        except Exception as e:
            logger.warning(f"Captcha provider failed: {e}")
            return ""

        if manual:
            logger.info("Captcha provided by user input")
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
            logger.info(f"Portal service from config: {configured}")
            return encode_service_param(configured)

        account_service = client.query_account_service(query_string, user)
        if account_service:
            resolved = encode_service_param(account_service)
            logger.info(f"Portal service auto-detected (account bound): {account_service} -> {resolved}")
            return resolved

        candidates = [
            item["value"]
            for item in client.query_services(query_string)
            if item.get("value") and not item["value"].startswith(PLACEHOLDER_SERVICE_PREFIX)
        ]
        if candidates:
            scope = "single option" if len(candidates) == 1 else "first option"
            logger.info(f"Portal service auto-detected ({scope}): {candidates[0]}")
            return encode_service_param(candidates[0])

        logger.warning(
            "Portal service not resolved (portal offered no real option), "
            f"fallback to EDU: {ServiceType.EDU}. "
            "If login keeps failing, set SHMTU_AUTH_PORTAL_SERVICE explicitly."
        )
        return ServiceType.EDU

    def _login_eportal(
        self,
        user: str,
        pwd: str,
        query_string: str,
        password_encrypt: bool = False,
        captcha_provider: CaptchaProvider | None = None,
    ) -> tuple[bool, str]:
        """门户主流程：index.jsp → pageInfo → validcode → login（带验证码与 RSA 加密）。"""
        query_string = (query_string or "").strip()
        if len(query_string) == 0:
            return False, "Query String is Invalid!"

        client = EPortalClient(self.session, self.portal_base)
        client.open_entry(query_string)

        page_info = client.query_page_info(query_string)
        if not page_info.raw:
            return False, "pageInfo 请求失败，无法获取门户配置"

        service = self._resolve_portal_service(client, query_string, user)
        mac = self._extract_mac(query_string)

        submit_pwd = pwd
        encrypt_flag = bool(password_encrypt)
        if not encrypt_flag and page_info.password_encrypt and len(pwd) < ENCRYPTED_PASSWORD_MIN_LENGTH:
            try:
                submit_pwd = encrypt_password(
                    pwd,
                    mac,
                    page_info.public_key_modulus,
                    page_info.public_key_exponent,
                )
                encrypt_flag = True
            except Exception as e:
                logger.exception(f"Password encryption failed: {e}")
                return False, f"密码加密失败: {e}"

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

                logger.info(f"Login attempt {attempt}/{max_attempts} with validcode")

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
            self.userIndex = result.get("userIndex") or ""
            self.info = result.get("message") or ""
            logger.info(f"Portal login response: {result}")

            if result.get("result") == "success":
                self.isLogin = True
                return True, "Login Success (Portal)"

            confirmed = self._confirm_login_success("Portal")
            if confirmed is not None:
                return confirmed

            last_message = self.info or "Portal login failed"

            if not is_valid_code_error(last_message):
                return False, last_message

            # 验证码问题：换新图重试
            logger.warning(f"Valid code rejected: {last_message}")
            refreshed = str(result.get("validCodeUrl") or "").strip()
            if refreshed:
                page_info.valid_code_url = refreshed
            else:
                page_info = client.query_page_info(query_string)
                if not page_info.need_valid_code:
                    return False, last_message

        return False, f"验证码连续 {max_attempts} 次未通过: {last_message}"

    def _login_h3c(self, user: str, pwd: str, portal_url: str = "") -> tuple[bool, str]:
        """New H3C portal login flow."""
        try:
            entry_url = portal_url.strip()
            if not entry_url:
                entry_url = "http://1.1.1.1"

            logger.info(f"Start H3C fallback login flow, entry: {entry_url}")

            session = requests.Session()
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            }
            res = session.get(
                entry_url,
                headers=headers,
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=True,
            )

            if "authSuccess" in res.url or "success" in res.url.lower():
                logger.info(f"Already authenticated in H3C flow: {res.url}")
                return True, "Login Success (H3C)"

            if "auth.html" not in res.url and "hwifi.shmtu.edu.cn" not in res.url:
                logger.error(f"Unknown H3C auth flow URL: {res.url}")
                return False, f"Unknown H3C flow: {res.url}"

            parsed = urlparse(res.url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            qs_params = parse_qs(parsed.query)

            post_headers = headers.copy()
            post_headers.update(
                {
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": res.url,
                    "Origin": base_url,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                }
            )

            xsrf_token = session.cookies.get("XSRF-TOKEN")
            if xsrf_token:
                post_headers["X-XSRF-TOKEN"] = xsrf_token

            final_auth_data = {
                "userName": user,
                "userPass": pwd,
                "pushPageId": qs_params.get("pushPageId", [""])[0],
                "esn": "",
                "apmac": qs_params.get("apmac", [""])[0],
                "armac": "",
                "authType": qs_params.get("authType", ["1"])[0],
                "ssid": qs_params.get("ssid", [""])[0],
                "uaddress": qs_params.get("uaddress", [""])[0],
                "umac": qs_params.get("umac", [""])[0],
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

            submit_url = f"{base_url}/portalauth/login"
            res2 = session.post(
                submit_url,
                data=final_auth_data,
                headers=post_headers,
                verify=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=False,
            )

            if res2.status_code == 200:
                try:
                    result = res2.json()
                    logger.info(f"H3C Login Response: {result}")
                    if result.get("success") or result.get("result") == "success":
                        return True, "Login Success (H3C)"
                    confirmed = self._confirm_login_success("H3C")
                    if confirmed is not None:
                        return confirmed
                    error_msg = result.get("msg") or result.get("message") or "Unknown H3C error"
                    return False, error_msg
                except Exception as e:
                    logger.error(f"Failed to parse H3C response: {e}. Body: {res2.text[:200]}")
                    confirmed = self._confirm_login_success("H3C")
                    if confirmed is not None:
                        return confirmed
                    return False, "H3C response parse failed"

            if 300 <= res2.status_code < 400:
                return True, "Login Success (H3C Redirect)"

            confirmed = self._confirm_login_success("H3C")
            if confirmed is not None:
                return confirmed
            return False, f"H3C login failed status={res2.status_code}"
        except Exception as e:
            logger.exception(f"H3C Login Network Error: {e}")
            confirmed = self._confirm_login_success("H3C")
            if confirmed is not None:
                return confirmed
            return False, "H3C Network Error!"

    def login(
        self,
        user,
        pwd,
        password_encrypt=False,
        skip_network_check=False,
        captcha_provider: CaptchaProvider | None = None,
    ) -> (bool, str):
        """
        输入参数登入校园网，自动检测当前网络是否认证。

        门户现已要求图形验证码，完整时序为：
        index.jsp（建会话）→ pageInfo（取验证码地址 / RSA 公钥）
        → validcode（取图）→ 识别验证码 → login（带 validcode 与密文密码）

        :param user:登入id
        :param pwd:登入密码
        :param password_encrypt: 密码是否为密文
        :param skip_network_check: 是否跳过登录前联网探测
        :param captcha_provider: 验证码兜底回调，OCR 失败时调用，签名 (image: bytes) -> str | None
        :return:元组第一项：是否认证状态；第二项：详细信息
        """
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        if not skip_network_check:
            # 执行登录前再进行一次状态检测
            self.test_net()
            if self.isLogin:
                logger.info("Already Login!")
                return True, "Already Login"

        if user == "" or pwd == "":
            return False, "用户名或密码为空"

        # 如果外部已检测过网络状态，则跳过 get_query_string 内部的网络检测
        auth_result = get_query_string(skip_connectivity_check=skip_network_check).strip()
        portal_url, current_query_string = self._split_auth_result(auth_result)

        # 1) 门户主流程（含验证码 + 密码加密）
        portal_ok, portal_msg = self._login_eportal(
            user, pwd, current_query_string, password_encrypt, captcha_provider
        )
        if portal_ok:
            return True, portal_msg

        logger.warning(f"Portal login failed: {portal_msg}")

        # 验证码问题换门户也没用，直接返回，避免无谓的等待
        if is_valid_code_error(portal_msg) or "验证码" in portal_msg:
            return False, portal_msg

        # 2) 兜底：另一套 H3C portalauth 门户
        if not portal_url:
            return False, portal_msg

        logger.warning("Switch to H3C portalauth fallback...")
        h3c_ok, h3c_msg = self._login_h3c(user, pwd, portal_url)
        if h3c_ok:
            return True, h3c_msg

        return False, f"Portal failed: {portal_msg}; H3C failed: {h3c_msg}"

    def get_all_data(self) -> dict:
        """
        获取当前认证账号全部信息
        #！！！注意！！！#此操作会获得账号alldata['userId']姓名alldata['userName']以及密码alldata['password']
        :return:全部数据的字典格式
        """
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        res = requests.get(self.url + "getOnlineUserInfo", headers=self.header, verify=False)
        try:
            self.allData = json.loads(res.text)
            logger.info(f"Get All Data: {self.allData}")
        except json.decoder.JSONDecodeError as e:
            print("数据解析失败，请稍后重试。")
            logger.exception(f"Data Parse Error: {e}")
            print(e)
        print(self.allData)
        return self.allData

    def logout(self) -> (bool, str):
        """
        登出，操作内会自动获取特征码，海事这个操作没啥用，会自动重连
        :return:元组第一项：是否操作成功；第二项：详细信息
        """
        # if self.alldata == None:
        #     self.get_alldata()

        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        res = requests.get(self.url + "logout", headers=self.header, verify=False)
        logout_json = json.loads(res.text)
        self.info = logout_json["message"]
        logger.info(f"Logout: {logout_json}")
        if logout_json["result"] == "success":
            return True, "下线成功"
        else:
            return False, self.info


if __name__ == "__main__":
    net_auth = ShmtuNetAuthCore()
    # print(net_auth.test_net())
    print(net_auth.login("", ""))
