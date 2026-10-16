from time import sleep as time_sleep

from shmtu_auth.src.core.shmtu_auth import ShmtuNetAuth
from shmtu_auth.src.core.core_exp import check_is_connected
from shmtu_auth.src.utils.env import get_env_int
from shmtu_auth.src.utils.logs import get_logger
from shmtu_auth.src.utils.program_env_config import (
    convert_number_to_star,
    convert_password_to_star,
    get_user_list,
)

logger = get_logger()

# 检测时间间隔，单位：秒
time_interval = 10

env_time_interval = get_env_int("SHMTU_AUTH_TIME_INTERVAL", -1)
if env_time_interval > 0:
    time_interval = env_time_interval


def monitor_auth(user_list_3=None, *, wait_for_users=False, on_status=None, wake_event=None):
    def report(phase, message):
        if on_status is not None:
            on_status(phase, message)

    def pause():
        if wake_event is None:
            time_sleep(time_interval)
        else:
            wake_event.wait(time_interval)
            wake_event.clear()

    logger.info("Initializing...")
    net_auth = ShmtuNetAuth()

    logger.info("Reading user information...")
    if user_list_3 is None:
        user_list_3 = get_user_list()

    if len(user_list_3) == 0 and not wait_for_users:
        logger.error("No user information found.")
        return

    user_count = len(user_list_3)
    logger.info(f"Found {user_count} user:")
    for i in range(user_count):
        user = user_list_3[i]
        user_name = convert_number_to_star(user[0])
        password = convert_password_to_star(user[1])
        logger.info(f"[{i + 1}]User: {user_name}, Password: {password}")

    logger.info("Auth status monitor started.")

    while True:
        if wait_for_users:
            # 网页模式允许首次未配置以及清空账号；保存后立即唤醒本循环。
            user_list_3 = get_user_list()
            if not user_list_3:
                report("waiting", "等待在网页中添加账号")
                pause()
                continue
        report("checking", "正在检查网络")
        logger.info("Checking network status...")
        is_online = check_is_connected()
        if not is_online:
            report("authenticating", "正在尝试认证")
            logger.info("Network offline, trying to login...")

            # 登录前重取一次账号列表：凭据服务的账号可能已轮换（TTL 过期 / 停用），
            # 常驻进程若永远用启动时那份列表，轮换后会拿旧账号无限重试。
            # 本地环境变量配置时这只是一次 env 读取；凭据服务自带 TTL 缓存，不贵。
            # 失败不致命：列表为空就沿用现有列表。
            # 注意这里不 try 探测本身 —— check_is_connected 抛异常要炸出来（有
            # 回归测试钉住这个语义，见 PyTest/test_monitor_lifecycle.py）。
            try:
                refreshed_users = get_user_list()
                if len(refreshed_users) > 0 or wait_for_users:
                    user_list_3 = refreshed_users
            except Exception as e:  # noqa: BLE001
                logger.warning(f"刷新账号列表失败，沿用现有列表: {e}")

            if wait_for_users and not user_list_3:
                report("waiting", "等待在网页中添加账号")
                pause()
                continue
            try:
                if net_auth.login_by_list(user_list_3):
                    logger.info("Login success.")
                    report("online", "认证成功，网络已连接")
                else:
                    logger.error("Login failed.")
                    report("offline", "本轮认证未成功，将自动重试；详情请查看容器日志")
            except Exception as e:  # noqa: BLE001
                # 登录流程里的任何意外（门户响应解析、凭据服务等）都不允许
                # 杀死监控循环 —— 常驻工具的全部价值就在「下一轮还能再试」。
                # 探测、sleep 等环节的异常仍然穿透，让编程错误保持可见。
                logger.exception(f"Login flow crashed unexpectedly: {e}")
                report("error", "认证出现异常，将自动重试")
        else:
            logger.info("Network is online, no action needed.")
            report("online", "网络已连接")

        logger.info(f"Sleeping for {time_interval} seconds...")
        pause()


def start_monitor_auth():
    """CLI 在主线程持续监控，避免入口返回后解释器提前关闭线程池。"""
    users = get_user_list()
    if not users:
        logger.error("No valid accounts configured. Set SHMTU_AUTH_USER_LIST and matching passwords.")
        raise SystemExit(1)

    monitor_auth(users)
