import threading
from time import sleep as time_sleep
from typing import List

from shmtu_auth.src.core.core_exp import (
    check_is_connected_retry as check_is_connected_with_retry,
)
from shmtu_auth.src.core.shmtu_auth import ShmtuNetAuth
from shmtu_auth.src.datatype.shmtu.auth.auth_user import UserItem, get_valid_user_list
from shmtu_auth.src.gui.common.captcha_bridge import get_captcha_bridge
from shmtu_auth.src.gui.common.signal_bus import (
    auth_attempt,
    auth_failed,
    auth_status_changed,
    auth_success,
    auth_thread_started,
    auth_thread_stopped,
    log_new,
)


class AuthThread(threading.Thread):
    need_work = True

    user_list: List[UserItem]

    check_internet_interval: int

    check_internet_retry_times: int
    check_internet_retry_wait_time: int

    shmtu_auth_obj: ShmtuNetAuth

    def __init__(
        self,
        user_list: List[UserItem] = None,
        check_internet_interval: int = 60,
        check_internet_retry_times: int = 1,
        check_internet_retry_wait_time: int = 3,
    ):
        super().__init__()

        self.user_list = user_list
        if self.user_list is None:
            self.user_list = []
        self.user_list = get_valid_user_list(self.user_list)

        self.check_internet_interval = check_internet_interval

        self.check_internet_retry_times = check_internet_retry_times
        self.check_internet_retry_wait_time = check_internet_retry_wait_time

        self.shmtu_auth_obj = ShmtuNetAuth()

        # 验证码兜底：OCR 识别不出来时弹窗请用户手输。
        # 这一步在 GUI 线程执行（AuthThread 由界面创建），桥接对象因此亲和于 GUI 线程。
        self.captcha_bridge = get_captcha_bridge()

    def _interruptible_sleep(self, seconds: int) -> None:
        """可中断睡眠：按 1 秒分片睡，stop() 后尽快返回。"""
        for _ in range(max(1, int(seconds))):
            if not self.need_work:
                return
            time_sleep(1)

    def main_loop(self):
        # 检查状态：单次探测，失败直接进登录 —— 登录才是最可靠的复核
        # （门户的响应比任何探测都权威），在探测上反复重试只是把修复往后推。
        network_status = check_is_connected_with_retry(retry_times=1, wait_time=0)
        # 阻塞任务后必须检查是否需要继续工作
        if not self.need_work:
            return

        # 更新状态
        if self.shmtu_auth_obj.isLogin != network_status:
            if network_status:
                log_new("Auth", "检测到网络已连接(状态变动)")
                auth_status_changed(True)
            else:
                log_new("Auth", "检测到网络未连接(状态变动)")
                auth_status_changed(False)

        self.shmtu_auth_obj.isLogin = network_status

        # 如果已经认证，直接跳过后续操作
        if network_status:
            return

        # 这里没有认证，因此要进行认证。
        #
        # 「重试次数 / 重试间隔」两个滑块管的是**登录失败后**的重试轮数，
        # 不再是登录前的探测重试：探测失败 1 次就该去登录，门户的响应比
        # 任何探测都权威；全部账号试过仍失败时，等一个间隔再来一轮
        # （重试前先快速复核连通性，网络自己恢复的话就不打扰门户了）。
        retry_rounds = max(1, self.check_internet_retry_times)
        for round_index in range(1, retry_rounds + 1):
            if not self.need_work:
                return

            if round_index > 1:
                if check_is_connected_with_retry(retry_times=1, wait_time=0, force=True):
                    log_new("Auth", "重试前检测到网络已连接，跳过登录")
                    auth_status_changed(True)
                    self.shmtu_auth_obj.isLogin = True
                    return
                log_new("Auth", f"登录失败，开始第 {round_index}/{retry_rounds} 轮重试")

            login_ok = False
            for user in self.user_list:
                # 实例化时已经过滤过了，按理来说这句话没啥用~
                if not user.is_valid():
                    continue
                if not self.need_work:
                    return

                # 发送认证尝试信号
                auth_attempt(user.user_id)

                login_result = self.shmtu_auth_obj.login(
                    user.user_id,
                    user.password,
                    user.is_encrypted,
                    skip_network_check=True,
                    captcha_provider=self.captcha_bridge.ask,
                )

                if login_result[0]:  # 登录成功
                    auth_success(user.user_id)
                    login_ok = True
                    break
                else:  # 登录失败
                    error_msg = login_result[1] if len(login_result) > 1 else "未知错误"
                    auth_failed(user.user_id, error_msg)

            if login_ok:
                return

            # 中间轮失败才等一个重试间隔；最后一轮失败交给外层按
            # check_internet_interval 睡，避免无意义地多等一次
            if round_index < retry_rounds:
                self._interruptible_sleep(self.check_internet_retry_wait_time)

    def run(self):
        if self.user_list is None or len(self.user_list) == 0:
            return

        # 发送线程启动信号
        auth_thread_started()

        while self.need_work:
            # main_loop 里的任何意外（门户请求、登录流程）都不允许静默杀死
            # 线程 —— 否则 auth_thread_stopped 永远不会发出，界面还显示
            # 「监控中」，实际已经死了。
            try:
                self.main_loop()
            except Exception as e:  # noqa: BLE001
                log_new("Auth", f"认证线程主循环异常（继续运行）: {e}")

            self._interruptible_sleep(self.check_internet_interval)

        # 发送线程停止信号
        auth_thread_stopped()

    def stop(self):
        self.need_work = False

    def check_is_stop(self):
        return self.is_alive()
