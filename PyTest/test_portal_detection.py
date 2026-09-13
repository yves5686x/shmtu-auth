"""门户识别判据的单测。

2026-09 门户改版后地址从 ``hwifi.shmtu.edu.cn`` 换成了
``ismu.shmtu.edu.cn:8443/eportal/``，但探测代码里的判据还停留在旧域名，
导致网关明明把请求劫持到了新认证页、代码却认不出来，最后只能返回空
queryString，报出「Query String is Invalid」。

这个坑的表现是「没抓到门户地址」，很容易被误判成网络不通，所以把判据
钉死在这里，主包和 docker 副本各测一遍。
"""

import pytest
from _portal_test_utils import docker_app

from shmtu_auth.src.core.core import ShmtuNetAuthCore
from shmtu_auth.src.core.get_query_string_requests import looks_like_portal

# 2026-09 改版后的统一门户
NEW_PORTAL = (
    "https://ismu.shmtu.edu.cn:8443/eportal/index.jsp"
    "?wlanuserip=9660ce92c1fd65a271f3ab972569faad"
    "&wlanacname=436da45d7eab307de7f4e23d9acef73c"
    "&ssid=&nasip=839548253c34fbabf4d91f0984d9ec49"
    "&mac=67d1ff70d8b083fe77eff0367912afaa"
    "&t=wireless-v2"
)

# 旧无线门户
OLD_PORTAL = "http://hwifi.shmtu.edu.cn/auth.html?userip=10.11.19.117"


def docker_looks_like_portal():
    """取出 docker 副本里的同款函数（独立副本，不能 import 主包）。"""
    with docker_app() as (_, auth_core, _, _):
        return auth_core.looks_like_portal


@pytest.fixture(params=["main", "docker"])
def detect(request):
    """参数化：主包与 docker 副本的判据必须行为一致。"""
    if request.param == "main":
        return looks_like_portal
    return docker_looks_like_portal()


class TestLooksLikePortal:
    def test_new_portal_ismu_is_recognized(self, detect):
        """核心回归：新门户 ismu.shmtu.edu.cn 必须被认出来。"""
        assert detect(NEW_PORTAL) is True

    def test_old_portal_hwifi_still_recognized(self, detect):
        assert detect(OLD_PORTAL) is True

    def test_path_marker_works_for_unknown_host(self, detect):
        """门户再换域名时，路径特征 /eportal/ 仍能兜住。"""
        assert detect("https://brand-new-portal.example.com/eportal/index.jsp?a=b") is True

    def test_generic_hijack_cross_domain_with_query(self, detect):
        """请求 A 却跳到域名 B 且带参数 —— 典型的 captive portal 劫持。"""
        assert detect("http://1.1.1.1", original_url="http://neverssl.com") is False
        assert detect("http://gate.example.net/redirect?userip=1.2.3.4", original_url="http://neverssl.com") is True

    def test_normal_page_not_portal(self, detect):
        assert detect("http://example.com") is False
        assert detect("https://ismu.shmtu.edu.cn:8443/eportal/") is True  # 路径命中
        assert detect("http://www.baidu.com/index.html") is False

    def test_https_upgrade_same_host_is_not_hijack(self, detect):
        """http→https 同域升级是正常行为，不能误判成劫持。"""
        assert detect("https://example.com/", original_url="http://example.com") is False

    def test_empty_input(self, detect):
        assert detect("") is False
        assert detect(None) is False


class TestSplitAuthResult:
    """新门户 URL 没有 '|' 分隔时，也必须能拆出 queryString。"""

    def test_main_splits_new_portal_url(self):
        portal_url, query_string = ShmtuNetAuthCore._split_auth_result(NEW_PORTAL)
        assert portal_url == NEW_PORTAL
        assert query_string.startswith("wlanuserip%3D")
        assert "mac%3D67d1ff70d8b083fe77eff0367912afaa" in query_string

    def test_docker_splits_new_portal_url(self):
        with docker_app() as (_, auth_core, _, _):
            portal_url, query_string = auth_core.HeadlessNetAuth._split_auth_result(NEW_PORTAL)
        assert portal_url == NEW_PORTAL
        assert "mac%3D67d1ff70d8b083fe77eff0367912afaa" in query_string

    def test_bare_query_string_passthrough(self):
        """只给裸 queryString 时，门户地址为空、原样返回。"""
        portal_url, query_string = ShmtuNetAuthCore._split_auth_result("wlanuserip=a&mac=b")
        assert portal_url == ""
        assert query_string == "wlanuserip=a&mac=b"


