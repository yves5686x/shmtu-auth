"""按显示尺寸预缩放并缓存图标。

项目里的图标资源分辨率远大于实际显示尺寸：

* ``:/project/logo_shmtu``   → 2000x1992（``logopng.fw.png``，708 KB）
* ``:/project/logo_golang``  → 1062x938
* ``:/project/logo_kotlin``  → 500x500
* ``:/gui/Logo128``          → 128x128

而它们在 ``SampleCard`` 里只画 48x48、在 ``LinkCard`` 里只画 54x54。

qfluentwidgets 的 ``IconWidget.paintEvent`` 每次都调 ``drawIcon(icon, painter, rect)``，
而 ``drawIcon`` 对字符串路径是**当场** ``QIcon(path)`` 再 ``paint``：
每帧新建一个 QIcon，于是每帧都要把上面那些大图用 SmoothTransformation 缩到 48x48。
主页的几张卡片用的都是 ``logo_shmtu``（2000x1992），滚动时每帧都要重缩放好几张，
这是主页滑动卡顿的主要来源之一。

这里在第一次用到时按「目标尺寸 × 屏幕缩放比」缩一次，把 QIcon 缓存起来复用；
之后每帧都只是 1:1 贴图。传进来的如果不是图片资源路径（例如 ``FluentIcon``，
它按主题渲染、本身就很便宜），原样返回，不改变原有行为。
"""

from typing import Any, Dict, Optional, Tuple, Union

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# (资源路径, 宽, 高, 设备像素比) -> QIcon
_icon_cache: Dict[Tuple[str, int, int, float], QIcon] = {}


def get_device_pixel_ratio(ratio: Optional[float] = None) -> float:
    """拿到目标设备像素比；不传就用主屏幕的。"""
    if ratio is not None:
        return float(ratio)

    app = QApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            return float(screen.devicePixelRatio())

    return 1.0


def scaled_icon(icon: Any, size: Union[int, Tuple[int, int]], ratio: Optional[float] = None) -> Any:
    """把图片资源预处理成目标尺寸的图标；非图片资源原样返回。

    Parameters
    ----------
    icon:
        图标来源。字符串按 Qt 资源路径处理；``FluentIcon`` 之类的对象直接透传。
    size:
        显示尺寸（逻辑像素）。传一个 int 表示正方形，或传 ``(宽, 高)``。
    ratio:
        设备像素比，默认取主屏幕。

    Returns
    -------
    可以直接交给 ``IconWidget`` 的对象。
    """
    # 只有字符串是「大图资源」；FluentIconBase / QIcon / QPixmap 交给原逻辑处理
    if not isinstance(icon, str) or not icon:
        return icon

    if isinstance(size, int):
        width = height = size
    else:
        width, height = int(size[0]), int(size[1])

    if width <= 0 or height <= 0:
        return icon

    device_ratio = get_device_pixel_ratio(ratio)
    key = (icon, width, height, round(device_ratio, 2))

    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    source = QPixmap(icon)
    if source.isNull():
        # 加载失败就交回原路径，让 Qt 自己报错，别把图标弄丢
        logger.warning(f"图标资源加载失败: {icon}")
        return icon

    # 已经不比目标尺寸大就不用缩了
    if source.width() <= width and source.height() <= height:
        result = icon
    else:
        pixmap = source.scaled(
            QSize(int(width * device_ratio), int(height * device_ratio)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(device_ratio)
        result = QIcon(pixmap)

    _icon_cache[key] = result
    return result


def clear_icon_cache():
    """清空缓存（主题/缩放变化后如果图标需要重建可以调用）。"""
    _icon_cache.clear()
