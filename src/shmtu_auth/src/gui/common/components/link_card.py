from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, IconWidget, SingleDirectionScrollArea, TextWrap

from shmtu_auth.src.gui.common.icon_cache import scaled_icon
from shmtu_auth.src.gui.common.scroll_tuning import prefer_time_based_smooth_scroll
from shmtu_auth.src.gui.common.style_sheet import StyleSheet


class LinkCard(QFrame):
    def __init__(self, icon, title, content, url, parent=None):
        super().__init__(parent=parent)
        self.url = QUrl(url)
        self.setFixedSize(198, 220)
        # 同 SampleCard：大图资源先按显示尺寸缩好，避免每帧重缩放
        self.iconWidget = IconWidget(scaled_icon(icon, 54), self)
        self.titleLabel = QLabel(title, self)
        self.contentLabel = QLabel(TextWrap.wrap(content, 28, False)[0], self)
        self.urlWidget = IconWidget(FluentIcon.LINK, self)

        self.__init_widget()

    def __init_widget(self):
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.iconWidget.setFixedSize(54, 54)
        self.urlWidget.setFixedSize(16, 16)

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.setContentsMargins(24, 24, 0, 13)
        self.vBoxLayout.addWidget(self.iconWidget)
        self.vBoxLayout.addSpacing(16)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.contentLabel)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.urlWidget.move(170, 192)

        self.titleLabel.setObjectName("titleLabel")
        self.contentLabel.setObjectName("contentLabel")

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        QDesktopServices.openUrl(self.url)


class LinkCardView(SingleDirectionScrollArea):
    """Link card view"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Orientation.Horizontal)
        self.view = QWidget(self)
        self.hBoxLayout = QHBoxLayout(self.view)

        self.hBoxLayout.setContentsMargins(36, 0, 0, 0)
        self.hBoxLayout.setSpacing(12)
        self.hBoxLayout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.view.setObjectName("view")
        StyleSheet.LINK_CARD.apply(self)
        prefer_time_based_smooth_scroll(self)

    def wheelEvent(self, e):
        # LinkCardView 是横向滚动容器，嵌入在主页垂直滚动流中。
        # 当内容已全部完整展示（无可滚动空间），或者滚轮主要是垂直方向（未按 Shift）时，
        # 将事件转发给父级页面，确保主页上下滑动顺畅，不被这行卡片卡住！
        h_bar = self.horizontalScrollBar()
        is_horizontal = abs(e.angleDelta().x()) > abs(e.angleDelta().y()) or bool(
            e.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )

        if not is_horizontal or (h_bar and h_bar.maximum() <= 0):
            e.ignore()
            parent_scroll = self.parent()
            while parent_scroll:
                if isinstance(parent_scroll, QScrollArea):
                    parent_scroll.wheelEvent(e)
                    return
                parent_scroll = parent_scroll.parent()
            return

        if not e.pixelDelta().isNull():
            QScrollArea.wheelEvent(self, e)
            return

        super().wheelEvent(e)

    def addCard(self, icon, title, content, url):
        """add link card"""
        card = LinkCard(icon, title, content, url, self.view)
        self.hBoxLayout.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
