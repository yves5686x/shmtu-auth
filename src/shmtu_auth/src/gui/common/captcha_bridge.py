"""验证码人工输入请求的跨线程桥接。

认证逻辑跑在 :class:`~shmtu_auth.src.gui.feature.network_auth.AuthThread`
（普通 Python 后台线程）里，而 Qt 控件只能在 GUI 线程创建和操作。

这里用 ``Qt.ConnectionType.BlockingQueuedConnection`` 把「请用户输入验证码」的
请求投递到 GUI 线程执行，并**阻塞后台线程**直到用户填完或取消 —— 这样才能把一个
同步的 ``(image: bytes) -> str | None`` 回调交给核心层的 ``captcha_provider``。
"""

from typing import Optional

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal, Slot

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()


class CaptchaBridge(QObject):
    """把验证码输入请求从认证线程搬到 GUI 线程。"""

    # 故意不带参数：图片通过成员变量传递，避免 Qt 跨线程排队时对 bytes 做额外封送。
    # 阻塞连接保证了 emit 之前写入的 _pending_image 对槽函数可见（happens-before）。
    _request = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._pending_image: bytes = b""
        self._code: Optional[str] = None

        # 接收者 self 由 GUI 线程创建、亲和于 GUI 线程，所以槽会在 GUI 线程执行，
        # 且 emit 的调用方会被阻塞到槽返回。
        self._request.connect(self._handle_request, Qt.ConnectionType.BlockingQueuedConnection)

    def ask(self, image: bytes) -> Optional[str]:
        """在认证线程调用，返回用户输入的验证码；取消或异常返回 ``None``。"""
        if not image:
            return None

        app = QCoreApplication.instance()
        if app is None:
            logger.warning("Qt application is not running, cannot ask user for captcha")
            return None

        if QThread.currentThread() is app.thread():
            # 已经身处 GUI 线程，直接调用；若还走 BlockingQueuedConnection 会自锁
            return self._handle_request()

        self._pending_image = image
        self._code = None
        self._request.emit()
        return self._code

    @Slot()
    def _handle_request(self) -> Optional[str]:
        # 延迟导入，避免无 GUI 场景（CLI/Docker）import 本模块时把 Qt 控件拉进来
        from shmtu_auth.src.gui.view.components.custom.captcha_message_box import (
            ask_captcha_code,
        )

        self._code = ask_captcha_code(self._pending_image)
        self._pending_image = b""
        return self._code


# 认证线程与 GUI 共用的单例；在 GUI 线程首次 import/创建时才绑定线程亲和性
_bridge: Optional[CaptchaBridge] = None


def get_captcha_bridge() -> CaptchaBridge:
    """获取（或惰性创建）全局验证码桥接。**必须在 GUI 线程调用。**"""
    global _bridge
    if _bridge is None:
        _bridge = CaptchaBridge()
    return _bridge


def ask_captcha_code(image: bytes) -> Optional[str]:
    """供核心层直接使用的 ``captcha_provider`` 回调。"""
    return get_captcha_bridge().ask(image)
