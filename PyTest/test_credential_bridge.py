"""自建凭据服务接入 GUI 的桥接逻辑单测。

重点守住两件事：

1. 服务能用时，服务账号排在最前（优先尝试）
2. 服务不能用时，**界面里的账号原封不动** —— 绝不因为服务挂了就认证不了
"""

from shmtu_auth.src.datatype.shmtu.auth.auth_user import UserItem
from shmtu_auth.src.gui.common import credential_bridge


def test_fetch_returns_empty_when_service_not_configured(monkeypatch):
    """没配 SHMTU_AUTH_CREDENTIAL_URL 时返回空列表，不算错误。"""
    monkeypatch.setattr(credential_bridge, "get_user_list_from_service", lambda: [])
    assert credential_bridge.fetch_service_users() == []


def test_fetch_converts_service_payload_to_user_items(monkeypatch):
    monkeypatch.setattr(
        credential_bridge,
        "get_user_list_from_service",
        lambda: [("202500000001", "pwd-1", False), ("202500000002", "pwd-2", True)],
    )

    users = credential_bridge.fetch_service_users()

    assert [u.user_id for u in users] == ["202500000001", "202500000002"]
    assert [u.password for u in users] == ["pwd-1", "pwd-2"]
    assert [u.is_encrypted for u in users] == [False, True]


def test_fetch_swallows_unexpected_exception(monkeypatch):
    """服务端炸了也不能把认证流程带崩，退化成空列表即可。"""

    def boom():
        raise RuntimeError("network exploded")

    monkeypatch.setattr(credential_bridge, "get_user_list_from_service", boom)

    assert credential_bridge.fetch_service_users() == []


def test_service_users_pass_user_item_validation():
    """服务账号必须能通过 is_valid()，否则会被认证流程静默丢掉。"""
    item = UserItem(user_id="202500000001", password="pwd")
    assert item.is_valid()


def test_merge_puts_service_users_first():
    local = [UserItem(user_id="202500000001", password="local")]
    service = [UserItem(user_id="202500000009", password="from-service")]

    merged = credential_bridge.merge_service_users(local, service)

    assert [u.user_id for u in merged] == ["202500000009", "202500000001"]
    assert merged[0].password == "from-service"


def test_merge_dedupes_keeping_service_version():
    """同一学号同时存在时，服务那份优先（服务是权威来源）。"""
    local = [UserItem(user_id="202500000001", password="old")]
    service = [UserItem(user_id="202500000001", password="new")]

    merged = credential_bridge.merge_service_users(local, service)

    assert len(merged) == 1
    assert merged[0].password == "new"


def test_merge_falls_back_to_local_when_service_empty():
    """服务没给东西时，界面里的账号原样保留。"""
    local = [UserItem(user_id="202500000001", password="local")]

    merged = credential_bridge.merge_service_users(local, [])

    assert merged == local


def test_merge_does_not_mutate_local_list():
    """合并返回新列表，不能改到界面共享的那份。"""
    local = [UserItem(user_id="202500000001", password="local")]
    service = [UserItem(user_id="202500000009", password="svc")]

    credential_bridge.merge_service_users(local, service)

    assert len(local) == 1
    assert local[0].user_id == "202500000001"


def test_merge_handles_none_inputs():
    assert credential_bridge.merge_service_users(None, None) == []


def test_merge_keeps_local_order_after_service_block():
    """本地账号之间的相对顺序不变，只把服务账号整体挪到最前。"""
    local = [
        UserItem(user_id="202500000001", password="a"),
        UserItem(user_id="202500000002", password="b"),
    ]
    service = [UserItem(user_id="202500000009", password="svc")]

    merged = credential_bridge.merge_service_users(local, service)

    assert [u.user_id for u in merged] == [
        "202500000009",
        "202500000001",
        "202500000002",
    ]
