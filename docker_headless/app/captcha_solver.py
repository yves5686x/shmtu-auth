"""门户图形验证码识别（无头版）。

与主包 ``shmtu_auth/src/core/captcha_solver.py`` 逻辑一致，差异只有两点：

1. 用标准库 ``logging`` 而不是 ``loguru``，配置读取走 ``app.config.get_env_str``，
   保持本目录「不依赖 loguru / toml / PyQt」的定位；
2. 无头环境没有弹窗兜底，OCR 是唯一出路，因此这里只做识别，识别失败返回 ``None``。

门户验证码是 ``GET /eportal/validcode?rnd=<随机数>`` 返回的 80×30、4 位纯数字、
橙色字符 + 白色背景、无干扰线的图片，识别难度很低。

OCR 后端全部是**可选导入**（ddddocr 已在 ``requirements.txt`` 里，pytesseract 为备选），
任一后端缺失时本模块静默降级，不会影响 import 与主流程。
"""

import logging
import re
from typing import Callable, List, Optional

from app.config import get_env_str

LOGGER = logging.getLogger("shmtu_auth_headless.captcha")

# 门户验证码固定为 4 位数字
CAPTCHA_LENGTH = 4

# 上层提供的验证码输入回调：接收图片字节，返回识别结果（取消/失败返回 None）
CaptchaProvider = Callable[[bytes], Optional[str]]

_DIGITS_ONLY = re.compile(r"\D")


def normalize_code(text: Optional[str]) -> Optional[str]:
    """把 OCR 原始输出规整为 4 位数字，不符合则返回 None。

    非 4 位的结果一律视为「不可信」，避免拿错误验证码去登录、白白消耗一次机会。
    """
    if not text:
        return None
    digits = _DIGITS_ONLY.sub("", str(text))
    if len(digits) != CAPTCHA_LENGTH:
        return None
    return digits


class BaseCaptchaSolver:
    """验证码识别后端基类。"""

    name: str = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def solve(self, image: bytes) -> Optional[str]:
        raise NotImplementedError


class DdddOcrSolver(BaseCaptchaSolver):
    """ddddocr 后端：专门做验证码识别，对本门户这种 4 位数字图命中率很高。"""

    name = "ddddocr"

    def __init__(self) -> None:
        self._engine = None

    def is_available(self) -> bool:
        try:
            import ddddocr  # noqa: F401
        except Exception:
            return False
        return True

    def solve(self, image: bytes) -> Optional[str]:
        try:
            if self._engine is None:
                import ddddocr

                self._engine = ddddocr.DdddOcr(show_ad=False)
            return normalize_code(self._engine.classification(image))
        except Exception as exc:
            LOGGER.warning("ddddocr failed: %s", exc)
            return None


class TesseractSolver(BaseCaptchaSolver):
    """pytesseract 后端：需要本机安装 tesseract 可执行文件。"""

    name = "pytesseract"

    def is_available(self) -> bool:
        try:
            import PIL  # noqa: F401
            import pytesseract  # noqa: F401
        except Exception:
            return False
        return True

    def solve(self, image: bytes) -> Optional[str]:
        try:
            import io

            import pytesseract
            from PIL import Image

            img = Image.open(io.BytesIO(image))
            img = img.convert("L")
            # 门户验证码是深色字 + 浅色底，二值化后更利于识别
            img = img.point(lambda p: 255 if p > 140 else 0)
            text = pytesseract.image_to_string(
                img,
                config="--psm 8 -c tessedit_char_whitelist=0123456789",
            )
            return normalize_code(text)
        except Exception as exc:
            LOGGER.warning("pytesseract failed: %s", exc)
            return None


# 按优先级排列；ddddocr 对本门户命中率明显更高，放在前面
_SOLVERS: List[BaseCaptchaSolver] = [DdddOcrSolver(), TesseractSolver()]

_cached_available: Optional[List[BaseCaptchaSolver]] = None


def _ocr_enabled() -> bool:
    """配置项 SHMTU_AUTH_CAPTCHA_OCR，默认开启。"""
    value = get_env_str("SHMTU_AUTH_CAPTCHA_OCR", "true")
    if value is None:
        return True
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def get_available_solvers() -> List[BaseCaptchaSolver]:
    """返回当前环境可用的 OCR 后端（结果缓存，探测只做一次）。

    关闭 OCR 配置时直接返回空列表，跳过昂贵的能力探测。
    """
    global _cached_available
    if _cached_available is None:
        if not _ocr_enabled():
            LOGGER.info("Captcha OCR disabled by config")
            _cached_available = []
            return _cached_available

        _cached_available = [s for s in _SOLVERS if s.is_available()]
        if _cached_available:
            names = ", ".join(s.name for s in _cached_available)
            LOGGER.info("OCR captcha solver available: %s", names)
        else:
            LOGGER.error(
                "No OCR captcha solver available. The portal now requires a captcha "
                "for every login, so headless auth cannot succeed without one. "
                "Install the OCR backend with `pip install -r requirements.txt` "
                "(ddddocr), or set SHMTU_AUTH_CAPTCHA_OCR=true together with "
                "pytesseract + tesseract."
            )
    return _cached_available


def solve_captcha(image: bytes) -> Optional[str]:
    """尝试用可用 OCR 后端识别验证码，全部失败返回 None。"""
    for solver in get_available_solvers():
        code = solver.solve(image)
        if code:
            LOGGER.info("Captcha recognized by %s: %s", solver.name, code)
            return code
    LOGGER.info("Captcha OCR inconclusive")
    return None
