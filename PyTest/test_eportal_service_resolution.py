"""门户 service 候选、响应编码与验证码判定的单测。

这里的期望值全部来自真实的门户抓包（``校园网登录网口流程.har`` /
``校园网登录ISMU流程.har``），因此可以直接证伪「提交的 service 和浏览器不一致」
这类回归。

service 的策略是**两种接入类型都试**（有线「校园网」/ 无线 i-SHMU），不再按运行
环境上报的网络类型二选一 —— 容器里的类型判断经常不准。

同时覆盖一个曾经真实存在过的 bug：门户响应 ``Content-Type: text/html`` 不带
charset，requests 会退回 ISO-8859-1，把 ``验证码错误.`` 解成乱码，导致验证码错误
既识别不出来、也不会重试。
"""

import json
from urllib.parse import urlencode

import pytest

from _portal_test_utils import FakeResponse, FakeSession, docker_app

from shmtu_auth.src.core.eportal_protocol import (
    PLACEHOLDER_SERVICE_PREFIX,
    EPortalClient,
    encode_service_param,
    is_valid_code_error,
)

# 两种网络环境下 HAR 的实际提交值
HAR_WIRED_SERVICE = "%E6%A0%A1%E5%9B%AD%E7%BD%91"
HAR_WIRELESS_SERVICE = "iSMU"
# HAR 里 login 请求体里的原始字节片段
HAR_WIRED_WIRE_FORM = "service=%25E6%25A0%25A1%25E5%259B%25AD%25E7%25BD%2591"
HAR_WIRELESS_WIRE_FORM = "service=iSMU"

# 门户返回的验证码错误提示（UTF-8 字节 + 不带 charset 的 Content-Type）
VALID_CODE_ERROR_BODY = '{"result":"fail","message":"验证码错误.","validCodeUrl":"/eportal/validcode?rnd=1"}'
AUTH_FAIL_BODY = '{"result":"fail","message":"认证失败","validCodeUrl":""}'


def _new_main_auth():
    from shmtu_auth.src.core.core import ShmtuNetAuthCore

    return ShmtuNetAuthCore()


def both_candidate_providers():
    """返回 [(名称, 工厂函数), ...]，主包与 docker 两份实现一起参数化。

    工厂函数每次新建一个实例，避免"记住上次成功的 service"这类实例状态在用例间串味。
    """
    providers = [("main", _new_main_auth)]

    with docker_app() as (_, auth_core, _, _):
        providers.append(("docker", lambda: auth_core.HeadlessNetAuth()))

    return providers


# ------------------------------------------------- service 参数编码与线上字节


def test_encode_service_param_quotes_raw_name():
    assert encode_service_param("校园网") == HAR_WIRED_SERVICE
    assert encode_service_param("iSMU") == HAR_WIRELESS_SERVICE
    # 两边的空白要忽略
    assert encode_service_param("  校园网  ") == HAR_WIRED_SERVICE


def test_encode_service_param_passes_through_encoded_value():
    """用户直接填了编码后的值时原样使用，避免二次编码。"""
    assert encode_service_param(HAR_WIRED_SERVICE) == HAR_WIRED_SERVICE


def test_encode_service_param_empty():
    assert encode_service_param("") == ""
    assert encode_service_param("   ") == ""
    assert encode_service_param(None) == ""


@pytest.mark.parametrize(
    "raw_name,expected_wire_form",
    [("校园网", HAR_WIRED_WIRE_FORM), ("iSMU", HAR_WIRELESS_WIRE_FORM)],
)
def test_service_wire_bytes_match_har(raw_name, expected_wire_form):
    """用 requests 的 data= 提交后，请求体必须和浏览器抓包逐字节一致。

    门户 JS 对隐藏域做了两次 encodeURIComponent，requests 又会再编码一次，
    所以这里只需要编码一次 —— 这个测试就是钉死这个推论的。
    """
    assert urlencode({"service": encode_service_param(raw_name)}) == expected_wire_form


# ---------------------------------------------------- 响应编码（乱码 bug 回归）


def test_post_json_decodes_utf8_without_charset():
    """Content-Type 不带 charset 时，也必须拿到正确的中文 message。"""
    res = FakeResponse(VALID_CODE_ERROR_BODY.encode("utf-8"))

    # 前提：requests 的默认行为确实会解出乱码
    assert "验证码" not in res.text

    decoded = EPortalClient._response_text(res)
    assert json.loads(decoded)["message"] == "验证码错误."


def test_valid_code_error_detected_after_utf8_decode():
    res = FakeResponse(VALID_CODE_ERROR_BODY.encode("utf-8"))
    payload = json.loads(EPortalClient._response_text(res))

    assert payload["result"] == "fail"
    assert is_valid_code_error(payload["message"]), "解码后必须能识别出验证码错误"


