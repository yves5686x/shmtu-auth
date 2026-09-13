"""门户图形验证码识别。

门户验证码是 ``GET /eportal/validcode?rnd=<随机数>`` 返回的
80×30、4 位纯数字、橙色字符 + 白色背景，没有干扰线，识别难度很低。

本模块遵循「OCR 优先、失败再问人」的策略：

1. 先尝试可用的 OCR 后端（``ddddocr`` → ``pytesseract``）
2. 都不可用或结果不可信时返回 ``None``
3. 由调用方决定是否弹窗请用户输入（见 ``CaptchaProvider``）

OCR 后端全部是**可选依赖**，缺失时本模块静默降级，不会影响主流程。
"""

import re
from typing import Callable, List, Optional

from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

# 门户验证码固定为 4 位数字
CAPTCHA_LENGTH = 4

# 上层提供的验证码输入回调：接收图片字节，返回识别结果（取消/失败返回 None）
CaptchaProvider = Callable[[bytes], Optional[str]]

_DIGITS_ONLY = re.compile(r"\D")


def normalize_code(text: Optional[str]) -> Optional[str]:
    """把 OCR 原始输出规整为 4 位数字，不符合则返回 None。

    非 4 位的结果一律视为「不可信」，交给上层兜底，避免拿错误验证码去登录。
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

    def __init__(self):
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
        except Exception as e:
            logger.warning(f"ddddocr failed: {e}")
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
        except Exception as e:
            logger.warning(f"pytesseract failed: {e}")
            return None


_SOLVERS: List[BaseCaptchaSolver] = [DdddOcrSolver(), TesseractSolver()]

_cached_available: Optional[List[BaseCaptchaSolver]] = None


def get_available_solvers() -> List[BaseCaptchaSolver]:
    """返回当前环境可用的 OCR 后端（结果缓存，探测只做一次）。"""
    global _cached_available
    if _cached_available is None:
        _cached_available = [s for s in _SOLVERS if s.is_available()]
        if _cached_available:
            names = ", ".join(s.name for s in _cached_available)
            logger.info(f"OCR captcha solver available: {names}")
        else:
            logger.info("No OCR captcha solver available, will fall back to manual input")
    return _cached_available


def _ocr_enabled() -> bool:
    """配置项 SHMTU_AUTH_CAPTCHA_OCR，默认开启。"""
    try:
        from shmtu_auth.src.utils.env import get_env_str

        value = get_env_str("SHMTU_AUTH_CAPTCHA_OCR", "true")
    except Exception:
        return True
    if value is None:
        return True
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def solve_captcha(image: bytes) -> Optional[str]:
    """尝试用可用 OCR 后端识别验证码，全部失败返回 None。"""
    if not _ocr_enabled():
        logger.info("Captcha OCR disabled by config, fall back to manual input")
        return None

    for solver in get_available_solvers():
        code = solver.solve(image)
        if code:
            logger.info(f"Captcha recognized by {solver.name}: {code}")
            return code
    logger.info("Captcha OCR inconclusive")
    return None
