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

所以项目里的滚动区域统一用 ``PerfScrollArea``（下面这个子类），
它只是把引擎选择改成后者。做法是把 ``SmoothScroll.widthThreshold`` 设成 0 ——
这是库内部**用来挑选引擎的宽度门槛**，设成 0 就等于永远满足条件，
走的是它自己的分支逻辑，不需要碰任何私有方法。

依赖内部属性名将来若变了，这里静默跳过并退回原行为，不会把界面弄崩。
"""

from qfluentwidgets import ScrollArea

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()


def _iter_smooth_scrolls(scroll_area):
    """把一个滚动区域里所有的 ``SmoothScroll`` 实例找出来。

    两种容器各有各的挂法：

    * ``ScrollArea`` → ``scrollDelagate.verticalSmoothScroll`` / ``horizonSmoothScroll``
      （注意属性名 ``scrollDelagate`` 就是这么拼的，不是笔误）
    * ``SingleDirectionScrollArea`` → ``smoothScroll``
    """
    found = []

    delegate = getattr(scroll_area, "scrollDelagate", None)
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
    """让这个滚动区域的平滑滚动按「实际时间」推进，而不是按帧数推进。"""
    for smooth_scroll in _iter_smooth_scrolls(scroll_area):
        # 门槛设 0 ⇒ 永远走按时间推进的那个引擎
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


class PerfScrollArea(ScrollArea):
    """``ScrollArea`` + 按时间推进的平滑滚动。

    项目里所有页面级滚动区域都应该继承它，别再直接继承 ``ScrollArea`` ——
    否则就会掉回「按帧数推进」的引擎，帧一慢整段滚动就被拉长。
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        prefer_time_based_smooth_scroll(self)
