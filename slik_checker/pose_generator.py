"""Smart Challenge Pose Generator & Gesture Selector for iDebKu OJK.

Handles dynamic challenge gestures (e.g. 1A_B, 2A_B, 3A_B, 5A_B, JA_B)
using a hybrid approach:
1. Preset gesture photo lookup in `data/ktp/{nik}/` (fastest & 100% human-verified)
2. AI-assisted gesture generation via Gemini API from a reference selfie
3. Graceful fallback to the reference selfie if generation is unavailable
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

# Standard iDebKu challenge gesture definitions
GESTURE_DESCRIPTIONS = {
    "1": "Hand gesture showing 1 finger (index finger pointing up near chest)",
    "2": "Hand gesture showing 2 fingers (peace sign / V-sign near chest)",
    "3": "Hand gesture showing 3 fingers (thumb, index, and middle finger raised)",
    "4": "Hand gesture showing 4 fingers raised near chest",
    "5": "Hand gesture showing 5 fingers (open palm facing camera near chest)",
    "J": "Hand gesture showing a thumbs-up (jempol) near chest",
}


def parse_challenge_gesture(challenge_code: str) -> tuple[str, str]:
    """Parse challenge code into gesture key and full description.

    Examples:
        '5A_B' -> ('5', 'Hand gesture showing 5 fingers...')
        '3A_B' -> ('3', 'Hand gesture showing 3 fingers...')
        'JA_B' -> ('J', 'Hand gesture showing a thumbs-up...')
    """
    code_clean = challenge_code.strip().upper()
    # Extract first digit or letter
    m = re.match(r"^([1-5J])", code_clean)
    key = m.group(1) if m else "5"
    desc = GESTURE_DESCRIPTIONS.get(key, GESTURE_DESCRIPTIONS["5"])
    return key, desc


class PoseGenerator:
    """Smart challenge pose resolver combining local preset banks and AI generation."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path("data/ktp")

    def find_local_preset(self, nik: str, gesture_key: str) -> Path | None:
        """Search for a matching pre-taken human gesture photo in the user's folder.

        Accepts filenames like:
            data/ktp/{nik}/pose_1.jpg, pose_2.jpg, pose_3.jpg, pose_5.jpg, pose_jempol.jpg
            data/ktp/pose_5_{nik}.jpg, etc.
        """
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

    def generate_ai_pose(
        self,
        nik: str,
        selfie_path: Path,
        gesture_key: str,
        gesture_desc: str,
    ) -> Path | None:
        """Generate a challenge pose photo matching the requested gesture using AI.

        Uses the reference selfie to preserve person appearance and clothing.
        """
        api_key = settings.vision_captcha_api_key
        if not api_key:
            logger.debug("ai_pose_generation_skipped: no API key configured")
            return None

        api_key_val = api_key.get_secret_value()
        try:
            # Read and encode reference selfie
            img = Image.open(selfie_path)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64_img = base64.b64encode(buf.getvalue()).decode()

            target_dir = self.base_dir / nik
            target_dir.mkdir(parents=True, exist_ok=True)
            out_file = target_dir / f"challenge_ai_pose_{gesture_key}.jpg"

            # If already generated previously for this gesture, reuse it instantly
            if out_file.exists() and out_file.stat().st_size > 1000:
                logger.info(f"reusing_existing_ai_pose: {out_file}")
                return out_file

            # Prompt describing the exact transformation
            prompt = (
                f"Selfie photo of the exact same person in the reference image, "
                f"maintaining facial features, background, and lighting, but adding "
                f"a natural {gesture_desc}."
            )

            # Use Gemini / Imagen image generation/editing endpoint
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={api_key_val}"
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
                            out_file.write_bytes(img_bytes)
                            logger.info(f"ai_pose_generated_success: {out_file}")
                            return out_file

            logger.warning(f"ai_pose_generation_unsuccessful: status={resp.status_code}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ai_pose_generation_err: {e}")

        return None

    def resolve_pose(
        self,
        nik: str,
        selfie_path: str | Path | None,
        challenge_code: str = "5A_B",
    ) -> Path | None:
        """Resolve the final challenge pose photo path.

        Order of resolution:
        1. Local preset bank photo matching gesture (0ms, 100% human authentic)
        2. AI-generated pose derived from reference selfie
        3. Fallback to original reference selfie
        """
        gesture_key, gesture_desc = parse_challenge_gesture(challenge_code)

        # 1. Search local preset bank
        preset = self.find_local_preset(nik, gesture_key)
        if preset:
            return preset

        # 2. Try AI generation if reference selfie exists
        if selfie_path:
            p_selfie = Path(selfie_path)
            if p_selfie.exists():
                ai_pose = self.generate_ai_pose(nik, p_selfie, gesture_key, gesture_desc)
                if ai_pose:
                    return ai_pose
                # 3. Fallback to reference selfie
                logger.info(f"pose_fallback_to_selfie: {p_selfie}")
                return p_selfie

        return None


pose_generator = PoseGenerator()
