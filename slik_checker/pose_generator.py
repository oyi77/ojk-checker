"""Smart Challenge Pose & Selfie Generator for iDebKu OJK.

Handles dynamic challenge gestures (e.g. 1A_B, 2A_B, 3A_B, 5A_B, JA_B)
and automatic selfie synthesis when only an e-KTP photo is provided.

Pipeline:
1. Preset gesture photo lookup in `data/ktp/{nik}/` (fastest & 100% human-verified).
2. AI Selfie Synthesis from KTP (when no selfie is uploaded).
3. AI Challenge Pose Generation matching the exact OJK hand gesture.
4. Graceful fallback cascade to ensure registration is never blocked.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from slik_checker.config import settings
from slik_checker.logging_config import get_logger

logger = get_logger(__name__)

# Standard iDebKu challenge gesture definitions with anatomical precision
GESTURE_DESCRIPTIONS = {
    "1": "Hand gesture showing exactly 1 finger (index finger pointing straight up near chest level)",
    "2": "Hand gesture showing exactly 2 fingers (peace sign / V-sign with index and middle fingers)",
    "3": "Hand gesture showing exactly 3 fingers (thumb, index, and middle finger raised clearly)",
    "4": "Hand gesture showing exactly 4 fingers raised clearly near chest level",
    "5": "Hand gesture showing exactly 5 fingers (open palm facing camera with all 5 fingers visible)",
    "J": "Hand gesture showing a clear thumbs-up (jempol) near chest level",
}


def parse_challenge_gesture(challenge_code: str) -> tuple[str, str]:
    """Parse challenge code into gesture key and full description.

    Examples:
        '5A_B' -> ('5', 'Hand gesture showing 5 fingers...')
        '3A_B' -> ('3', 'Hand gesture showing 3 fingers...')
        'JA_B' -> ('J', 'Hand gesture showing a thumbs-up...')
    """
    code_clean = challenge_code.strip().upper()
    m = re.match(r"^([1-5J])", code_clean)
    key = m.group(1) if m else "5"
    desc = GESTURE_DESCRIPTIONS.get(key, GESTURE_DESCRIPTIONS["5"])
    return key, desc


class PoseGenerator:
    """Smart challenge pose & selfie generator combining local preset banks and AI."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path("data/ktp")

    def find_local_preset(self, nik: str, gesture_key: str) -> Path | None:
        """Search for a matching pre-taken human gesture photo in the user's folder."""
        search_dirs = [self.base_dir / nik, self.base_dir]
        aliases = [gesture_key, gesture_key.lower()]
        if gesture_key == "J":
            aliases.extend(["jempol", "thumb", "thumbs_up"])

        for d in search_dirs:
            if not d.exists():
                continue
            for alias in aliases:
                patterns = [
                    f"pose_{alias}.*",
                    f"pose_{alias}_*.*",
                    f"*{alias}*.*",
                ]
                for pat in patterns:
                    for f in d.glob(pat):
                        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                            logger.info(f"pose_preset_found: nik={nik} | file={f.name}")
                            return f
        return None

    def _call_gemini_image_generation(self, reference_path: Path, prompt: str, out_file: Path) -> Path | None:
        """Helper to invoke Gemini multimodal image generation/editing endpoint."""
        api_key = settings.vision_captcha_api_key
        if not api_key:
            logger.debug("ai_generation_skipped: no API key configured")
            return None

        api_key_val = api_key.get_secret_value()
        try:
            img = Image.open(reference_path)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64_img = base64.b64encode(buf.getvalue()).decode()

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash-image:generateContent?key={api_key_val}"
            )
            payload: dict[str, Any] = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
                        ]
                    }
                ],
                "generationConfig": {"responseMimeType": "image/jpeg"},
            }

            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        inline = part.get("inline_data", {})
                        if inline.get("data"):
                            img_bytes = base64.b64decode(inline["data"])
                            out_file.parent.mkdir(parents=True, exist_ok=True)
                            out_file.write_bytes(img_bytes)
                            logger.info(f"ai_photo_generated_success: {out_file}")
                            return out_file

            logger.warning(f"ai_generation_unsuccessful: status={resp.status_code}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ai_generation_err: {e}")

        return None

    def generate_selfie_from_ktp(self, nik: str, ktp_path: Path) -> Path | None:
        """Synthesize an authentic selfie holding e-KTP when only a KTP is provided."""
        target_dir = self.base_dir / nik
        target_dir.mkdir(parents=True, exist_ok=True)
        out_file = target_dir / "selfie_from_ktp.jpg"

        if out_file.exists() and out_file.stat().st_size > 1000:
            logger.info(f"reusing_existing_ktp_selfie: {out_file}")
            return out_file

        prompt = (
            "Authentic unedited front-facing smartphone selfie photograph of the real Indonesian adult "
            "whose face photo appears on the attached identity card (e-KTP). The person is holding their "
            "Indonesian e-KTP card in front of their chest with one hand, facing the camera directly. "
            "Photorealistic smartphone camera photo, real human skin texture, natural ambient indoor room lighting, "
            "eyes looking at camera lens, neutral pleasant expression. The e-KTP is clearly visible without "
            "blocking or covering the person's face. 8k resolution photo quality, zero CGI, zero cartoon, "
            "completely genuine Indonesian human appearance."
        )
        return self._call_gemini_image_generation(ktp_path, prompt, out_file)

    def generate_ai_pose(
        self,
        nik: str,
        reference_path: Path,
        gesture_key: str,
        gesture_desc: str,
    ) -> Path | None:
        """Generate a challenge pose photo matching the requested gesture using AI."""
        target_dir = self.base_dir / nik
        target_dir.mkdir(parents=True, exist_ok=True)
        out_file = target_dir / f"challenge_ai_pose_{gesture_key}.jpg"

        if out_file.exists() and out_file.stat().st_size > 1000:
            logger.info(f"reusing_existing_ai_pose: {out_file}")
            return out_file

        prompt = (
            f"Authentic unedited front-facing smartphone selfie photograph of the exact same Indonesian person "
            f"shown in the reference image. The person is looking directly at the camera with natural lighting, "
            f"holding their Indonesian e-KTP card in one hand while performing a clear {gesture_desc} with the other hand. "
            f"Photorealistic smartphone camera photo, authentic human skin texture, natural hand anatomy with exact "
            f"anatomical finger count, genuine indoor room lighting, zero cartoon, zero CGI, high definition 8k."
        )
        return self._call_gemini_image_generation(reference_path, prompt, out_file)

    def resolve_selfie(
        self,
        nik: str,
        selfie_path: str | Path | None,
        ktp_path: str | Path | None,
    ) -> Path | None:
        """Resolve the selfie photo path. If only KTP is available, synthesizes a selfie."""
        if selfie_path:
            p_selfie = Path(selfie_path)
            if p_selfie.exists():
                return p_selfie

        if ktp_path:
            p_ktp = Path(ktp_path)
            if p_ktp.exists():
                logger.info(f"generating_selfie_from_ktp: nik={nik}")
                gen_selfie = self.generate_selfie_from_ktp(nik, p_ktp)
                if gen_selfie:
                    return gen_selfie
                # Fallback to KTP file directly if AI synthesis unavailable
                logger.info(f"selfie_fallback_to_ktp: {p_ktp}")
                return p_ktp

        return None

    def resolve_pose(
        self,
        nik: str,
        selfie_path: str | Path | None,
        ktp_path: str | Path | None = None,
        challenge_code: str = "5A_B",
    ) -> Path | None:
        """Resolve the final challenge pose photo path across presets, selfie, and KTP-only."""
        gesture_key, gesture_desc = parse_challenge_gesture(challenge_code)

        # 1. Search local preset bank first (0ms, authentic)
        preset = self.find_local_preset(nik, gesture_key)
        if preset:
            return preset

        # 2. Try AI generation using reference selfie if provided
        if selfie_path:
            p_selfie = Path(selfie_path)
            if p_selfie.exists():
                ai_pose = self.generate_ai_pose(nik, p_selfie, gesture_key, gesture_desc)
                if ai_pose:
                    return ai_pose
                logger.info(f"pose_fallback_to_selfie: {p_selfie}")
                return p_selfie

        # 3. KTP-only scenario: generate challenge pose directly from KTP
        if ktp_path:
            p_ktp = Path(ktp_path)
            if p_ktp.exists():
                ai_pose_from_ktp = self.generate_ai_pose(nik, p_ktp, gesture_key, gesture_desc)
                if ai_pose_from_ktp:
                    return ai_pose_from_ktp
                logger.info(f"pose_fallback_to_ktp: {p_ktp}")
                return p_ktp

        return None


pose_generator = PoseGenerator()
