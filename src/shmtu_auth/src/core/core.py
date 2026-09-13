import json
from urllib.parse import parse_qs, unquote, urlparse

import requests
from urllib3 import __version__ as urllib3_version

from shmtu_auth.src.core.captcha_solver import (
    CaptchaProvider,
    get_available_solvers,
    normalize_code,
    solve_captcha,
)
from shmtu_auth.src.core.core_exp import check_is_connected_retry, get_query_string
from shmtu_auth.src.core.eportal_protocol import (
    DEFAULT_PORTAL_BASE,
    EPortalClient,
    PortalPageInfo,
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


def _iter_query_string_variants(query_string: str):
    """给出 queryString 的候选形态：原串，以及 URL 解码后的串。

    探测拿到的 queryString 往往已经把 & 和 = 编码成 %26 / %3D（门户表单要求），
    直接 parse_qs 会解析不出任何字段。这里把两种形态都交给调用方试一遍。
    """
    raw = (query_string or "").strip()
    if not raw:
        return

    yield raw

    try:
        decoded = unquote(raw)
    except Exception:  # noqa: BLE001
        return
    if decoded != raw:
        yield decoded

# 门户的两种接入类型：(显示名, service 提交值)
#
# 门户的 service 由服务端下发，本应二选一：有线「校园网」/ 无线 i-SHMU。但"现在是
# 有线还是无线"这个判断依赖于运行环境上报的网络类型，在容器 / Docker / 网关代拨
# 等场景下经常不准（拿到的可能是宿主机或上一跳的类型）。选错的代价只是一轮多余的
# 请求，所以这里直接两个都试，不再猜。详见 _portal_service_candidates()。
DEFAULT_PORTAL_SERVICES: tuple[tuple[str, str], ...] = (
    ("校园网(有线)", ServiceType.EDU),
    ("iSMU(无线)", ServiceType.ISMU),
)

# 验证码自动重试次数的默认值。
#
# 验证码是一次性的，且服务端**每 GET 一次就换一张图**，所以每轮重试都是一次全新的
# 识别机会。无 GUI 的环境（Docker）没有弹窗兜底，扛不住单次识别失败，因此默认给
# 一个偏大的值。可用 SHMTU_AUTH_CAPTCHA_MAX_RETRY 覆盖。
DEFAULT_CAPTCHA_MAX_RETRY = 6


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
        # 上一次成功登录时生效的 service 值。仅内存记忆，用于把优先尝试顺序提前，
        # 不会改变"两个都试"的语义（见 _portal_service_candidates）。
        self._last_ok_service: str = ""
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
        # 不限定门户域名：ismu（新）/ hwifi（旧）以及任何未来换域名的门户都要能拆。
        if auth_result.lower().startswith(("http://", "https://")) and "?" in auth_result:
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
        """从 queryString 中取 mac，取不到时退化成 DEFAULT_MAC。

        queryString 在流程里有两种形态，必须都认：

          - 未编码：``wlanuserip=xxx&mac=yyy&t=zzz``
          - 已编码：``wlanuserip%3Dxxx%26mac%3Dyyy%26t%3Dzzz``（& → %26，= → %3D）

        门户表单要求把 & 和 = 编码后提交，所以探测拿到的多半是**第二种**。
        直接对它做 parse_qs 会把整串当成一个「没有值的 key」—— 实测表现就是
        「queryString 里明明写着 mac=67d1ff70…，程序却说没有 mac」，
        于是密码用 DEFAULT_MAC 参与加密，门户解不开，只回一句
        「用户不存在或者密码错误!」，把人引向反复检查密码。

        所以两种形态依次试，谁先解析出 mac 就用谁。
        """
        for candidate in _iter_query_string_variants(query_string):
            try:
                values = parse_qs(candidate.lstrip("?"))
            except Exception:  # noqa: BLE001
                continue
            mac = (values.get("mac") or [""])[0].strip()
            if mac:
                return mac

        return DEFAULT_MAC

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

    def _captcha_capable(self, provider: CaptchaProvider | None = None) -> bool:
        """当前环境有没有办法拿到验证码：OCR 后端可用，或上层提供了人工输入通道。

        都没有的话就没必要往门户发登录请求了 —— 无 GUI 的容器里如果忘了装 OCR，
        直接给出「装 OCR」的提示比跑满重试次数后报一句含糊的失败有用得多。
        """
        if provider is not None:
            return True
        return bool(get_available_solvers())

    def _portal_service_candidates(self) -> list[tuple[str, str]]:
        """返回本次登录要**依次尝试**的 ``(显示名, service 提交值)`` 列表。

        注意这里刻意不做「按当前网络类型二选一」的判断：门户的 ``service`` 虽然由
        服务端下发，但"现在是有线还是无线"这件事在容器 / Docker / 网关代拨场景下经常
        判断错。两种都试一遍的代价很小（每轮约一次取图 + 一次 POST），却能让有线、
        无线、以及判断错误的场景全部自愈。

        顺序：

        1. 配置项 ``SHMTU_AUTH_PORTAL_SERVICE`` 非空 → **只试它**（显式定死，
           不再兜底，方便排查）
        2. 否则 → ``校园网(有线)`` 与 ``iSMU(无线)`` 依次尝试
        3. 若本进程上一次登录成功过，把当时生效的那个提到最前面（常驻进程从第二轮
           起就能一次命中；这只是内存记忆、不落盘，两个候选仍然都会被尝试）

        :return: ``[(显示名, 可直接放进 payload 的 service 值), ...]``，至少一项
        """
        configured = get_env_str("SHMTU_AUTH_PORTAL_SERVICE", "")
        if configured:
            resolved = encode_service_param(configured)
            logger.info(f"Portal service pinned by config: {configured} -> {resolved}")
            return [(configured, resolved)]

        candidates = [(name, encode_service_param(value)) for name, value in DEFAULT_PORTAL_SERVICES]

        remembered = getattr(self, "_last_ok_service", "")
        if remembered:
            hit = next((item for item in candidates if item[1] == remembered), None)
            if hit is not None:
                candidates.remove(hit)
                candidates.insert(0, hit)
                logger.info(f"Portal service candidates: {hit[0]} first (worked last time)")

        names = ", ".join(name for name, _ in candidates)
        logger.info(f"Portal service candidates (try in order): {names}")
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
                    logger.warning(f"{last_message}（第 {attempt}/{max_attempts} 次）")
                    continue

                valid_code = self._obtain_valid_code(image, captcha_provider)
                if not valid_code:
                    # 无 GUI 的容器里识别不出来是常态，不能当成致命错误直接放弃，
                    # 换一张图继续试才是正解
                    unrecognized += 1
                    last_message = "验证码识别失败"
                    logger.warning(f"验证码识别失败（第 {attempt}/{max_attempts} 次），换一张重试")
                    continue

                logger.info(f"Login attempt {attempt}/{max_attempts} with validcode, service={service}")

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

            # 非验证码类错误（账号密码错、服务不对、被风控……）：重试同样的图没意义，
            # 交给上层决定是否换一种接入类型
            if not is_valid_code_error(last_message):
                return False, last_message

            # 验证码被服务端拒绝：换新图重试
            logger.warning(f"Valid code rejected: {last_message}")
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
        pwd: str,
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
        if len(query_string) == 0:
            # 空 queryString 说明这台机器根本没有被网关重定向到认证页，
            # 跟账号密码无关。给出排查方向，否则用户只会去反复检查密码。
            return False, (
                "Query String is Invalid! —— 没抓到门户的 queryString\n"
                "这通常不是账号密码问题，而是本机没能被网关重定向到认证页：\n"
                "1. 先用浏览器随便打开一个 http 站点，看会不会自动跳到认证页；\n"
                "   不跳的话说明当前网络不需要认证，或者不在校园网内。\n"
                "2. 浏览器会跳、程序却抓不到，跑这个看探测细节：\n"
                "   PYTHONPATH=src python diagnose_portal.py"
            )

        client = EPortalClient(self.session, self.portal_base)
        client.open_entry(query_string)

        page_info = client.query_page_info(query_string)
        if not page_info.raw:
            return False, "pageInfo 请求失败，无法获取门户配置"

        if page_info.need_valid_code and not self._captcha_capable(captcha_provider):
            logger.error("Portal requires a captcha but no OCR backend or manual input is available")
            return False, (
                "门户要求图形验证码，但当前环境既没有可用的 OCR 后端，也没有人工输入通道；"
                "无 GUI 环境请安装 OCR 依赖（pip install -r requirements-ocr.txt）"
            )

        mac = self._extract_mac(query_string)
        if mac == DEFAULT_MAC:
            # 门户 JS 在拿不到 mac 时也用这个值兜底，所以行为一致；但密码会用这个错误的
            # mac 参与加密，服务端解密必然失败，最终只表现为含糊的「认证失败」。
            # 不显式告警的话，排查时极易误判成账号密码错误。
            logger.warning(
                f"queryString 中没有 mac 参数，已退化成门户默认值 {DEFAULT_MAC}；"
                "密码将用该值参与加密，门户多半解不开。"
                "请检查 queryString 是否完整，容器需使用 host 网络"
            )

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

        candidates = self._portal_service_candidates()
        last_message = ""

        for index, (display_name, service) in enumerate(candidates):
            logger.info(
                f"Portal login trying {display_name} ({service}) [{index + 1}/{len(candidates)}]"
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
                logger.warning(f"{display_name} 登录失败（{message}），换下一种接入类型重试")

        if len(candidates) > 1:
            return False, f"两种接入类型均登录失败（{last_message}）"
        return False, last_message

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

        # 上面 not skip 时刚跑过 test_net()（同一套探测），skip 时是调用方明确
        # 说外部已检测过 —— 两种情况下 get_query_string 内部都不需要再做一遍
        # 连通性探测。之前这里会把它白白跑两遍，认证前多等好几秒。
        auth_result = get_query_string(skip_connectivity_check=True).strip()
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