def test_auth_failure_not_mistaken_for_valid_code_error():
    res = FakeResponse(AUTH_FAIL_BODY.encode("utf-8"))
    payload = json.loads(EPortalClient._response_text(res))

    assert payload["message"] == "认证失败"
    assert not is_valid_code_error(payload["message"])


def test_response_text_handles_empty_body():
    assert EPortalClient._response_text(FakeResponse(b"")) == ""


def test_response_text_never_raises_on_undecodable_body():
    """彻底解不出来时也不能抛异常，退化成替换字符即可。"""
    res = FakeResponse(b"\xff\xfe\xd1\xe9", encoding="gbk", apparent="ascii")
    text = EPortalClient._response_text(res)
    assert isinstance(text, str)


# --------------------------------------------------------- query_services 解析


def _get_services_payload():
    return {
        "serviceContent": (
            "<div id='bch_service_0' onclick=\"selectService('校园网','校园网','0')\">校园网</div>"
            "<div id='bch_service_1' onclick=\"selectService('iSMU','i-SHMU','1')\">i-SHMU</div>"
            "<div id='bch_service_2' onclick=\"selectService('[-1-1]系统默认服务[-1-1]','系统默认服务','2')\">"
            "系统默认服务</div>"
        ),
        "defaultService": (
            "<div id=\"selectDisname\">请选择服务</div>"
            "<input name=\"net_access_type\" id=\"net_access_type\" value='' type=\"hidden\"/>"
        ),
        "serviceJson": "[]",
        "services": [],
        "typeflag": True,
        "isService": True,
    }


def test_query_services_parses_select_service_html():
    session = FakeSession(FakeResponse(json.dumps(_get_services_payload(), ensure_ascii=False).encode("utf-8")))
    client = EPortalClient(session)

    services = client.query_services("wlanuserip=x")

    assert services == [
        {"value": "校园网", "name": "校园网"},
        {"value": "iSMU", "name": "i-SHMU"},
        {"value": PLACEHOLDER_SERVICE_PREFIX + "系统默认服务" + PLACEHOLDER_SERVICE_PREFIX, "name": "系统默认服务"},
    ]


