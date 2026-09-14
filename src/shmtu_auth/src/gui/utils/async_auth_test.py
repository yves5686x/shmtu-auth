"""「手动测试认证服务」用的一次性认证测试。

界面上的「手动测试」原先只能测**网络连通性**；账号密码对不对、验证码能不能过、
接入服务选得对不对，只能等后台认证线程按周期跑，用户拿不到即时结论。
这里补一个只跑一次的认证测试，跑完立刻把结果交回界面。

为什么必须放在后台线程：核心的 ``ShmtuNetAuth.login()`` 是阻塞的
（建会话 → pageInfo → 取验证码 → OCR → RSA 加密 → POST，验证码最多重试 6 次），
直接放在界面线程会把窗口冻住好几秒。

验证码人工兜底依然可用：``captcha_bridge`` 用 BlockingQueuedConnection 把
「请用户输入验证码」投递到 GUI 线程执行并阻塞本线程等待结果，
所以在 QThread 里调用是安全的（见 captcha_bridge 模块说明）。
"""

from typing import List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from shmtu_auth.src.datatype.shmtu.auth.auth_user import UserItem
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()


class SingleAuthTestWorker(QThread):
    """用第一个可用账号执行一次认证，结束后发出结果信号。"""

    # (是否成功, 用户ID, 提示信息)
    test_finished = Signal(bool, str, str)

    def __init__(self, user_list: Optional[List[UserItem]] = None, parent=None):
        super().__init__(parent)
        self.user_list: List[UserItem] = list(user_list or [])
        self._cancelled = False

    def cancel(self):
        """请求取消：登录本身无法中途打断，但结束后不再发信号。"""
        self._cancelled = True

    def run(self):
        try:
            success, user_id, message = self._run_once()
        except Exception as e:  # 绝不让异常把线程带崩
            logger.exception("手动认证测试异常")
            success, user_id, message = False, "", f"测试过程中出现异常：{e}"

        if self._cancelled:
            logger.info("手动认证测试已取消，丢弃结果")
            return

        self.test_finished.emit(success, user_id, message)

    def _run_once(self) -> Tuple[bool, str, str]:
        from shmtu_auth.src.core.shmtu_auth import ShmtuNetAuth
        from shmtu_auth.src.datatype.shmtu.auth.auth_user import get_valid_user_list
        from shmtu_auth.src.gui.common.captcha_bridge import get_captcha_bridge
        from shmtu_auth.src.gui.common.credential_bridge import (
            fetch_service_users,
            merge_service_users,
        )

        # 与后台认证线程保持一致的取号顺序：自建凭据服务的账号优先，再回退界面里的
        service_users = fetch_service_users()
        if service_users:
            logger.info(f"手动认证测试：凭据服务提供 {len(service_users)} 个账号，将优先使用")

        merged_users = merge_service_users(self.user_list, service_users)
        valid_users = get_valid_user_list(merged_users)

        if not valid_users:
            return False, "", "没有可用的账号，请先在「用户列表」里添加"

        # 只测第一个 —— 这正是后台认证线程会最先尝试的那个
        user = valid_users[0]

        logger.info(f"手动认证测试开始：用户 {user.user_id}")
        shmtu_auth = ShmtuNetAuth()
        result = shmtu_auth.login(
            user.user_id,
            user.password,
            user.is_encrypted,
            skip_network_check=True,
            captcha_provider=get_captcha_bridge().ask,
        )

        success = bool(result[0]) if result else False
        message = result[1] if len(result) > 1 else ""
        logger.info(f"手动认证测试结束：用户 {user.user_id} 结果 {success} - {message}")

        return success, user.user_id, message
