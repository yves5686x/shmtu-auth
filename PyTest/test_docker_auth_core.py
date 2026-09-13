"""``docker_headless`` 认证主流程的单测。

Docker 部署是**无人值守**的：没有弹窗，验证码全靠 OCR，出问题也没人看得见。
所以这里把「两种接入类型依次尝试、验证码重试、OCR 失败、密码加密、兜底策略」
这几条关键路径都钉住。

``EPortalClient`` 被替换成可编程的替身，测试不发任何真实请求。
"""

import pytest
from _portal_test_utils import docker_app

MODULUS = (
    "94dd2a8675fb779e6b9f7103698634cd400f27a154afa67af6166a43fc26417222a79506d34cacc7641946abda1785b7"
    "acf9910ad6a0978c91ec84d40b71d2891379af19ffb333e7517e390bd26ac312fe940c340466b4a5d4af1d65c3b5944"
    "078f96a1a51a5a53e4bc302818b7c9f63c4a1b07bd7d874cef1c3d4b2f5eb7871"
)

MAC = "67d1ff70d8b083fe77eff0367912afaa"
FAKE_IMAGE = b"\x89PNG\r\n\x1a\n-fake-captcha"
USER = "202540510004"
PASSWORD = "MyPass123"
QUERY_STRING = "wlanuserip=abc&wlanacname=def&mac=" + MAC

# 每次 _login_eportal 内部创建出来的替身，方便测试断言
CREATED = []
# 下一个被创建出来的替身要应用的配置（在 __init__ 里生效）
PENDING = {}

# 绝大多数用例关心的是「流程走对了」，所以替身默认返回成功
SUCCESS = {"result": "success", "message": ""}
FAIL = {"result": "fail", "message": "认证失败"}


def plan(**kwargs):
    """描述「下一次请求门户时，门户应该长什么样」。"""
    PENDING.clear()
    PENDING.update(kwargs)


def make_fake_portal_client(page_info_cls):
    class FakePortalClient:
        def __init__(self, session, base_url, user_agent=None):
            self.session = session
            self.base_url = base_url
            self.opened = []
            self.fetched_urls = []
            self.submitted = []
            self.page_info_calls = 0
            self.valid_code_url = "/eportal/validcode?rnd=1"
            self.password_encrypt = True
            self.images = {}
            # 依次消费；用完后回落到 fallback_login
            self.login_responses = []
            self.fallback_login = dict(SUCCESS)

            for key, value in PENDING.items():
                # dict 类型的配置要复制，避免多个替身共享同一个 mapping
                setattr(self, key, dict(value) if isinstance(value, dict) else value)

            CREATED.append(self)

        def open_entry(self, query_string):
            self.opened.append(query_string)
            return True

        def query_page_info(self, query_string):
            self.page_info_calls += 1
            # need_valid_code 由 valid_code_url 是否为空决定，与真实实现一致
            return page_info_cls(
                valid_code_url=self.valid_code_url,
                password_encrypt=self.password_encrypt,
                public_key_modulus=MODULUS,
                public_key_exponent="10001",
                raw={"ok": True},
            )

        def fetch_valid_code(self, valid_code_url):
            self.fetched_urls.append(valid_code_url)
            return self.images.get(valid_code_url, FAKE_IMAGE)

        def submit_login(self, data):
            self.submitted.append(dict(data))
            if self.login_responses:
                return self.login_responses.pop(0)
            return dict(self.fallback_login)

        def query_account_service(self, query_string, user):
            return ""

        def query_services(self, query_string):
            return []

    return FakePortalClient


