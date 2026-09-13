"""门户相关测试的共用工具。

放在 ``_`` 前缀下是为了不被 pytest 当成测试模块收集
（pytest.ini 只匹配 ``test_*.py`` 和 ``*_test.py``）。
"""

import contextlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_HEADLESS = REPO_ROOT / "docker_headless"


class FakeResponse:
    """只实现被测代码用到的那几个属性。

    默认 ``encoding="ISO-8859-1"`` 是在复刻真实行为：门户返回
    ``Content-Type: text/html``（不带 charset），requests 会退回 ISO-8859-1。
    """

    def __init__(self, body: bytes, encoding: str = "ISO-8859-1", apparent: str = "utf-8", status: int = 200):
        self.content = body
        self.encoding = encoding
        self.apparent_encoding = apparent
        self.status_code = status
        self.url = "https://ismu.shmtu.edu.cn:8443/eportal/InterFace.do?method=x"
        self.headers = {"Content-Type": "text/html"}
        self.text = body.decode(encoding, errors="replace")


class FakeSession:
    """按 URL 里的 method 参数返回不同响应。

    可以传 ``{method: FakeResponse}`` 做分流，也可以直接传一个 ``FakeResponse``
    让所有请求都返回它。
    """

    def __init__(self, responses=None, default: FakeResponse = None):
        if isinstance(responses, FakeResponse):
            default = responses
            responses = None

        self._responses = dict(responses or {})
        self._default = default if default is not None else FakeResponse(b"{}")
        self.requests_sent = []

    def _pick(self, url: str) -> FakeResponse:
        for key, response in self._responses.items():
            if f"method={key}" in url:
                return response
        return self._default

    def get(self, url, **kwargs):
        self.requests_sent.append(("GET", url))
        return self._pick(url)

    def post(self, url, **kwargs):
        self.requests_sent.append(("POST", url))
        return self._pick(url)


class StubClient:
    """替代 EPortalClient，用于验证 service 取值优先级。"""

    def __init__(self, account_service: str = "", options=None):
        self._account_service = account_service
        self._options = list(options or [])
        self.account_calls = []

    def query_account_service(self, query_string, user):
        self.account_calls.append((query_string, user))
        return self._account_service

    def query_services(self, query_string):
        return [dict(item) for item in self._options]


@contextlib.contextmanager
def docker_app():
    """把 docker_headless 当作 ``app`` 包临时导入，退出时完全还原 sys.modules。"""
    saved_path = list(sys.path)
    saved_modules = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
    for key in list(saved_modules):
        del sys.modules[key]

    sys.path.insert(0, str(DOCKER_HEADLESS))
    try:
        import app.auth_core as auth_core
        import app.captcha_solver as captcha_solver
        import app.eportal_protocol as eportal_protocol
        import app.portal_crypto as portal_crypto

        yield eportal_protocol, auth_core, captcha_solver, portal_crypto
    finally:
        for key in list(sys.modules):
            if key == "app" or key.startswith("app."):
                del sys.modules[key]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path
