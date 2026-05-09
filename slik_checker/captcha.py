"""Multi-engine captcha solver with consensus voting."""

from __future__ import annotations

import io
import warnings
from collections import Counter
from typing import Callable, Optional

# Suppress MPS pin_memory warning on Apple Silicon
warnings.filterwarnings("ignore", message="'pin_memory' argument is set as true but not supported on MPS now, device pinned memory won't be used.")

import numpy as np
from PIL import Image

from slik_checker.config import settings
from slik_checker.exceptions import CaptchaSolverError
from slik_checker.logging_config import get_logger

logger = get_logger(__name__)

ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class CaptchaSolver:
    def __init__(self) -> None:
        self._engines: dict[str, Callable] = {}
        self._ddddocr: object | None = None
        self._easyocr_reader: object | None = None
        self._init_engines()

    def _init_engines(self) -> None:
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            self._engines["tesseract"] = self._solve_tesseract
            logger.info(f"captcha_engine_loaded: engine={'tesseract'}")
        except Exception as e:
            logger.warning(f"captcha_engine_unavailable: engine={'tesseract'}")

        try:
            import ddddocr

            self._ddddocr = ddddocr.DdddOcr(show_ad=False, old=True)
            self._engines["ddddocr"] = self._solve_ddddocr
            logger.info(f"captcha_engine_loaded: engine={'ddddocr'}")
        except Exception as e:
            logger.warning(f"captcha_engine_unavailable: engine={'ddddocr'}")

        try:
            import easyocr

            self._easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            self._engines["easyocr"] = self._solve_easyocr
            logger.info(f"captcha_engine_loaded: engine={'easyocr'}")
        except Exception as e:
            logger.warning(f"captcha_engine_unavailable: engine={'easyocr'}")

    @property
    def available(self) -> bool:
        return len(self._engines) > 0

    @property
    def engine_count(self) -> int:
        return len(self._engines)

    def _solve_tesseract(self, img: Image.Image) -> Optional[str]:
        import pytesseract
        from PIL import ImageEnhance, ImageFilter, ImageOps

        p = img.convert("L")
        p = ImageOps.autocontrast(p, cutoff=3)
        p = p.filter(ImageFilter.MedianFilter(3))
        p = ImageEnhance.Contrast(p).enhance(2.0)
        p = p.point(lambda x: 255 if x > 128 else 0)
        p = ImageOps.invert(p)

        text = (
            pytesseract.image_to_string(
                p,
                config=f"--psm 7 -c tessedit_char_whitelist={ALLOWLIST}",
            )
            .strip()
            .replace(" ", "")
            .replace("\n", "")
        )
        return self._validate(text)

    def _solve_ddddocr(self, img: Image.Image) -> Optional[str]:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        text = self._ddddocr.classification(buf.getvalue())  # type: ignore[union-attr]
        text = text.replace(" ", "").replace("\n", "")
        return self._validate(text)

    def _solve_easyocr(self, img: Image.Image) -> Optional[str]:
        arr = np.array(img)
        results = self._easyocr_reader.readtext(  # type: ignore[union-attr]
            arr,
            detail=0,
            paragraph=True,
            allowlist=ALLOWLIST,
        )
        text = "".join(results).replace(" ", "").replace("\n", "")
        return self._validate(text)

    def _validate(self, text: str) -> Optional[str]:
        if not text:
            return None
        mn, mx = settings.captcha_min_length, settings.captcha_max_length
        if mn <= len(text) <= mx:
            return text
        if len(text) > mx:
            trimmed = text[:mx]
            return trimmed if mn <= len(trimmed) else None
        return None

    def solve(self, img: Image.Image) -> Optional[str]:
        if not self._engines:
            raise CaptchaSolverError("No captcha engines available")

        results: list[str] = []
        for name, fn in self._engines.items():
            try:
                r = fn(img)
                if r:
                    results.append(r)
                    logger.debug(f"captcha_engine_result: engine={name} | result={r}")
            except Exception as e:
                logger.warning(f"captcha_engine_error: engine={name}")

        if not results:
            return None

        counts = Counter(results)
        winner, count = counts.most_common(1)[0]

        if count >= 2:
            logger.info(f"captcha_consensus: result={winner}")
            return winner

        # No consensus - return winner but log warning
        logger.info(f"captcha_no_consensus: result={winner}")
        return winner

    def solve_from_bytes(self, data: bytes) -> Optional[str]:
        img = Image.open(io.BytesIO(data))
        return self.solve(img)

    def solve_from_path(self, path: str) -> Optional[str]:
        img = Image.open(path)
        return self.solve(img)


captcha_solver = CaptchaSolver()
