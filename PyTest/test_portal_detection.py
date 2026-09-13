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

    真实踩过的坑：机器设了代理，代理返回 502 错误页。原判断只看
    ``status_code <= 0``，502 也算通过，于是程序认为「已联网、无需认证」，
    直接返回空 queryString —— 用户看到的就是「明明上不了网却显示在线」。
    """

    @staticmethod
    def _main_connected(monkeypatch, responses):
        from shmtu_auth.src.core import get_query_string_requests as mod

        it = iter(responses)
        monkeypatch.setattr(mod, "get_text_code", lambda url, *a, **k: next(it))
        return mod.is_connect_by_sites()

    @staticmethod
    def _docker_connected(monkeypatch, responses):
        with docker_app() as (_, auth_core, _, _):
            client = auth_core.HeadlessNetAuth()
            it = iter(responses)
            monkeypatch.setattr(client, "_get_text_code", lambda url: next(it))
            return client.is_connected()

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_502_from_proxy_is_offline(self, monkeypatch, runner):
        """代理返回 502 错误页：必须判为离线（原来会误判成在线）。"""
        responses = [("", 502, "http://www.baidu.com"), ("", 502, "https://www.bilibili.com/")]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_407_proxy_auth_required_is_offline(self, monkeypatch, runner):
        responses = [("", 407, "http://www.baidu.com"), ("", 407, "https://www.bilibili.com/")]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_200_with_foreign_body_is_offline(self, monkeypatch, runner):
        """状态码 200 但内容是代理/网关的拦截页，也不能算联网。"""
        responses = [
            ("<html>Proxy Error</html>", 200, "http://www.baidu.com"),
            ("<html>Proxy Error</html>", 200, "https://www.bilibili.com/"),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_real_baidu_page_is_online(self, monkeypatch, runner):
        responses = [("<html>百度 baidu.com</html>", 200, "https://www.baidu.com/")]
        assert getattr(self, runner)(monkeypatch, responses) is True

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_redirected_to_portal_is_offline(self, monkeypatch, runner):
        """被 captive portal 劫持时必须判为离线。"""
        responses = [
            ("<html>portal</html>", 200, NEW_PORTAL),
            ("<html>portal</html>", 200, NEW_PORTAL),
        ]
        assert getattr(self, runner)(monkeypatch, responses) is False

    @pytest.mark.parametrize("runner", ["_main_connected", "_docker_connected"])
    def test_request_exception_is_offline(self, monkeypatch, runner):
        responses = [("", 0, ""), ("", 0, "")]
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