@pytest.fixture
def env(monkeypatch):
    """在 docker 模块上下文里搭好一个 HeadlessNetAuth，并挡住真实联网探测。"""
    CREATED.clear()
    PENDING.clear()

    with docker_app() as (eportal_protocol, auth_core, captcha_solver, portal_crypto):
        monkeypatch.setattr(
            auth_core, "EPortalClient", make_fake_portal_client(eportal_protocol.PortalPageInfo)
        )
        # 真实环境靠 OCR 后端拿验证码；测试里默认假装后端可用，识别结果由
        # use_ocr / no_ocr 控制。要验证「后端缺失就快速失败」用 no_ocr_backend。
        monkeypatch.setattr(auth_core, "get_available_solvers", lambda: ["fake-ocr"])

        auth = auth_core.HeadlessNetAuth()
        # 真实 is_connected() 会去访问百度和 B 站，测试里一律当作离线
        monkeypatch.setattr(auth, "is_connected", lambda: False)

        yield {
            "auth": auth,
            "auth_core": auth_core,
            "captcha_solver": captcha_solver,
            "portal_crypto": portal_crypto,
            "client": lambda: CREATED[-1],
        }


def use_ocr(env, monkeypatch, code="1234"):
    monkeypatch.setattr(env["auth_core"], "solve_captcha", lambda image: code)


def no_ocr(env, monkeypatch):
    monkeypatch.setattr(env["auth_core"], "solve_captcha", lambda image: None)


def no_ocr_backend(env, monkeypatch):
    """模拟「容器里忘了装 OCR 依赖」：一个可用后端都没有。"""
    monkeypatch.setattr(env["auth_core"], "get_available_solvers", lambda: [])


# ------------------------------------------------------------------ 正常路径


def test_login_success_with_ocr_and_encryption(env, monkeypatch):
    use_ocr(env, monkeypatch, "8291")

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert (ok, msg) == (True, "Login Success (Portal)")
    assert env["auth"].is_login is True

    # 会话必须先建立在 index.jsp 上，验证码与会话绑定
    assert client.opened == [QUERY_STRING]
    assert client.fetched_urls == ["/eportal/validcode?rnd=1"]

    payload = client.submitted[-1]
    assert payload["userId"] == USER
    assert payload["queryString"] == QUERY_STRING
    # OCR 结果被带进 validcode
    assert payload["validcode"] == "8291"
    # 门户要求加密时，密码必须换成 RSA 密文，且 mac 取自 queryString
    assert payload["passwordEncrypt"] == "true"
    assert payload["password"] != PASSWORD
    assert payload["password"] == env["portal_crypto"].encrypt_password(
        PASSWORD, MAC, MODULUS, "10001"
    )
    # 替身没有账号绑定服务，兜底到校园网
    assert payload["service"] == env["auth_core"].ServiceType.EDU


def test_candidates_are_submitted_in_order(env, monkeypatch):
    """两种接入类型依次尝试：第一个失败就换下一个，成功即停。"""
    use_ocr(env, monkeypatch)
    plan(
        login_responses=[
            {"result": "fail", "message": "认证失败"},
            {"result": "success", "message": ""},
        ],
    )

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert (ok, msg) == (True, "Login Success (Portal)")
    assert [item["service"] for item in client.submitted] == [
        env["auth_core"].ServiceType.EDU,
        env["auth_core"].ServiceType.ISMU,
    ]
    # 成功的那一个被记住，供本进程下次优先尝试
    assert env["auth"]._last_ok_service == env["auth_core"].ServiceType.ISMU


def test_both_services_failing_reports_both(env, monkeypatch):
    use_ocr(env, monkeypatch)
    plan(fallback_login=FAIL)

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    assert ok is False
    assert "两种接入类型均登录失败" in msg and "认证失败" in msg
    assert len(env["client"]().submitted) == 2, "两种接入类型都要试过"


def test_remembered_service_is_tried_first(env, monkeypatch):
    use_ocr(env, monkeypatch)
    env["auth"]._last_ok_service = env["auth_core"].ServiceType.ISMU
    plan(fallback_login=FAIL)

    env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    assert env["client"]().submitted[0]["service"] == env["auth_core"].ServiceType.ISMU


def test_config_pins_service_and_skips_the_other(env, monkeypatch):
    use_ocr(env, monkeypatch)
    monkeypatch.setenv("SHMTU_AUTH_PORTAL_SERVICE", "iSMU")
    plan(fallback_login=FAIL)

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert ok is False
    assert msg == "认证失败", "定死一个 service 时不该出现「两种接入类型」的措辞"
    assert [item["service"] for item in client.submitted] == ["iSMU"]