def test_query_services_reads_service_json():
    payload = {
        "serviceContent": "",
        "defaultService": "",
        "serviceJson": json.dumps([{"serviceName": "iSMU", "serviceShowName": "i-SHMU"}], ensure_ascii=False),
    }
    session = FakeSession(FakeResponse(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
    client = EPortalClient(session)

    assert client.query_services("qs") == [{"value": "iSMU", "name": "i-SHMU"}]


def test_query_services_returns_empty_on_bad_response():
    session = FakeSession(FakeResponse(b"<html>oops</html>"))
    client = EPortalClient(session)
    assert client.query_services("qs") == []


def test_query_account_service_decodes_chinese():
    """HAR 实测：网口环境返回「校园网」。"""
    session = FakeSession(FakeResponse("校园网".encode("utf-8")))
    client = EPortalClient(session)

    assert client.query_account_service("qs", "202540510004") == "校园网"

    method, url = session.requests_sent[-1]
    assert method == "POST"
    assert url.endswith("userV2.do?method=getServices")


def test_query_account_service_empty_without_username():
    session = FakeSession(FakeResponse(b""))
    client = EPortalClient(session)

    assert client.query_account_service("qs", "") == ""
    assert session.requests_sent == []


# ---------------------------------------------------------- service 候选列表


@pytest.mark.parametrize("name,provider", both_candidate_providers())
def test_candidates_cover_both_wired_and_wireless(name, provider, monkeypatch):
    """默认必须把两种接入类型都列为候选，且顺序稳定。"""
    monkeypatch.delenv("SHMTU_AUTH_PORTAL_SERVICE", raising=False)

    candidates = provider()._portal_service_candidates()

    assert [value for _, value in candidates] == [HAR_WIRED_SERVICE, HAR_WIRELESS_SERVICE], (
        "有线 / 无线都要试，不能只挑一个"
    )
    # 显示名只是给人看的，不该影响提交值
    assert [display for display, _ in candidates] == ["校园网(有线)", "iSMU(无线)"]


@pytest.mark.parametrize("name,provider", both_candidate_providers())
def test_candidates_never_depend_on_network_type(name, provider, monkeypatch):
    """回归：没有任何环境信息也照样给出两个候选（不猜网络类型）。"""
    monkeypatch.delenv("SHMTU_AUTH_PORTAL_SERVICE", raising=False)
    monkeypatch.delenv("SHMTU_AUTH_LOGIN_URL", raising=False)

    assert len(provider()._portal_service_candidates()) == 2


@pytest.mark.parametrize("name,provider", both_candidate_providers())
def test_config_pins_single_candidate(name, provider, monkeypatch):
    """配置项既然显式给了值，就只试它，不再兜到另一种类型。"""
    monkeypatch.setenv("SHMTU_AUTH_PORTAL_SERVICE", "iSMU")

    candidates = provider()._portal_service_candidates()

    assert candidates == [("iSMU", HAR_WIRELESS_SERVICE)]


@pytest.mark.parametrize("name,provider", both_candidate_providers())
def test_config_accepts_preencoded_value(name, provider, monkeypatch):
    monkeypatch.setenv("SHMTU_AUTH_PORTAL_SERVICE", HAR_WIRED_SERVICE)

    candidates = provider()._portal_service_candidates()

    assert candidates == [(HAR_WIRED_SERVICE, HAR_WIRED_SERVICE)]


@pytest.mark.parametrize("name,provider", both_candidate_providers())
def test_last_successful_service_is_tried_first(name, provider, monkeypatch):
    """上次成功的类型排到最前面（省一轮），但另一个候选仍然保留。"""
    monkeypatch.delenv("SHMTU_AUTH_PORTAL_SERVICE", raising=False)

    auth = provider()
    auth._last_ok_service = HAR_WIRELESS_SERVICE

    candidates = auth._portal_service_candidates()

    assert [value for _, value in candidates] == [HAR_WIRELESS_SERVICE, HAR_WIRED_SERVICE]


@pytest.mark.parametrize("name,provider", both_candidate_providers())
def test_unknown_memo_does_not_break_order(name, provider, monkeypatch):
    """记忆值不在候选里（例如上次用的是别的校区服务）时，顺序保持不变。"""
    monkeypatch.delenv("SHMTU_AUTH_PORTAL_SERVICE", raising=False)

    auth = provider()
    auth._last_ok_service = "%E6%A0%A1%E5%9B%AD%E7%BD%91-something-else"

    assert [value for _, value in auth._portal_service_candidates()] == [
        HAR_WIRED_SERVICE,
        HAR_WIRELESS_SERVICE,
    ]


# ------------------------------------------------------------- docker 一致性


def test_docker_service_candidates_match_main(monkeypatch):
    """同一组输入下，docker 版与主包的候选列表必须完全一致。"""
    monkeypatch.delenv("SHMTU_AUTH_PORTAL_SERVICE", raising=False)

    cases = [
        ("", ""),
        ("", HAR_WIRELESS_SERVICE),
        ("iSMU", ""),
        (HAR_WIRED_SERVICE, ""),
    ]

    with docker_app() as (_, auth_core, _, _):
        for config, memo in cases:
            if config:
                monkeypatch.setenv("SHMTU_AUTH_PORTAL_SERVICE", config)
            else:
                monkeypatch.delenv("SHMTU_AUTH_PORTAL_SERVICE", raising=False)

            main_auth = _new_main_auth()
            docker_auth = auth_core.HeadlessNetAuth()
            main_auth._last_ok_service = memo
            docker_auth._last_ok_service = memo

            assert (
                main_auth._portal_service_candidates() == docker_auth._portal_service_candidates()
            ), f"config={config!r} memo={memo!r} 结果不一致"


def test_docker_encode_service_param_matches_main():
    with docker_app() as (eportal_protocol, _, _, _):
        assert eportal_protocol.encode_service_param is not encode_service_param
        for value in ("校园网", "iSMU", HAR_WIRED_SERVICE, "", "  "):
            assert eportal_protocol.encode_service_param(value) == encode_service_param(value)


def test_docker_response_text_decodes_utf8():
    with docker_app() as (eportal_protocol, _, _, _):
        res = FakeResponse(VALID_CODE_ERROR_BODY.encode("utf-8"))
        decoded = eportal_protocol.EPortalClient._response_text(res)
        assert json.loads(decoded)["message"] == "验证码错误."


def test_docker_service_type_constants():
    with docker_app() as (eportal_protocol, auth_core, _, _):
        from shmtu_auth.src.core.shmtu_auth_const_value import ServiceType

        assert auth_core.ServiceType.EDU == ServiceType.EDU
        assert auth_core.ServiceType.ISMU == ServiceType.ISMU
        # 常量本身就是「编码一次」的形态，正好等于 quote 原始名
        assert auth_core.ServiceType.EDU == encode_service_param("校园网")
        # 占位前缀两处必须一致
        assert eportal_protocol.PLACEHOLDER_SERVICE_PREFIX == PLACEHOLDER_SERVICE_PREFIX