class TestManualQueryString:
    """手动指定时必须完全跳过自动探测。"""

    def test_manual_value_skips_probing(self, monkeypatch):
        import shmtu_auth.src.core.core_exp as core_exp

        monkeypatch.setattr(core_exp, "get_manual_query_string", lambda: NEW_PORTAL)
        monkeypatch.setattr(
            core_exp,
            "get_query_string_by_url",
            lambda *a, **k: pytest.fail("配了手动 queryString 就不该再发起探测"),
        )

        assert core_exp.get_query_string() == NEW_PORTAL

    def test_docker_manual_value_skips_probing(self, monkeypatch):
        with docker_app() as (_, auth_core, _, _):
            monkeypatch.setattr(
                auth_core,
                "get_env_str",
                lambda key, default=None: NEW_PORTAL
                if key == "SHMTU_AUTH_QUERY_STRING"
                else default,
            )
            client = auth_core.HeadlessNetAuth()
            client.is_connected = lambda: False
            client._get_text_code = lambda *a, **k: pytest.fail("配了手动 queryString 就不该再探测")

            assert client.get_auth_result() == NEW_PORTAL


class TestConnectivity:
    """连通性探测不能把「上不了网」误判成「已联网」。

    踩过两个坑，都在下面守着：

    1. 机器设了代理时，代理返回 502 错误页。原判断只看 ``status_code <= 0``，
       502 也算通过，于是程序认为「已联网、无需认证」，直接返回空 queryString
       —— 用户看到的就是「明明上不了网却显示在线」。

    2. 更隐蔽的：透明代理 / 缓存 / DNS 劫持会对所有 http 请求返回 200，
       但内容根本不是目标网站。此时 https 一定不通（校园网不劫持 https）。
       所以判据是「必须至少一个 https 目标通过」，只有 http 通过一律判离线。
    """

    BAIDU = "http://www.baidu.com"
    BILIBILI = "https://www.bilibili.com/"
    QQ = "https://www.qq.com/"

    @classmethod
    def _as_mapping(cls, responses) -> dict:
        """把 (url, (text, status, final_url)) 序列转成按 url 索引的表。

        用映射而不是迭代器，测试就不必关心探测目标的个数和顺序，
        增删探测目标时不会因为数量对不上而误报 StopIteration。
        """
        return dict(responses)

    @classmethod
    def _main_connected(cls, monkeypatch, responses):
        from shmtu_auth.src.core import get_query_string_requests as mod

        mapping = cls._as_mapping(responses)

        def fake_probe(urls, *args, **kwargs):
            out = []
            for url in urls:
                text, status, final_url = mapping.get(url, ("", 0, ""))
                out.append(
                    mod.ProbeResult(
                        url=url, text=text, status=status, final_url=final_url
                    )
                )
            return out

        monkeypatch.setattr(mod, "probe_many", fake_probe)
        return mod.is_connect_by_sites()

    @classmethod
    def _docker_connected(cls, monkeypatch, responses):
        with docker_app() as (_, auth_core, _, _):
            client = auth_core.HeadlessNetAuth()
            mapping = cls._as_mapping(responses)
            monkeypatch.setattr(
                client, "_get_text_code", lambda url: mapping.get(url, ("", 0, ""))
            )
            return client.is_connected()

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_502_from_proxy_is_offline(self, monkeypatch, runner):
        """代理返回 502 错误页：必须判为离线（原来会误判成在线）。"""
        responses = [
            (self.BAIDU, ("", 502, self.BAIDU)),
            (self.BILIBILI, ("", 502, self.BILIBILI)),
            (self.QQ, ("", 502, self.QQ)),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_407_proxy_auth_required_is_offline(self, monkeypatch, runner):
        responses = [
            (self.BAIDU, ("", 407, self.BAIDU)),
            (self.BILIBILI, ("", 407, self.BILIBILI)),
            (self.QQ, ("", 407, self.QQ)),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_200_with_foreign_body_is_offline(self, monkeypatch, runner):
        """状态码 200 但内容是代理/网关的拦截页，也不能算联网。"""
        responses = [
            (self.BAIDU, ("<html>Proxy Error</html>", 200, self.BAIDU)),
            (self.BILIBILI, ("<html>Proxy Error</html>", 200, self.BILIBILI)),
            (self.QQ, ("<html>Proxy Error</html>", 200, self.QQ)),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_real_page_is_online(self, monkeypatch, runner):
        """https 目标真正拿到内容才算联网。"""
        responses = [
            (self.BILIBILI, ("<html>bilibili 哔哩哔哩</html>", 200, self.BILIBILI)),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is True

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_http_only_200_is_offline(self, monkeypatch, runner):
        """【本次修复的核心】http 全 200、https 全不通 —— 一眼假，必须判离线。

        真实场景：校园网未认证时，透明代理 / 缓存 / DNS 劫持会对所有 http
        请求返回 200（内容空白或是拦截页），但 https 必然不通。
        只凭 http 的 200 就判「已联网」，程序会认为无需认证，
        用户就彻底上不了网了。
        """
        responses = [
            (self.BAIDU, ("<html>百度 baidu.com</html>", 200, self.BAIDU)),
            # https 全部连不上
            (self.BILIBILI, ("", 0, "")),
            (self.QQ, ("", 0, "")),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_https_error_response_still_means_online(self, monkeypatch, runner):
        """https 返回 4xx/5xx 也算外网可达 —— 跟「连不上」是两回事。

        服务器能返回 412 / 501（WAF 拦爬虫），说明 TLS 握手成功、真的到了
        服务器；而未认证时 https 是超时（状态码 0），根本拿不到响应。
        把两者都当成「不通」会让已经能上网的机器被反复判定为需认证。
        """
        responses = [
            (self.BAIDU, ("<html>百度 baidu.com</html>", 200, self.BAIDU)),
            (self.BILIBILI, ("<html>waf blocked</html>", 412, self.BILIBILI)),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is True

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_redirected_to_portal_is_offline(self, monkeypatch, runner):
        """被 captive portal 劫持时必须判为离线。"""
        responses = [
            (self.BAIDU, ("<html>portal</html>", 200, NEW_PORTAL)),
            (self.BILIBILI, ("<html>portal</html>", 200, NEW_PORTAL)),
            (self.QQ, ("<html>portal</html>", 200, NEW_PORTAL)),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_request_exception_is_offline(self, monkeypatch, runner):
        responses = []
        assert getattr(self, runner)(monkeypatch, responses) is False


class TestProbeBypassesProxy:
    """探测请求必须绕过系统代理，否则网关劫持不到。"""

    def test_main_session_trust_env_disabled(self):
        from shmtu_auth.src.core import get_query_string_requests as mod

        mod._session = None  # 强制重建
        session = mod._get_session()
        assert session.trust_env is False
        mod._session = None

    def test_docker_session_trust_env_disabled(self):
        with docker_app() as (_, auth_core, _, _):
            client = auth_core.HeadlessNetAuth()
        assert client.session.trust_env is False


class TestPublicApiStillExists:
    """防止重构时误删公开函数。

    真实事故：把探测改成并发时，先删了 ``get_text_code`` 再加回来，
    中间态下 GUI 的「手动测试网络连接」直接抛
    ``NameError: name 'get_text_code' is not defined``。

    这类错误特别阴险：``py_compile`` 全过（语法没问题）、单测全绿
    （没覆盖到那条路径），只有真正跑起来才炸。所以在这里把公开 API
    的存在性钉死 —— 谁再误删，测试立刻变红。
    """

    MAIN_REQUIRED = (
        "get_text_code",
        "probe_many",
        "judge_connectivity",
        "looks_like_portal",
        "is_connect_by_sites",
        "is_connect_by_google",
        "get_query_string_by_url",
        "get_query_string_by_baidu",
        "ProbeResult",
    )

    # docker 副本里挂在 client 上的方法
    DOCKER_REQUIRED = (
        "_get_text_code",
        "is_connected",
        "_is_expected_host",
        "get_auth_result",
    )

    # docker 副本里挂在模块上的函数（注意不是 client 的方法）
    DOCKER_MODULE_REQUIRED = ("looks_like_portal",)

    def test_main_module_keeps_public_api(self):
        from shmtu_auth.src.core import get_query_string_requests as mod

        missing = [name for name in self.MAIN_REQUIRED if not hasattr(mod, name)]
        assert not missing, f"主包公开 API 被误删: {missing}"

        for name in self.MAIN_REQUIRED:
            if name == "ProbeResult":
                continue
            assert callable(getattr(mod, name)), f"{name} 不是可调用对象"

    def test_docker_module_keeps_public_api(self):
        with docker_app() as (_, auth_core, _, _):
            client = auth_core.HeadlessNetAuth()
            missing = [
                name for name in self.DOCKER_REQUIRED if not hasattr(client, name)
            ]
            assert not missing, f"docker 副本方法被误删: {missing}"

            missing_module = [
                name
                for name in self.DOCKER_MODULE_REQUIRED
                if not hasattr(auth_core, name)
            ]
            assert not missing_module, (
                f"docker 副本模块级函数被误删: {missing_module}"
            )

    def test_legacy_helper_is_actually_callable(self, monkeypatch):
        """光 hasattr 不够 —— 还要真能调用，不抛 NameError。"""
        from shmtu_auth.src.core import get_query_string_requests as mod

        monkeypatch.setattr(
            mod,
            "probe_many",
            lambda urls, *a, **k: [
                mod.ProbeResult(url=u, text="", status=0, final_url="") for u in urls
            ],
        )

        # 不通时返回 ("", 0, "")，重点是调用过程不能抛 NameError
        assert mod.get_text_code("http://127.0.0.1:1") == ("", 0, "")