def test_captcha_error_does_not_switch_service(env, monkeypatch):
    """验证码问题换接入类型也没用：不该为它多跑一轮完整流程。"""
    use_ocr(env, monkeypatch, "1111")
    monkeypatch.setenv("SHMTU_AUTH_CAPTCHA_MAX_RETRY", "2")
    plan(fallback_login={"result": "fail", "message": "验证码错误."})

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    assert ok is False
    assert "验证码" in msg
    assert len(env["client"]().submitted) == 2, "只在第一种接入类型上重试，不该换 service"


def test_password_not_re_encrypted_when_already_encrypted(env, monkeypatch):
    use_ocr(env, monkeypatch)

    env["auth"]._login_eportal(USER, "ciphertext-from-gui", QUERY_STRING, password_encrypt=True)

    payload = env["client"]().submitted[-1]
    assert payload["password"] == "ciphertext-from-gui"
    assert payload["passwordEncrypt"] == "true"


def test_portal_without_validcode_still_works(env, monkeypatch):
    """门户若不再强制验证码，流程要能自己退化，不该硬塞 validcode。"""
    no_ocr(env, monkeypatch)
    plan(valid_code_url="")

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert (ok, msg) == (True, "Login Success (Portal)")
    assert client.fetched_urls == []
    assert client.submitted[-1]["validcode"] == ""


def test_empty_query_string_is_rejected(env):
    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, "   ")

    assert ok is False
    assert "Query string is invalid" in msg


# ---------------------------------------------------------------- 验证码重试


def test_validcode_rejected_then_retry_with_new_image(env, monkeypatch):
    """验证码一次性：服务端拒绝时会带回新图地址，必须重取重试。"""
    use_ocr(env, monkeypatch, "1111")
    plan(
        valid_code_url="/eportal/validcode?rnd=1",
        images={"/eportal/validcode?rnd=1": FAKE_IMAGE, "/eportal/validcode?rnd=2": FAKE_IMAGE},
        login_responses=[
            {"result": "fail", "message": "验证码错误.", "validCodeUrl": "/eportal/validcode?rnd=2"},
            {"result": "success", "message": ""},
        ],
    )

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert (ok, msg) == (True, "Login Success (Portal)")
    assert client.fetched_urls == ["/eportal/validcode?rnd=1", "/eportal/validcode?rnd=2"]
    assert len(client.submitted) == 2


def test_validcode_retry_refetches_page_info_without_new_url(env, monkeypatch):
    """服务端没带新图地址时要重新问 pageInfo，而不是复用旧图。"""
    use_ocr(env, monkeypatch, "1111")
    plan(
        login_responses=[
            {"result": "fail", "message": "验证码错误."},  # 故意不带 validCodeUrl
            {"result": "success", "message": ""},
        ],
    )

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert ok is True, msg
    # 首次 pageInfo + 重试时再来一次
    assert client.page_info_calls == 2
    assert len(client.submitted) == 2


def test_validcode_retry_exhausted(env, monkeypatch):
    use_ocr(env, monkeypatch, "1111")
    monkeypatch.setenv("SHMTU_AUTH_CAPTCHA_MAX_RETRY", "2")
    plan(fallback_login={"result": "fail", "message": "验证码错误."})

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    assert ok is False
    assert "验证码连续 2 次未通过" in msg
    assert len(env["client"]().submitted) == 2, "重试次数应受 SHMTU_AUTH_CAPTCHA_MAX_RETRY 控制"


# ------------------------------------------------------------- 验证码获取失败


def test_ocr_failure_retries_with_fresh_images(env, monkeypatch):
    """无头环境没有弹窗兜底：识别不出来要换新图继续试，而不是立刻放弃。"""
    no_ocr(env, monkeypatch)
    monkeypatch.setenv("SHMTU_AUTH_CAPTCHA_MAX_RETRY", "3")

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert ok is False
    assert "未能识别" in msg
    assert client.submitted == [], "识别不出验证码时不能拿空 validcode 去撞"
    assert len(client.fetched_urls) == 3, "每轮都要重新取图（服务端每次都是新图）"


