"""门户 service 解析、响应编码与验证码判定的单测。

这里的期望值全部来自真实的门户抓包（``校园网登录网口流程.har`` /
``校园网登录ISMU流程.har``），因此可以直接证伪「提交的 service 和浏览器不一致」
这类回归。

同时覆盖一个曾经真实存在过的 bug：门户响应 ``Content-Type: text/html`` 不带
charset，requests 会退回 ISO-8859-1，把 ``验证码错误.`` 解成乱码，导致验证码错误
既识别不出来、也不会重试。
"""

import json
from urllib.parse import urlencode

import pytest

from _portal_test_utils import FakeResponse, FakeSession, StubClient, docker_app

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


def both_service_resolvers():
    """返回 [(名称, resolve 函数), ...]，主包与 docker 两份一起参数化。"""
    from shmtu_auth.src.core.core import ShmtuNetAuthCore

    resolvers = [
        ("main", lambda client, qs, user: ShmtuNetAuthCore._resolve_portal_service(None, client, qs, user))
    ]

    with docker_app() as (_, auth_core, _, _):
        resolvers.append(
            ("docker", lambda client, qs, user: auth_core.HeadlessNetAuth._resolve_portal_service(None, client, qs, user))
        )
    return resolvers


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


# ------------------------------------------------------- service 取值优先级


@pytest.mark.parametrize("name,resolve", both_service_resolvers())
@pytest.mark.parametrize(
    "account_service,expected",
    [("校园网", HAR_WIRED_SERVICE), ("iSMU", HAR_WIRELESS_SERVICE)],
)
def test_account_bound_service_is_authoritative(name, resolve, account_service, expected):
    """HAR 实测：账号绑定服务会随网络环境返回校园网 / iSMU，且需编码一次。"""
    client = StubClient(account_service=account_service)

    assert resolve(client, "query-string", "202540510004") == expected
    assert client.account_calls == [("query-string", "202540510004")]


@pytest.mark.parametrize("name,resolve", both_service_resolvers())
def test_dropdown_option_used_when_account_lookup_empty(name, resolve):
    client = StubClient(
        account_service="",
        options=[{"value": "校园网", "name": "校园网"}, {"value": "iSMU", "name": "i-SHMU"}],
    )

    assert resolve(client, "qs", "u") == HAR_WIRED_SERVICE


@pytest.mark.parametrize("name,resolve", both_service_resolvers())
def test_placeholder_option_does_not_win(name, resolve):
    """本校门户未登录时只下发「系统默认服务」占位项，不能被当成真实服务。"""
    client = StubClient(
        account_service="",
        options=[{"value": PLACEHOLDER_SERVICE_PREFIX + "系统默认服务" + PLACEHOLDER_SERVICE_PREFIX,
                  "name": "系统默认服务"}],
    )

    from shmtu_auth.src.core.shmtu_auth_const_value import ServiceType

    assert resolve(client, "qs", "u") == ServiceType.EDU


@pytest.mark.parametrize("name,resolve", both_service_resolvers())
def test_config_overrides_auto_detection(name, resolve, monkeypatch):
    monkeypatch.setenv("SHMTU_AUTH_PORTAL_SERVICE", "iSMU")
    # 配置项既然给了值，就不该再去问门户
    client = StubClient(account_service="校园网")

    assert resolve(client, "qs", "u") == "iSMU"
    assert client.account_calls == []


@pytest.mark.parametrize("name,resolve", both_service_resolvers())
def test_config_accepts_preencoded_value(name, resolve, monkeypatch):
    monkeypatch.setenv("SHMTU_AUTH_PORTAL_SERVICE", HAR_WIRED_SERVICE)
    client = StubClient()

    assert resolve(client, "qs", "u") == HAR_WIRED_SERVICE


# ------------------------------------------------------------- docker 一致性


def test_docker_service_resolver_matches_main(monkeypatch):
    """同一组输入下，docker 版与主包的解析结果必须完全一致。"""
    from shmtu_auth.src.core.core import ShmtuNetAuthCore

    cases = [
        (StubClient(account_service="校园网"), "qs", "u"),
        (StubClient(account_service="iSMU"), "qs", "u"),
        (StubClient(account_service="", options=[{"value": "校园网", "name": "校园网"}]), "qs", "u"),
        (StubClient(account_service=""), "qs", "u"),
    ]

    with docker_app() as (_, auth_core, _, _):
        for client, query_string, user in cases:
            main_result = ShmtuNetAuthCore._resolve_portal_service(None, client, query_string, user)
            docker_result = auth_core.HeadlessNetAuth._resolve_portal_service(None, client, query_string, user)
            assert main_result == docker_result, f"client={client.__dict__} 结果不一致"


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
