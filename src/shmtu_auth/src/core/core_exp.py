from time import sleep as time_sleep

from shmtu_auth.src.core.get_query_string_requests import (
    get_query_string_by_url,
    is_connect_by_sites,
)
from shmtu_auth.src.core.shmtu_auth_const_value import get_default_query_string
from shmtu_auth.src.utils.env import get_env_str
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 手动指定 queryString 的配置项。
#
# 自动探测的前提是「网关会劫持 http 明文请求」，但这在部分环境下就是不生效：
# 机器设了代理、有多张网卡、网关换了策略、或者接入方式不同。
# 这时把浏览器跳转后地址栏里的完整认证页 URL 粘进来即可。
#
# 优先级：**自动探测优先，这里只是兜底**。探测每次拿的都是当前网络的实时
# 参数（queryString 里有一次性时间戳 t= 和 mac 绑定，换网络/过一段时间
# 就失效），常驻进程拿手动串当主力会在第一次失效后永久坏掉且难以排查；
# 而探测失败的环境恰恰是手动串唯一能起作用的地方。
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
    """探测是否已联网；第一次没探通时按 ``retry_times`` / ``wait_time`` 再试。

    这两个参数原先是被直接丢掉的（函数体里只有一句 ``return check_is_connected()``），
    于是界面上「联网失败的重试次数 / 重试等待时间」两个滑块改了完全没有效果 ——
    现在按字面生效。

    重试时一定传 ``force=True``：连通性结论有短 TTL 缓存，不强制刷新的话
    「重试」只会把刚缓存下来的那个「不通」再读一遍，等于没重试。

    ⚠️ 调用方注意：这个函数会 ``sleep``。**界面线程不要直接用**
    （``retry_times=1`` 时不 sleep，是安全的）。
    """
    attempts = max(1, int(retry_times))

    for attempt in range(attempts):
        if check_is_connected(force=force or attempt > 0):
            if attempt > 0:
                logger.info(f"联网探测第 {attempt + 1} 次才成功")
            return True

        if attempt < attempts - 1 and wait_time > 0:
            logger.info(f"联网探测未通过，{wait_time} 秒后重试（第 {attempt + 2}/{attempts} 次）")
            time_sleep(wait_time)

    return False


def get_query_string(skip_connectivity_check: bool = False) -> str:
    """获取认证URL和query string。

    Args:
        skip_connectivity_check: 是否跳过网络连通性检测（外部已检测过时设为True）

    Returns:
        格式: 'portal_url|query_string' 或者只返回 query_string(兼容旧逻辑)
    """
    # 自动探测优先：探测拿到的 queryString 是当前网络的实时参数，永不过期。
    try_str: str = get_query_string_by_url(skip_connectivity_check=skip_connectivity_check).strip()

    if len(try_str) > 0:
        return try_str

    # 探测没抓到（网关不劫持明文请求 / 代理 / 多网卡等环境），退回手动指定。
    # 接受的两种写法：
    #   1. 完整认证页 URL：https://ismu.shmtu.edu.cn:8443/eportal/index.jsp?wlanuserip=...
    #   2. 裸 queryString：wlanuserip=...&mac=...&t=wireless-v2
    manual = get_manual_query_string()
    if manual:
        logger.warning(
            f"自动探测未抓到 queryString，使用手动指定的 {MANUAL_QUERY_STRING_ENV}。"
            "注意手动串有时效性（t=/mac），失效后需要重新从浏览器复制"
        )
        return manual

    return get_default_query_string().strip()