def test_default_retry_count_is_generous(env, monkeypatch):
    """无头环境扛不住单次识别失败，默认重试次数必须给足。"""
    no_ocr(env, monkeypatch)
    monkeypatch.delenv("SHMTU_AUTH_CAPTCHA_MAX_RETRY", raising=False)

    env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    assert env["auth_core"].DEFAULT_CAPTCHA_MAX_RETRY >= 5
    assert len(env["client"]().fetched_urls) == env["auth_core"].DEFAULT_CAPTCHA_MAX_RETRY


def test_missing_ocr_backend_fails_fast(env, monkeypatch):
    """忘了装 OCR 依赖要立刻说清楚，而不是跑满重试再报一句含糊的失败。"""
    no_ocr_backend(env, monkeypatch)

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert ok is False
    assert "OCR" in msg
    assert client.submitted == []
    assert client.fetched_urls == [], "没有识别能力时连图都不必取"


def test_missing_ocr_backend_is_ok_when_provider_given(env, monkeypatch):
    """有弹窗 / 外部输入通道时，没有 OCR 后端也能正常登录。"""
    no_ocr_backend(env, monkeypatch)
    no_ocr(env, monkeypatch)

    ok, msg = env["auth"]._login_eportal(
        USER, PASSWORD, QUERY_STRING, captcha_provider=lambda image: "1234"
    )

    assert ok is True, msg


def test_ocr_failure_falls_back_to_provider(env, monkeypatch):
    """给了 captcha_provider 时（例如未来接 GUI / 外部输入）要能用上。"""
    no_ocr(env, monkeypatch)
    seen = []

    def provider(image):
        seen.append(image)
        return "  4 5 6 7 "

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING, captcha_provider=provider)

    assert ok is True, msg
    assert seen == [FAKE_IMAGE]
    # provider 的返回值会过 normalize_code，把空格去掉
    assert env["client"]().submitted[-1]["validcode"] == "4567"


def test_provider_returning_garbage_is_rejected(env, monkeypatch):
    no_ocr(env, monkeypatch)

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING, captcha_provider=lambda image: "ab")

    assert ok is False
    assert env["client"]().submitted == []


def test_provider_raising_does_not_crash(env, monkeypatch):
    no_ocr(env, monkeypatch)

    def boom(image):
        raise RuntimeError("GUI 挂了")

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING, captcha_provider=boom)

    assert ok is False
    assert "未能识别" in msg


def test_missing_validcode_image_is_retried(env, monkeypatch):
    """取图失败也要重试，不能第一张图挂了就整个登录失败。"""
    use_ocr(env, monkeypatch)
    monkeypatch.setenv("SHMTU_AUTH_CAPTCHA_MAX_RETRY", "3")
    plan(images={"/eportal/validcode?rnd=1": None})

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    client = env["client"]()
    assert ok is False
    assert "验证码图片下载失败" in msg
    assert client.submitted == []
    assert len(client.fetched_urls) == 3


# --------------------------------------------------------------- 失败与兜底


def test_non_captcha_failure_returns_server_message(env, monkeypatch):
    use_ocr(env, monkeypatch)
    plan(fallback_login=FAIL)

    ok, msg = env["auth"]._login_eportal(USER, PASSWORD, QUERY_STRING)

    assert ok is False
    # 两种接入类型都试过之后，服务端原文要保留在消息里，方便排查
    assert "认证失败" in msg


def test_login_skips_h3c_fallback_on_captcha_error(env, monkeypatch):
    """验证码问题换门户也没用，不该再去试 H3C。"""
    auth = env["auth"]
    monkeypatch.setattr(
        auth, "_login_eportal", lambda *a, **kw: (False, "验证码连续 3 次未通过: 验证码错误.")
    )
    called = []
    monkeypatch.setattr(auth, "_login_h3c", lambda *a, **kw: called.append(1) or (True, "H3C"))

    ok, msg = auth.login(USER, PASSWORD, skip_network_check=True)

    assert ok is False
    assert msg == "验证码连续 3 次未通过: 验证码错误."
    assert called == []


