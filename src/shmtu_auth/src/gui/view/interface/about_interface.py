from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    HorizontalFlipView,
    HyperlinkCard,
    PrimaryPushSettingCard,
    SettingCard,
    SettingCardGroup,
)

from shmtu_auth.src.gui.common.config import (
    AUTHOR,
    AUTHOR_MAIN_PAGE_URL,
    FEEDBACK_URL,
    HELP_URL,
    REPO_URL,
    VERSION,
    YEAR,
)
from shmtu_auth.src.gui.task.check_update import start_check_update_once_thread
from shmtu_auth.src.gui.view.interface.gallery_interface import GalleryInterface
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()


class AboutInterface(GalleryInterface):
    def __init__(self, parent=None):
        super().__init__(title="关于本程序", subtitle=f"Author: {AUTHOR}", parent=parent)
        self.setObjectName("aboutInterface")

        # 1. 软件基本信息
        self.about_group = SettingCardGroup("应用程序信息", self.view)
        self.app_card = SettingCard(
            FIF.INFO,
            "上海海事大学校园网自动认证客户端",
            f"© Copyright {YEAR}, {AUTHOR}. 当前版本: {VERSION}",
            self.about_group,
        )
        self.check_update_card = PrimaryPushSettingCard(
            "立即检查",
            FIF.SYNC,
            "检查新版本",
            "在线检测 GitHub Releases 是否有新版本",
            self.about_group,
        )
        self.check_update_card.clicked.connect(lambda: start_check_update_once_thread())
        self.about_group.addSettingCard(self.app_card)
        self.about_group.addSettingCard(self.check_update_card)
        self.vBoxLayout.addWidget(self.about_group)

        # 2. 相关链接与支持
        self.links_group = SettingCardGroup("相关链接与支持", self.view)
        self.help_card = HyperlinkCard(
            HELP_URL, "查看使用文档", FIF.HELP, "使用文档", "查看详细配置指南与使用说明", self.links_group
        )
        self.repo_card = HyperlinkCard(
            REPO_URL, "前往代码仓库", FIF.GITHUB, "开源仓库", "访问 GitHub 获取最新源代码、提交 Issue 或 PR", self.links_group
        )
        self.feedback_card = HyperlinkCard(
            FEEDBACK_URL, "提交反馈", FIF.FEEDBACK, "意见与问题反馈", "向开发者反馈 BUG 或提交功能建议", self.links_group
        )
        self.author_card = HyperlinkCard(
            AUTHOR_MAIN_PAGE_URL, "访问作者主页", FIF.HOME, "作者主页", "了解作者的更多开源项目与主页", self.links_group
        )
        self.links_group.addSettingCard(self.help_card)
        self.links_group.addSettingCard(self.repo_card)
        self.links_group.addSettingCard(self.feedback_card)
        self.links_group.addSettingCard(self.author_card)
        self.vBoxLayout.addWidget(self.links_group)

        # 3. 界面展示
        self.showcase_group = SettingCardGroup("界面展示", self.view)
        self.vBoxLayout.addWidget(self.showcase_group)

        # HorizontalFlipView 居中展示
        flip_container = QWidget(self.view)
        flip_layout = QHBoxLayout(flip_container)
        flip_layout.setContentsMargins(0, 0, 0, 0)
        self.horizontal_flip_view = HorizontalFlipView(flip_container)
        self.horizontal_flip_view.setFixedSize(600, 380)
        self.horizontal_flip_view.addImages(
            [
                ":/about/flip/current/1",
                ":/about/flip/current/2",
            ]
        )
        flip_layout.addWidget(self.horizontal_flip_view, 0, Qt.AlignCenter)
        self.vBoxLayout.addWidget(flip_container)
