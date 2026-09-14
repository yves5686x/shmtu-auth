from shmtu_auth.src.core.get_query_string_requests import (
    get_query_string_by_url,
    is_connect_by_sites,
)
from shmtu_auth.src.core.query_string import handle_query_string
from shmtu_auth.src.core.shmtu_auth_const_value import get_default_query_string
from shmtu_auth.src.utils.env import get_env_str
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 手动指定 queryString 的配置项。
#
# 自动探测的前提是「网关会劫持 http 明文请求」，但这在部分环境下就是不生效：
# 机器设了代理、有多张网卡、网关换了策略、或者接入方式不同。
# 这时把浏览器跳转后地址栏里的完整认证页 URL 粘进来即可，不依赖探测。
MANUAL_QUERY_STRING_ENV = "SHMTU_AUTH_QUERY_STRING"


def get_manual_query_string() -> str:
    """读取手动指定的 queryString（完整认证页 URL 或裸 queryString 都接受）。"""
    return (get_env_str(MANUAL_QUERY_STRING_ENV, default="") or "").strip()


def check_is_connected(force: bool = False) -> bool:
    """检测是否已联网。

    :param force: 跳过探测结果的 TTL 缓存强制重新探测（复核刚做过的动作时用）
    """
    return is_connect_by_sites(force=force)


def check_is_connected_retry(
    retry_times: int = 3,
    wait_time: int = 5,
    force: bool = False,
) -> bool:
    # Keep signature for compatibility, but do a single fast probe without retry/wait.
    _ = retry_times
    _ = wait_time
    return check_is_connected(force=force)


def get_query_string(skip_connectivity_check: bool = False) -> str:
    """获取认证URL和query string。

    Args:
        skip_connectivity_check: 是否跳过网络连通性检测（外部已检测过时设为True）

    Returns:
        格式: 'portal_url|query_string' 或者只返回 query_string(兼容旧逻辑)
    """
    # 手动指定的优先级最高，配了就完全跳过自动探测。
    # 接受的两种写法：
    #   1. 完整认证页 URL：https://ismu.shmtu.edu.cn:8443/eportal/index.jsp?wlanuserip=...
    #   2. 裸 queryString：wlanuserip=...&mac=...&t=wireless-v2
    manual = get_manual_query_string()
    if manual:
        logger.info(f"使用手动指定的 {MANUAL_QUERY_STRING_ENV}，跳过自动探测")
        return manual

    try_str: str = get_query_string_by_url(skip_connectivity_check=skip_connectivity_check).strip()

    try_str = handle_query_string(try_str)

    if len(try_str) > 0:
        return try_str
    else:
        return get_default_query_string().strip()