def test_login_falls_back_to_h3c_on_other_failure(env, monkeypatch):
    auth = env["auth"]
    monkeypatch.setattr(auth, "_login_eportal", lambda *a, **kw: (False, "认证失败"))
    monkeypatch.setattr(auth, "get_auth_result", lambda **kw: "http://1.1.1.1/auth.html|a=1")
    monkeypatch.setattr(auth, "_login_h3c", lambda user, pwd, portal_url="": (True, "Login Success (H3C)"))

    ok, msg = auth.login(USER, PASSWORD, skip_network_check=True)

    assert (ok, msg) == (True, "Login Success (H3C)")


def test_login_reports_both_failures_when_both_paths_fail(env, monkeypatch):
    auth = env["auth"]
    monkeypatch.setattr(auth, "_login_eportal", lambda *a, **kw: (False, "认证失败"))
    monkeypatch.setattr(auth, "get_auth_result", lambda **kw: "http://1.1.1.1/auth.html|a=1")
    monkeypatch.setattr(auth, "_login_h3c", lambda user, pwd, portal_url="": (False, "H3C 超时"))

    ok, msg = auth.login(USER, PASSWORD, skip_network_check=True)

    assert ok is False
    assert "认证失败" in msg and "H3C 超时" in msg


def test_login_does_not_use_h3c_without_portal_url(env, monkeypatch):
    auth = env["auth"]
    monkeypatch.setattr(auth, "_login_eportal", lambda *a, **kw: (False, "pageInfo request failed"))
    monkeypatch.setattr(auth, "get_auth_result", lambda **kw: "")
    called = []
    monkeypatch.setattr(auth, "_login_h3c", lambda *a, **kw: called.append(1) or (True, "H3C"))

    ok, msg = auth.login(USER, PASSWORD, skip_network_check=True)

    assert ok is False
    assert called == []


def test_login_rejects_empty_credentials(env):
    ok, msg = env["auth"].login("", PASSWORD, skip_network_check=True)

    assert ok is False
    assert "empty" in msg.lower()


def test_login_short_circuits_when_already_online(env, monkeypatch):
    auth = env["auth"]
    monkeypatch.setattr(auth, "is_connected", lambda: True)

    assert auth.login(USER, PASSWORD) == (True, "Already online")


def test_login_passes_provider_down_to_eportal(env, monkeypatch):
    """login() 必须把 captcha_provider 透传到主流程。"""
    auth = env["auth"]
    captured = {}

    def fake_eportal(user, password, query_string, password_encrypt=False, captcha_provider=None):
        captured["provider"] = captcha_provider
        return True, "ok"

    monkeypatch.setattr(auth, "_login_eportal", fake_eportal)
    provider = lambda image: "1234"  # noqa: E731

    auth.login(USER, PASSWORD, skip_network_check=True, captcha_provider=provider)

    assert captured["provider"] is provider


# --------------------------------------------------------------------- 工具


def test_extract_mac(env):
    extract = env["auth_core"].HeadlessNetAuth._extract_mac
    default_mac = env["portal_crypto"].DEFAULT_MAC

    assert extract("") == default_mac
    assert extract("wlanuserip=a&mac=deadbeef") == "deadbeef"
    assert extract("?wlanuserip=a&mac=deadbeef") == "deadbeef"
    # mac 为空时门户 JS 会退化成 111111111
    assert extract("wlanuserip=a&mac=") == default_mac


def test_login_api_defaults_to_portal(env):
    auth = env["auth"]
    assert auth.portal_base == env["auth_core"].DEFAULT_PORTAL_BASE
    assert auth.login_api.endswith("eportal/InterFace.do?method=")


def test_login_api_honours_env_override(monkeypatch):
    with docker_app() as (_, auth_core, _, _):
        monkeypatch.setenv("SHMTU_AUTH_LOGIN_URL", "https://example.test/eportal/")
        auth = auth_core.HeadlessNetAuth()

        assert auth.portal_base == "https://example.test/eportal/"
        assert auth.login_api == "https://example.test/eportal/InterFace.do?method="
