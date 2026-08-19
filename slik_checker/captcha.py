"""Multi-engine captcha solver with advanced preprocessing, voting, and training data collection."""

from __future__ import annotations

import io
import re
import time
import warnings
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from slik_checker.config import settings
from slik_checker.exceptions import CaptchaSolverError
from slik_checker.logging_config import get_logger

# Suppress MPS pin_memory warning on Apple Silicon
warnings.filterwarnings(
    "ignore",
    message="'pin_memory' argument is set as true but not supported on MPS now, device pinned memory won't be used.",
)

logger = get_logger(__name__)

ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CAPTCHA_SAVE_DIR = Path("data/captcha_samples")


class CaptchaPreprocessor:
    """Generates multiple preprocessed versions of a captcha image."""

    @staticmethod
    def grayscale(img: Image.Image) -> Image.Image:
        return img.convert("L")

    @staticmethod
    def autocontrast(img: Image.Image, cutoff: int = 3) -> Image.Image:
        return ImageOps.autocontrast(img, cutoff=cutoff)

    @staticmethod
    def median_filter(img: Image.Image, size: int = 3) -> Image.Image:
        return img.filter(ImageFilter.MedianFilter(size))

    @staticmethod
    def gaussian_blur(img: Image.Image, radius: float = 1.0) -> Image.Image:
        return img.filter(ImageFilter.GaussianBlur(radius=radius))

    @staticmethod
    def enhance_contrast(img: Image.Image, factor: float = 2.0) -> Image.Image:
        return ImageEnhance.Contrast(img).enhance(factor)

    @staticmethod
    def enhance_sharpness(img: Image.Image, factor: float = 2.0) -> Image.Image:
        return ImageEnhance.Sharpness(img).enhance(factor)

    @staticmethod
    def binary_threshold(img: Image.Image, threshold: int = 128) -> Image.Image:
        return img.point(lambda x: 255 if x > threshold else 0)

    @staticmethod
    def invert(img: Image.Image) -> Image.Image:
        return ImageOps.invert(img)

    @staticmethod
    def otsu_threshold(img: Image.Image) -> Image.Image:
        """Apply OTSU adaptive thresholding."""
        arr = np.array(img)
        # Simple OTSU approximation
        hist, _ = np.histogram(arr, bins=256, range=(0, 256))
        total = arr.size
        sum_total = sum(i * hist[i] for i in range(256))
        sum_bg = 0
        w_bg = 0
        w_fg = 0
        max_var = 0
        threshold = 128
        for t in range(256):
            w_bg += hist[t]
            if w_bg == 0:
                continue
            w_fg = total - w_bg
            if w_fg == 0:
                break
            sum_bg += t * hist[t]
            mean_bg = sum_bg / w_bg
            mean_fg = (sum_total - sum_bg) / w_fg
            var_between = w_bg * w_fg * (mean_bg - mean_fg) ** 2
            if var_between > max_var:
                max_var = var_between
                threshold = t
        return img.point(lambda x: 255 if x > threshold else 0)

    @staticmethod
    def remove_noise(img: Image.Image, iterations: int = 1) -> Image.Image:
        """Remove isolated noise pixels."""
        arr = np.array(img)
        for _ in range(iterations):
            for y in range(1, arr.shape[0] - 1):
                for x in range(1, arr.shape[1] - 1):
                    if arr[y, x] == 255:  # white pixel
                        neighbors = [
                            arr[y - 1, x], arr[y + 1, x],
                            arr[y, x - 1], arr[y, x + 1],
                        ]
                        black_count = sum(1 for n in neighbors if n == 0)
                        if black_count >= 3:
                            arr[y, x] = 0
                    elif arr[y, x] == 0:  # black pixel
                        neighbors = [
                            arr[y - 1, x], arr[y + 1, x],
                            arr[y, x - 1], arr[y, x + 1],
                        ]
                        white_count = sum(1 for n in neighbors if n == 255)
                        if white_count >= 3:
                            arr[y, x] = 255
        return Image.fromarray(arr)

    @staticmethod
    def dilate(img: Image.Image, iterations: int = 1) -> Image.Image:
        """Simple dilation to make characters thicker."""
        arr = np.array(img)
        for _ in range(iterations):
            new_arr = arr.copy()
            for y in range(1, arr.shape[0] - 1):
                for x in range(1, arr.shape[1] - 1):
                    if arr[y, x] == 0:  # black pixel
                        # Expand to neighbors
                        new_arr[y - 1, x] = 0
                        new_arr[y + 1, x] = 0
                        new_arr[y, x - 1] = 0
                        new_arr[y, x + 1] = 0
            arr = new_arr
        return Image.fromarray(arr)

    @staticmethod
    def erode(img: Image.Image, iterations: int = 1) -> Image.Image:
        """Simple erosion to remove small noise."""
        arr = np.array(img)
        for _ in range(iterations):
            new_arr = arr.copy()
            for y in range(1, arr.shape[0] - 1):
                for x in range(1, arr.shape[1] - 1):
                    if arr[y, x] == 0:  # black pixel
                        # Only keep if all neighbors are also black
                        neighbors = [
                            arr[y - 1, x], arr[y + 1, x],
                            arr[y, x - 1], arr[y, x + 1],
                        ]
                        if any(n != 0 for n in neighbors):
                            new_arr[y, x] = 255
            arr = new_arr
        return Image.fromarray(arr)

    @staticmethod
    def resize(img: Image.Image, scale: float = 2.0) -> Image.Image:
        """Upscale image for better OCR."""
        w, h = img.size
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    @staticmethod
    def deskew(img: Image.Image) -> Image.Image:
        """Simple deskew by rotating slightly."""
        try:
            import pytesseract
            osd = pytesseract.image_to_osd(img, config="--psm 0 -c min_characters_to_try=5")
            angle = float([line for line in osd.split("\n") if "Orientation in degrees:" in line][0].split(":")[1].strip())
            if angle != 0:
                return img.rotate(-angle, expand=True, fillcolor="white")
        except Exception:
            pass
        return img

    def get_all_pipelines(self, img: Image.Image) -> list[tuple[str, Image.Image]]:
        """Generate multiple preprocessed versions with different strategies."""
        gray = self.grayscale(img)
        pipelines: list[tuple[str, Image.Image]] = []

        # Strategy 1: Basic autocontrast + median + threshold (current)
        p = self.autocontrast(gray)
        p = self.median_filter(p, 3)
        p = self.enhance_contrast(p, 2.0)
        p = self.binary_threshold(p, 128)
        p = self.invert(p)
        pipelines.append(("basic", p.resize((p.width * 3, p.height * 3), Image.LANCZOS)))

        # Strategy 2: OTSU threshold
        p = self.autocontrast(gray)
        p = self.gaussian_blur(p, 1.0)
        p = self.otsu_threshold(p)
        p = self.invert(p)
        p = self.remove_noise(p, 1)
        p = self.dilate(p, 1)
        pipelines.append(("otsu", p.resize((p.width * 3, p.height * 3), Image.LANCZOS)))

        # Strategy 3: High contrast + erode
        p = self.enhance_contrast(gray, 3.0)
        p = self.enhance_sharpness(p, 3.0)
        p = self.median_filter(p, 3)
        p = self.binary_threshold(p, 150)
        p = self.invert(p)
        p = self.erode(p, 1)
        pipelines.append(("erode", p.resize((p.width * 3, p.height * 3), Image.LANCZOS)))

        # Strategy 4: Low threshold + dilate
        p = self.autocontrast(gray)
        p = self.gaussian_blur(p, 0.5)
        p = self.binary_threshold(p, 100)
        p = self.invert(p)
        p = self.dilate(p, 2)
        pipelines.append(("dilate", p.resize((p.width * 3, p.height * 3), Image.LANCZOS)))

        # Strategy 5: Heavy noise removal
        p = self.autocontrast(gray)
        p = self.median_filter(p, 5)
        p = self.enhance_contrast(p, 2.5)
        p = self.binary_threshold(p, 128)
        p = self.invert(p)
        p = self.remove_noise(p, 2)
        pipelines.append(("denoised", p.resize((p.width * 3, p.height * 3), Image.LANCZOS)))

        return pipelines


