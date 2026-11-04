"""界面滚动相关的调优。

qfluentwidgets 的 ``ScrollArea`` 把滚轮事件交给 ``SmoothScrollDelegate`` 接管，
之后由一个 60fps 的 ``QTimer`` 逐帧推进滚动位置（``SmoothScroll``），
「每一帧推进多少」由它选出的引擎决定：

* ``FixedStepSmoothScrollEngine`` —— 每帧固定推进 1 步、共 24 步。
  **动画时长是按帧数算的**：某一帧画慢了（真机上重绘 + 合成一帧要十几到几十
  毫秒），整段动画就被等比拉长，标称 400ms 能拖到七八百毫秒 —— 手感就是
  「滑起来发滞、跟不上手」。而它的选中条件偏严：
  ``宽度 × devicePixelRatio > 2560`` 才轮到下面那个引擎。
  MacBook 上页面宽度约 1250（逻辑像素）、dpr=2，算下来 2500 < 2560，
  恰好落在这个引擎上。
* ``AdaptiveSmoothScrollEngine`` —— 按 ``QElapsedTimer`` 的真实耗时推进，
  时长恒定：帧晚了这一帧就少走一点，但整段动画不会被拉长。

优化策略：
1. 触控板（Mac Trackpad / Windows Precision Touchpad，pixelDelta 非空）：
   绕过 60Hz 软件定时器插值，直接调用 Qt 原生物理滚轮事件，发挥 120Hz 高刷屏原生丝滑度！
2. 鼠标滚轮：
   兼容 qfluentwidgets 不同版本的属性拼写（scrollDelegate / scrollDelagate），
   将门槛设为 0 强制启用自适应时间引擎，并缩短过渡动画时长（150ms 左右），紧凑跟手。
"""

from PySide6.QtWidgets import QScrollArea
from qfluentwidgets import ScrollArea

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()


def _iter_smooth_scrolls(scroll_area):
    """把一个滚动区域里所有的 ``SmoothScroll`` 实例找出来。

    兼容两种容器与不同版本的属性名拼写：
    * ``ScrollArea`` → ``scrollDelegate`` (新版) 或 ``scrollDelagate`` (旧版 typo)
      内部的 ``verticalSmoothScroll`` / ``horizonSmoothScroll``
    * ``SingleDirectionScrollArea`` → ``smoothScroll``
    """
    found = []

    delegate = getattr(scroll_area, "scrollDelegate", None) or getattr(scroll_area, "scrollDelagate", None)
    if delegate is not None:
        for name in ("verticalSmoothScroll", "horizonSmoothScroll"):
            item = getattr(delegate, name, None)
            if item is not None:
                found.append(item)

    single = getattr(scroll_area, "smoothScroll", None)
    if single is not None:
        found.append(single)

    return found


def prefer_time_based_smooth_scroll(scroll_area) -> None:
    """让这个滚动区域的平滑滚动按「实际时间」推进，并消除过长动画带来的滞后感。"""
    for smooth_scroll in _iter_smooth_scrolls(scroll_area):
        # 门槛设 0 ⇒ 永远走按时间推进的那个引擎，不受屏幕分辨率门槛限制
        try:
            smooth_scroll.widthThreshold = 0
        except Exception as e:  # noqa: BLE001
            logger.debug(f"设置平滑滚动门槛失败（忽略，退回原行为）: {e}")
            continue

        setter = getattr(smooth_scroll, "setDynamicEngineEnabled", None)
        if setter is not None:
            try:
                setter(True)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"开启平滑滚动动态引擎失败（忽略）: {e}")

        # 调优内部引擎动画时长与队列，消除默认 400ms 动画带来的粘手滞后感
        for engine_attr in ("adaptiveEngine", "engine", "fixedStepEngine"):
            engine = getattr(smooth_scroll, engine_attr, None)
            if engine is not None:
                if hasattr(engine, "minDuration"):
                    try:
                        engine.minDuration = 150
                    except Exception:
                        pass
                if hasattr(engine, "maxQueueSize"):
                    try:
                        engine.maxQueueSize = 5
                    except Exception:
                        pass


class PerfScrollArea(ScrollArea):
    """优化后的平滑滚动区域。

    1. 触控板直接走系统原生物理滚动（120Hz 高刷平滑，零延迟不卡顿）；
    2. 传统鼠标滚轮启用时间自适应引擎，并压缩过渡延迟，紧凑跟手；
    3. 修复了 qfluentwidgets 新版本由于属性名更新导致平滑调优未生效的问题。
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        prefer_time_based_smooth_scroll(self)

    def wheelEvent(self, e):
        # 高精度触控板（Mac Trackpad / Windows Precision Touchpad，pixelDelta 非空）
        # 操作系统层面已经有高刷硬件级惯性平滑动画。
        # 此时绕过软件 SmoothScroll 伪平滑，直接调用 QScrollArea 原生滚轮处理，
        # 彻底消除拖手感、果冻感和掉帧感，发挥 120Hz ProMotion / 高刷屏丝滑度！
        if not e.pixelDelta().isNull():
            QScrollArea.wheelEvent(self, e)
            return

        super().wheelEvent(e)
