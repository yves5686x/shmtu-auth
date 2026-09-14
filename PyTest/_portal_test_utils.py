"""门户相关测试的共用工具。

放在 ``_`` 前缀下是为了不被 pytest 当成测试模块收集
（pytest.ini 只匹配 ``test_*.py`` 和 ``*_test.py``）。
"""




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
        self.kwargs_sent = []

    def _pick(self, url: str) -> FakeResponse:
        for key, response in self._responses.items():
            if f"method={key}" in url:
                return response
        return self._default

    def get(self, url, **kwargs):
        self.requests_sent.append(("GET", url))
        self.kwargs_sent.append(kwargs)
        return self._pick(url)

    def post(self, url, **kwargs):
        self.requests_sent.append(("POST", url))
        self.kwargs_sent.append(kwargs)
        return self._pick(url)
