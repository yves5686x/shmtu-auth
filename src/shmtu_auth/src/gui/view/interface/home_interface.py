from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon,
    SettingCard,
    SettingCardGroup,
    isDarkTheme,
)

from shmtu_auth.src.gui.common import font_confg
from shmtu_auth.src.gui.common.components.link_card import LinkCardView
from shmtu_auth.src.gui.common.components.sample_card import SampleCardView
from shmtu_auth.src.gui.common.config import (
    AUTHOR_MAIN_PAGE_URL,
    FEEDBACK_URL,
    HELP_URL,
    REPO_URL,
)
from shmtu_auth.src.gui.common.scroll_tuning import PerfScrollArea
from shmtu_auth.src.gui.common.signal_bus import signal_bus
from shmtu_auth.src.gui.common.style_sheet import StyleSheet
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()


class BannerWidget(QWidget):
    """Banner widget"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setFixedHeight(336)

        self.vBoxLayout = QVBoxLayout(self)

        self.galleryLabel = QLabel("ShangHai Maritime University", self)
        self.galleryLabel.setFont(font_confg.title_font)
        self.galleryLabel.setObjectName("galleryLabel")

        self.banner: QPixmap = self.__load_banner()
        self.linkCardView = LinkCardView(self)

        # 整块背景（图片 + 渐变）合成后的缓存。paintEvent 会被频繁触发（滚动、切页动画、
        # 缩放窗口、HiDPI 屏重绘），原先每帧都要重新做平滑缩放 + 裁剪 + 铺一层渐变，
        # 掉帧很明显。现在只在尺寸变化时重算一次，paintEvent 只剩一次贴图。
        self._cache_size = QSize()
        self._cache_pixmap = None

        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.setContentsMargins(0, 20, 0, 0)
        self.vBoxLayout.addWidget(self.galleryLabel)
        self.vBoxLayout.addWidget(self.linkCardView, 1, Qt.AlignBottom)

        margin_widget = QWidget(self)
        margin_widget.setFixedHeight(20)
        self.vBoxLayout.addWidget(margin_widget)

        self.vBoxLayout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.linkCardView.addCard(":/gui/Logo128", "快速入门", "查看本程序的在线文档。", HELP_URL)

        self.linkCardView.addCard(FluentIcon.GITHUB, "Github主页", "查看本程序的源代码。", REPO_URL)

        self.linkCardView.addCard(
            FluentIcon.HOME_FILL,
            "孔昊旻的主页",
            "查看作者的其他项目",
            AUTHOR_MAIN_PAGE_URL,
        )

        self.linkCardView.addCard(
            FluentIcon.FEEDBACK,
            "问题反馈",
            "反馈问题或建议(需要Github账户)。",
            FEEDBACK_URL,
        )

    @staticmethod
    def __load_banner() -> QPixmap:
        """加载 banner 原图，并按屏幕可能需要的大小预先缩小一次。

        资源里的原图是 3986x1329（2.5 MB，解出来约 21 MB 位图），而 banner 高度
        固定 336 —— 每次窗口尺寸变化都要从这张原图做一次平滑缩放，拖动窗口/切页时
        会明显掉帧。开机先按「屏幕实际需要的最大宽度」缩一次，后续缩放的成本就降下来了。
        """
        pixmap = QPixmap(":/shmtu/banner1")
        if pixmap.isNull():
            logger.warning("banner 资源加载失败，主页将只显示渐变背景")
            return pixmap

        # 至少留 2400，再按屏幕的物理宽度放宽（兼顾 HiDPI 与超宽屏）
        limit = 2400
        screen = QApplication.primaryScreen()
        if screen is not None:
            screen_width = int(screen.geometry().width() * screen.devicePixelRatio())
            limit = max(limit, screen_width)

        if pixmap.width() > limit:
            pixmap = pixmap.scaledToWidth(limit, Qt.TransformationMode.SmoothTransformation)

        return pixmap

    def __rebuild_cache(self):
        """按当前尺寸重建整块背景（图片 + 渐变）的缓存。"""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            self._cache_size = QSize(w, h)
            self._cache_pixmap = None
            return

        ratio = self.devicePixelRatioF() or 1.0

        # 按设备像素渲染，HiDPI 屏下不会糊
        pixmap = QPixmap(int(w * ratio), int(h * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # 圆角路径（沿用原有画法；simplified() 之后实际是整块矩形）
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        path.addRect(QRectF(0, h - 50, 50, 50))
        path.addRect(QRectF(w - 50, 0, 50, 50))
        path.addRect(QRectF(w - 50, h - 50, 50, 50))
        path = path.simplified()

        # 图片：等比放大到「盖满」当前尺寸，再垂直居中裁剪
        origin_width = self.banner.width()
        origin_height = self.banner.height()
        if origin_width > 0 and origin_height > 0:
            wh_ratio = origin_width / origin_height

            width_new = h * wh_ratio
            height_new = h
            if width_new < w:
                width_new = w
                height_new = width_new / wh_ratio

            scaled_pixmap = self.banner.scaled(
                QSize(int(width_new * ratio), int(height_new * ratio)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

            crop_y = (scaled_pixmap.height() - int(h * ratio)) / 2
            croped_pixmap = scaled_pixmap.copy(
                0, int(max(crop_y, 0)), int(w * ratio), int(h * ratio)
            )
            croped_pixmap.setDevicePixelRatio(ratio)

            painter.drawPixmap(QRectF(0, 0, w, h), croped_pixmap, QRectF(0, 0, w, h))

        # 渐变遮罩
        gradient = QLinearGradient(0, 0, 0, h)
        if not isDarkTheme():
            gradient.setColorAt(0, QColor(207, 216, 228, 255))
            gradient.setColorAt(1, QColor(207, 216, 228, 0))
        else:
            gradient.setColorAt(0, QColor(0, 0, 0, 255))
            gradient.setColorAt(1, QColor(0, 0, 0, 0))

        painter.fillPath(path, QBrush(gradient))
        painter.end()

        self._cache_size = QSize(w, h)
        self._cache_pixmap = pixmap

    def paintEvent(self, e):
        super().paintEvent(e)

        w, h = self.width(), self.height()
        if self._cache_pixmap is None or self._cache_size != QSize(w, h):
            self.__rebuild_cache()

        if self._cache_pixmap is None:
            return

        # 每帧只有这一张贴图（尺寸比对命中缓存时不会再做任何缩放/渐变）
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache_pixmap)


class QuickStatusCard(SettingCardGroup):
    """主页快速状态概览卡片"""

    def __init__(self, parent=None):
        super().__init__("系统状态概览", parent)

        # 网络状态卡
        self.network_card = SettingCard(FluentIcon.WIFI, "网络状态", self.__initial_network_tip())
        self.addSettingCard(self.network_card)

        # 服务状态卡
        self.service_card = SettingCard(FluentIcon.POWER_BUTTON, "认证服务", "未启动")
        self.addSettingCard(self.service_card)

        # 连接信号
        signal_bus.signal_auth_status_changed.connect(self.update_network_status)
        signal_bus.signal_auth_thread_started.connect(lambda: self.service_card.setContent("运行中 ✓"))
        signal_bus.signal_auth_thread_stopped.connect(lambda: self.service_card.setContent("已停止 ✗"))

    @staticmethod
    def __initial_network_tip() -> str:
        """先用上次记录的结论占位，不要一直挂在「检查中...」。

        真正的探测结果要等界面构建完、事件循环跑起来之后才回来（见 MainWindow 里
        对 SystemTray.start_initial_network_probe 的调用）。原先这里写死
        「检查中...」，而这张卡片只在收到状态信号时才更新 —— 没开认证服务时
        根本没人发信号，于是它会永远停在「检查中...」，看着像界面卡住了。
        """
        from shmtu_auth.src.gui.common.config import cfg

        if cfg.last_network_status.value:
            return "已连接 ✓"
        return "需要认证 ⚠"

    def update_network_status(self, is_online: bool):
        """更新网络状态"""
        if is_online:
            self.network_card.setContent("已连接 ✓")
        else:
            self.network_card.setContent("需要认证 ⚠")


class HomeInterface(PerfScrollArea):
    """Home interface"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.banner = BannerWidget(self)
        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self.__initWidget()
        self.load_card_content()

    def __initWidget(self):
        self.view.setObjectName("view")
        self.setObjectName("homeInterface")
        StyleSheet.HOME_INTERFACE.apply(self)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 36)
        self.vBoxLayout.setSpacing(40)
        self.vBoxLayout.addWidget(self.banner)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

    def load_card_content(self):
        # 状态概览卡片 - 添加左右边距
        from PySide6.QtWidgets import QHBoxLayout

        status_container = QWidget(self.view)
        status_layout = QHBoxLayout(status_container)
        status_layout.setContentsMargins(20, 0, 20, 0)  # 左右各20px边距

        self.status_card = QuickStatusCard(self.view)
        status_layout.addWidget(self.status_card)

        self.vBoxLayout.addWidget(status_container)

        current_application_view_group = SampleCardView("本程序功能", self.view)
        current_application_view_group.addSampleCard(
            icon=":/gui/Logo128",
            title="校园网自动认证",
            content="监控网络状况，自动认证校园网，\n避免因各种因素导致断网。",
            index=26,
            url="https://a645162.github.io/shmtu-auth/1.Guide/0.Quick%20Start/1.Quick%20Start.html",
        )
        self.vBoxLayout.addWidget(current_application_view_group)

        # 为上海海事大学开发的项目
        shmtu_project_view_group = SampleCardView("数字海大系列(非官方,个人学习使用)", self.view)
        # shmtu_project_view_group.addSampleCard(
        #     icon=":/project/logo_terminal",
        #     title="用户终端(非官方)",
        #     content="数字海大的用户终端(第三方)\n主要包括账单获取、账单分析等功能。",
        #     index=3,
        #     url="https://github.com/a645162/SHMTU-Terminal-Wails",
        # )
        shmtu_project_view_group.addSampleCard(
            icon=":/project/logo_shmtu",
            title="验证码识别服务器(C++)",
            content="自动识别统一认证平台的验证码。",
            index=4,
            url="https://github.com/a645162/shmtu-cas-ocr-server",
        )

        shmtu_project_view_group.addSampleCard(
            icon=":/project/logo_shmtu",
            title="验证码识别Demo-Windows",
            content="Win32+WPF+WinForms\n自动识别统一认证平台的验证码。",
            index=4,
            url="https://github.com/a645162/shmtu-cas-ocr-demo-windows",
        )
        shmtu_project_view_group.addSampleCard(
            icon=":/project/logo_shmtu",
            title="验证码识别Demo-Qt",
            content="Windows+macOS+Linux\n自动识别统一认证平台的验证码。",
            index=4,
            url="https://github.com/a645162/shmtu-cas-ocr-demo-qt",
        )
        shmtu_project_view_group.addSampleCard(
            icon=":/project/logo_shmtu",
            title="验证码识别Demo-Android",
            content="自动识别统一认证平台的验证码。",
            index=4,
            url="https://github.com/a645162/shmtu-cas-demo-android",
        )

        shmtu_project_view_group.addSampleCard(
            icon=":/project/logo_csharp",
            title="登录流程(.Net)",
            content="统一认证平台的登录流程\n包括调用识别验证码接口。",
            index=5,
            url="https://github.com/a645162/shmtu-dotnet-lib",
        )
        shmtu_project_view_group.addSampleCard(
            icon=":/project/logo_golang",
            title="登录流程(Golang)",
            content="统一认证平台的登录流程\n包括调用识别验证码接口。",
            index=5,
            url="https://github.com/a645162/shmtu-cas-go",
        )
        shmtu_project_view_group.addSampleCard(
            icon=":/project/logo_kotlin",
            title="登录流程(Kotlin)",
            content="统一认证平台的登录流程\n包括调用识别验证码接口。",
            index=5,
            url="https://github.com/a645162/shmtu-cas-kotlin",
        )

        self.vBoxLayout.addWidget(shmtu_project_view_group)

        # 为课题组开发的项目
        group_project_view_group = SampleCardView("为课题组开发的项目", self.view)
        group_project_view_group.addSampleCard(
            icon=":/project/logo_camera",
            title="DahuaCameraMaster",
            content="大华摄像机控制软件",
            index=4,
            url="https://github.com/a645162/DahuaCameraMaster",
        )
        group_project_view_group.addSampleCard(
            icon=":/project/logo_puzzle",
            title="PicPuzzle",
            content="拼图工具",
            index=4,
            url="https://github.com/a645162/PicPuzzle",
        )
        group_project_view_group.addSampleCard(
            icon=":/project/logo_gpu_dashboard",
            title="GPU任务通知工具",
            content="我与师兄合作开发的一款GPU任务监控工具，\nGPU训练任务结束自动推送消息。",
            index=4,
            url="https://github.com/a645162/nvi-notify",
        )
        group_project_view_group.addSampleCard(
            icon=":/project/logo_gpu_dashboard",
            title="GPU看板",
            content="GPU任务面板基于 React+ Ant Design 开发\n后端为显卡监控脚本的Flask。",
            index=4,
            url="https://github.com/a645162/group-center-dashboard",
        )
        group_project_view_group.addSampleCard(
            icon=":/project/logo_gpu_dashboard",
            title="GPU看板",
            content="GPU任务面板基于 Vue3 + Element Plus + Pinia 开发，后端为显卡监控脚本的Flask。",
            index=4,
            url="https://github.com/a645162/web-gpu-dashboard",
        )
        self.vBoxLayout.addWidget(group_project_view_group)
