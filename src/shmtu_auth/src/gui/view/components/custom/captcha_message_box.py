"""验证码人工输入对话框。

门户改版后每次登录都要求 4 位图形验证码。正常情况下由 OCR 自动识别
（见 ``shmtu_auth.src.core.captcha_solver``），只有识别不出来时才会走到这里，
请用户看着图片手输一次。
"""

from typing import Optional

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import QLabel
from qfluentwidgets import LineEdit, MessageBoxBase, SubtitleLabel

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 门户验证码固定 4 位数字
CAPTCHA_LENGTH = 4


class CaptchaMessageBox(MessageBoxBase):
    """展示验证码图片并收集用户输入。"""

    def __init__(self, image: bytes, parent=None, title: str = "请输入验证码"):
        super().__init__(parent)

        self.titleLabel = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.titleLabel)

        self.viewLayout.addWidget(
            QLabel("自动识别失败，请照着下面的图片输入 4 位数字：", self)
        )

        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap()
        if pixmap.loadFromData(image):
            # 原图只有 80×30，放大一点方便看清
            self.image_label.setPixmap(
                pixmap.scaled(
                    pixmap.width() * 2,
                    pixmap.height() * 2,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.image_label.setText("验证码图片加载失败")
        self.viewLayout.addWidget(self.image_label)

        self.code_line_edit = LineEdit(self)
        self.code_line_edit.setPlaceholderText(f"请输入 {CAPTCHA_LENGTH} 位数字")
        self.code_line_edit.setClearButtonEnabled(True)
        # 用正则而不是 QIntValidator：验证码可能出现 "0012" 这种前导零，
        # QIntValidator 会把它当成非法输入挡掉。
        self.code_line_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\d{0,4}"), self)
        )
        self.code_line_edit.setMaxLength(CAPTCHA_LENGTH)
        self.code_line_edit.textChanged.connect(self.__on_text_changed)
        self.viewLayout.addWidget(self.code_line_edit)

        # 输入位数不够时不允许点「确定」
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.yesButton.setEnabled(False)

        self.widget.setMinimumWidth(360)
        self.code_line_edit.setFocus()

    def __on_text_changed(self, text: str) -> None:
        self.yesButton.setEnabled(len(text.strip()) == CAPTCHA_LENGTH)

    def validate(self) -> bool:
        """MessageBoxBase 在点「确定」时调用；返回 False 则不关闭对话框。"""
        return len(self.code_line_edit.text().strip()) == CAPTCHA_LENGTH

    def get_code(self) -> Optional[str]:
        code = self.code_line_edit.text().strip()
        if len(code) != CAPTCHA_LENGTH:
            return None
        return code


def ask_captcha_code(image: bytes, parent=None) -> Optional[str]:
    """在 GUI 线程弹出验证码输入框。

    :param image: 验证码图片字节
    :param parent: 父窗口，通常传 ``QApplication.activeWindow()``
    :return: 用户输入的 4 位验证码；取消或图片异常返回 ``None``
    """
    if not image:
        return None

    if parent is None:
        try:
            from PySide6.QtWidgets import QApplication

            parent = QApplication.activeWindow()
        except Exception as e:  # pragma: no cover - 仅防御 Qt 未初始化
            logger.warning(f"Failed to resolve active window for captcha dialog: {e}")
            return None

    box = CaptchaMessageBox(image, parent)
    if not box.exec():
        logger.info("Captcha input cancelled by user")
        return None

    code = box.get_code()
    if code:
        logger.info("Captcha provided by user input")
    return code
