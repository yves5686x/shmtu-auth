"""把「自建凭据服务」的账号接进 GUI 的认证流程。

CLI / Docker 走的是 ``program_env_config.get_user_list()``：先按**物理网卡 MAC**
问自建凭据服务换账号，拿不到再回退 ``config.toml`` 里的本地账号。

GUI 的账号来自界面（持久化在 ``data/user_list.pickle``），不走那条链路，
所以这里补一个语义一致的入口，让 GUI 也能用上自建凭据服务：

* 拿到服务账号 → 排在最前，认证时优先尝试
* 服务没配 / 挂了 / 没这台设备的记录 → 原样用界面里的账号，行为不变

服务账号刻意**不写回**界面共享的用户列表，免得混进 pickle 里
（服务端换了账号，本地会留下过期副本）。
"""

from typing import List

from shmtu_auth.src.datatype.shmtu.auth.auth_user import UserItem
from shmtu_auth.src.utils.logs import get_logger
from shmtu_auth.src.utils.program_env_config import get_user_list_from_service

logger = get_logger()


def fetch_service_users() -> List[UserItem]:
    """向自建凭据服务要这台设备的账号。

    返回空列表表示「没有可用结果」——没配 ``SHMTU_AUTH_CREDENTIAL_URL``、
    请求失败、服务端返回空，统统归到这里，调用方按「回退本地」处理即可。
    """
    try:
        remote_list = get_user_list_from_service()
    except Exception as e:
        # get_user_list_from_service 内部已经处理了常规失败；
        # 这里再兜一层意外异常，绝不让凭据服务把认证流程带崩。
        logger.warning(f"Credential service raised: {e}")
        return []

    if not remote_list:
        return []

    users: List[UserItem] = []
    for entry in remote_list:
        # get_user_list_from_service() 返回 (user_id, password, is_encrypt)
        user_id, password, is_encrypt = entry
        item = UserItem(user_id=user_id, password=password)
        item.is_encrypted = bool(is_encrypt)
        users.append(item)

    return users


def merge_service_users(local_users: List[UserItem], service_users: List[UserItem]) -> List[UserItem]:
    """服务账号放最前，学号重复的只留一个（服务那份优先）。

    返回新列表，不改动传入的 ``local_users``。
    """
    merged: List[UserItem] = []
    seen = set()

    for item in list(service_users or []) + list(local_users or []):
        user_id = (item.user_id or "").strip()
        if user_id:
            if user_id in seen:
                continue
            seen.add(user_id)
        merged.append(item)

    return merged