class CaptchaDataCollector:
    """Collects captcha images and OCR results for training/analysis."""

    def __init__(self) -> None:
        self._save_dir = CAPTCHA_SAVE_DIR
        self._save_dir.mkdir(parents=True, exist_ok=True)

    def save_sample(self, data: bytes, engine_results: dict[str, str | None], final_text: str | None) -> str:
        """Save a captcha sample with metadata for analysis."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = f"captcha_{ts}.png"
        fpath = str(self._save_dir / fname)

        # Save raw image
        with open(fpath, "wb") as f:
            f.write(data)

        # Save metadata
        meta_path = str(self._save_dir / f"captcha_{ts}.meta.txt")
        with open(meta_path, "w") as f:
            f.write(f"timestamp: {datetime.now().isoformat()}\n")
            f.write(f"final: {final_text}\n")
            for engine, result in engine_results.items():
                f.write(f"{engine}: {result}\n")

        logger.debug(f"captcha_saved: {fname} | final={final_text}")
        return fpath

    def get_training_data_summary(self) -> dict[str, int]:
        """Get count of collected samples."""
        pngs = list(self._save_dir.glob("*.png"))
        return {"total_samples": len(pngs)}

    def get_samples_since(self, hours: int = 24) -> list[Path]:
        """Get samples collected in the last N hours."""
        cutoff = time.time() - hours * 3600
        samples = []
        for p in self._save_dir.glob("*.png"):
            if p.stat().st_mtime >= cutoff:
                samples.append(p)
        return samples


class CaptchaSolver:
    def __init__(self) -> None:
        self._engines: dict[str, Callable] = {}
        self._ddddocr: object | None = None
        self._easyocr_reader: object | None = None
        self._preprocessor = CaptchaPreprocessor()
        self._collector = CaptchaDataCollector()
        self._engines_ready = False

    def _init_engines(self) -> None:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            self._engines["tesseract"] = self._solve_tesseract
            logger.info(f"captcha_engine_loaded: engine={'tesseract'}")
        except Exception:
            logger.warning(f"captcha_engine_unavailable: engine={'tesseract'}")

        try:
            import ddddocr  # noqa: F401  (heavy model constructed lazily)
            self._engines["ddddocr"] = self._solve_ddddocr
            logger.info(f"captcha_engine_registered: engine={'ddddocr'}")
        except Exception:
            logger.warning(f"captcha_engine_unavailable: engine={'ddddocr'}")

        try:
            import easyocr  # noqa: F401  (heavy model constructed lazily)
            self._engines["easyocr"] = self._solve_easyocr
            logger.info(f"captcha_engine_registered: engine={'easyocr'}")
        except Exception:
            logger.warning(f"captcha_engine_unavailable: engine={'easyocr'}")
    def _ensure_engines(self) -> None:
        if self._engines_ready:
            return
        self._init_engines()
        self._engines_ready = True

    def _ensure_ddddocr(self):
        if self._ddddocr is None:
            import ddddocr
            self._ddddocr = ddddocr.DdddOcr(show_ad=False, old=True)
        return self._ddddocr

    def _ensure_easyocr(self):
        if self._easyocr_reader is None:
            import easyocr
            self._easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        return self._easyocr_reader


    @property
    def available(self) -> bool:
        self._ensure_engines()
        if len(self._engines) > 0:
            return True
        # Non-local solvers (vision LLM, external API, manual input, or a
        # pre-supplied override) do not require local OCR engines to be loaded.
        if settings.captcha_override:
            return True
        if settings.captcha_mode in ("manual", "vision"):
            return True
        if settings.vision_captcha_enabled and settings.vision_captcha_api_key:
            return True
        return bool(settings.external_captcha_api_key)

    @property
    def engine_count(self) -> int:
        self._ensure_engines()
        return len(self._engines)

    def _solve_tesseract(self, img: Image.Image) -> str | None:
        import pytesseract

        # Try multiple preprocessing strategies
        pipelines = self._preprocessor.get_all_pipelines(img)
        best_result = None
        best_confidence = -1

        for name, processed in pipelines:
            try:
                text = (
                    pytesseract.image_to_string(
                        processed,
                        config=f"--psm 7 -c tessedit_char_whitelist={ALLOWLIST}",
                    )
                    .strip()
                    .replace(" ", "")
                    .replace("\n", "")
                )
                validated = self._validate(text)
                if validated:
                    # Score: prefer longer valid texts
                    confidence = len(validated)
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_result = validated
                        logger.debug(f"tesseract_strategy_{name}: result={validated}")
            except Exception:
                continue

        return best_result

    def _solve_ddddocr(self, img: Image.Image) -> str | None:
        buf = io.BytesIO()
        # Try multiple scales
        results = []
        for scale in [1.0, 2.0, 3.0]:
            try:
                scaled = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
                buf.seek(0)
                buf.truncate(0)
                scaled.save(buf, format="PNG")
                text = self._ensure_ddddocr().classification(buf.getvalue())  # type: ignore[union-attr]
                text = text.replace(" ", "").replace("\n", "")
                validated = self._validate(text)
                if validated:
                    results.append(validated)
            except Exception:
                continue

        if not results:
            return None

        # Return the most common result
        cnt = Counter(results)
        return cnt.most_common(1)[0][0]

    def _solve_easyocr(self, img: Image.Image) -> str | None:
        results = []

        # Try multiple preprocessing strategies for easyocr
        pipelines = self._preprocessor.get_all_pipelines(img)
        for name, processed in pipelines:
            try:
                arr = np.array(processed)
                texts = self._ensure_easyocr().readtext(  # type: ignore[union-attr]
                    arr,
                    detail=0,
                    paragraph=True,
                    allowlist=ALLOWLIST,
                )
                text = "".join(texts).replace(" ", "").replace("\n", "")
                validated = self._validate(text)
                if validated:
                    results.append(validated)
                    logger.debug(f"easyocr_strategy_{name}: result={validated}")
            except Exception:
                continue

        if not results:
            return None

        cnt = Counter(results)
        return cnt.most_common(1)[0][0]

    def _validate(self, text: str) -> str | None:
        if not text:
            return None
        # Filter to only allowed characters
        filtered = "".join(c for c in text if c in ALLOWLIST)
        if not filtered:
            return None
        mn, mx = settings.captcha_min_length, settings.captcha_max_length
        if mn <= len(filtered) <= mx:
            return filtered
        if len(filtered) > mx:
            return filtered[:mx] if mn <= mx else None
        return None

    def _vote_all(self, img: Image.Image) -> tuple[str | None, dict[str, str | None]]:
        """Run every available OCR engine and vote; returns (winner, per-engine results).

        All engines (incl. the heavier easyocr) participate so that when ddddocr
        and easyocr disagree, the disagreement is surfaced and the most common
        reading wins. The orchestrator additionally tries each distinct candidate
        against the live portal, which is the authoritative validator.
        """
        engine_results: dict[str, str | None] = {}
        votes: list[str] = []
        for name, fn in self._engines.items():
            try:
                r = fn(img)
                engine_results[name] = r
                if r:
                    votes.append(r)
            except Exception:
                logger.warning(f"captcha_engine_error: engine={name}")
                engine_results[name] = None
        if votes:
            return Counter(votes).most_common(1)[0][0], engine_results
        return None, engine_results

    def solve(self, img: Image.Image) -> str | None:
        self._ensure_engines()
        if not self._engines:
            raise CaptchaSolverError("No captcha engines available")
        winner, _ = self._vote_all(img)
        return winner

    def solve_from_bytes(self, data: bytes) -> str | None:
        img = Image.open(io.BytesIO(data))
        self._ensure_engines()
        winner, engine_results = self._vote_all(img)
        try:
            self._collector.save_sample(data, engine_results, winner)
        except Exception as e:
            logger.debug(f"captcha_save_failed: {e}")
        return winner

    def solve_from_path(self, path: str) -> str | None:
        img = Image.open(path)
        return self.solve(img)

    def solve_vision(self, img: Image.Image) -> str | None:
        """Solve a captcha image via an OpenAI-compatible vision-LLM.

        FREE with a free-tier API key (Gemini 2.5 Flash free tier, GLM-4.6v free
        quota) or a local Ollama vision model (http://localhost:11434/v1, no key).
        Uses the OpenAI-compatible /chat/completions vision API, so it works with
        Gemini (OpenAI-compatible base URL), GLM (OpenAI-compatible base URL), or
        Ollama. Pattern from epic-freebies-helper (multimodal captcha reading).
        Returns the transcribed text, or None on any error / if not configured.
        """
        if not settings.vision_captcha_enabled:
            return None
        try:
            import base64


            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            base = str(settings.vision_captcha_api_base).rstrip("/")
            model = settings.vision_captcha_model
            prompt = settings.vision_captcha_prompt
            api_key = settings.vision_captcha_api_key
            api_key_val = api_key.get_secret_value() if api_key else ""

            # Native Gemini (…/models/<m>:generateContent) uses the X-goog-api-key
            # header and a different request/response shape than the OpenAI-compatible
            # /chat/completions path (used by GLM, Ollama, and Gemini's OpenAI endpoint).
            is_native = ":generateContent" in base

            if is_native:
                headers = {"Content-Type": "application/json"}
                if api_key_val:
                    headers["X-goog-api-key"] = api_key_val
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"text": prompt},
                                {"inline_data": {"mime_type": "image/png", "data": b64}},
                            ]
                        }
                    ],
                    "generationConfig": {"maxOutputTokens": 32, "temperature": 0.1},
                    "safetySettings": [
                        {"category": c, "threshold": "BLOCK_NONE"}
                        for c in (
                            "HARM_CATEGORY_HARASSMENT",
                            "HARM_CATEGORY_HATE_SPEECH",
                            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "HARM_CATEGORY_CIVIC_INTEGRITY",
                        )
                    ],
                }
                models_to_try = [
                    model,
                    "gemini-flash-lite-latest",
                    "gemini-3.5-flash-lite",
                    "gemini-3.6-flash",
                    "gemini-flash-latest",
                ]
                # Deduplicate while preserving order
                seen = set()
                models_to_try = [m for m in models_to_try if not (m in seen or seen.add(m))]
                for m in models_to_try:
                    target_url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
                    try:
                        resp = requests.post(target_url, headers=headers, json=payload, timeout=20)
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                if parts:
                                    raw = parts[0].get("text", "") or ""
                                    text = re.sub(r"[^A-Za-z0-9]", "", raw)
                                    if settings.captcha_min_length <= len(text) <= settings.captcha_max_length:
                                        logger.info(f"vision_captcha_solved: model={m} | result={text}")
                                        return text
                        logger.warning(f"vision_captcha_model_unsuccessful: model={m} | status={resp.status_code}")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"vision_captcha_model_err: model={m} | {e}")
                        continue
            else:
                url = f"{base}/chat/completions"
                headers = {"Content-Type": "application/json"}
                if api_key_val:
                    headers["Authorization"] = f"Bearer {api_key_val}"
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                                },
                            ],
                        }
                    ],
                    "max_tokens": 32,
                }
                for _attempt in range(3):
                    try:
                        resp = requests.post(url, headers=headers, json=payload, timeout=60)
                        if resp.status_code == 429:
                            logger.warning("vision_captcha_429: rate limited, backing off 60s")
                            time.sleep(60)
                            continue
                        resp.raise_for_status()
                        data = resp.json()
                        raw = data["choices"][0]["message"]["content"] or ""
                        text = re.sub(r"[^A-Za-z0-9]", "", raw)
                        if settings.captcha_min_length <= len(text) <= settings.captcha_max_length:
                            logger.info(f"vision_captcha_solved: result={text}")
                            return text
                        logger.warning(f"vision_captcha_bad: raw={raw!r} cleaned={text!r}")
                        return None
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"vision_captcha_error: {e}")
                        break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"vision_captcha_error: {e}")
        return None

    def solve_external(self, img: Image.Image) -> str | None:
        """Fallback to external captcha solving service (e.g., 2Captcha)."""
        api_key = settings.external_captcha_api_key
        if not api_key:
            logger.debug("external_captcha_not_configured")
            return None

        import base64
        import time


        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        svc_url = str(settings.external_captcha_service_url)
        poll_url = str(settings.external_captcha_result_url)
        poll_int = settings.external_captcha_poll_interval
        timeout = settings.external_captcha_timeout

        try:
            # Submit
            resp = requests.post(
                svc_url,
                data={"key": api_key.get_secret_value(), "method": "base64", "body": b64, "json": 1},
                timeout=30,
            )
            j = resp.json()
            if j.get("status") != 1:
                logger.warning(f"external_captcha_submit_failed: {j}")
                return None
            captcha_id = j["request"]

            # Poll for result
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(poll_int)
                poll_resp = requests.get(
                    poll_url,
                    params={"key": api_key.get_secret_value(), "action": "get", "id": captcha_id, "json": 1},
                    timeout=30,
                )
                poll_j = poll_resp.json()
                if poll_j.get("status") == 1:
                    text = poll_j["request"]
                    logger.info(f"external_captcha_solved: result={text}")
                    return self._validate(text)
                if poll_j.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
                    logger.warning("external_captcha_unsolvable")
                    break
            logger.warning("external_captcha_timeout")
        except Exception as e:
            logger.error(f"external_captcha_error: {e}")
        return None


captcha_solver = CaptchaSolver()
