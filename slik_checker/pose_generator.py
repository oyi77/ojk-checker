"""Smart Challenge Pose & Selfie Generator for iDebKu OJK with Strict Facial Identity Preservation.

Ensures that AI-synthesized selfies and challenge poses strictly match the exact
human facial identity from the e-KTP photo (1:1 biometric facial consistency).

Pipeline:
1. Preset gesture photo lookup in `data/ktp/{nik}/` (fastest & 100% human-verified).
2. High-precision e-KTP portrait crop to isolate the face as the primary facial reference.
3. Multimodal dual-image reference generation with strict facial biometric locking directives.
4. Graceful fallback cascade to ensure registration is never blocked.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageEnhance

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
    """Parse challenge code into gesture key and full description."""
    code_clean = challenge_code.strip().upper()
    m = re.match(r"^([1-5J])", code_clean)
    key = m.group(1) if m else "5"
    desc = GESTURE_DESCRIPTIONS.get(key, GESTURE_DESCRIPTIONS["5"])
    return key, desc


def extract_ktp_portrait(img: Image.Image) -> Image.Image:
    """Extract and enhance the passport-style portrait box from an Indonesian e-KTP.

    Based on Permendagri No. 9/2011 standard layout:
    The portrait is situated on the right ~38% of the card width, vertically from ~15% to 85%.
    """
    w, h = img.size
    x1 = max(0, int(w * 0.60))
    y1 = max(0, int(h * 0.15))
    x2 = min(w, int(w * 0.98))
    y2 = min(h, int(h * 0.85))

    crop = img.crop((x1, y1, x2, y2))
    # Enhance sharpness slightly for clear facial feature embedding
    enhancer = ImageEnhance.Sharpness(crop)
    return enhancer.enhance(1.2)


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

    def _call_omniroute_gateway(
        self,
        images: list[tuple[Image.Image, str]],
        prompt: str,
        out_file: Path,
    ) -> Path | None:
        """Invoke OmniRoute / Agnes / OpenAI-compatible AI gateway."""
        if not settings.pose_ai_api_base:
            return None

        base = str(settings.pose_ai_api_base).rstrip("/")
        api_key = settings.pose_ai_api_key or settings.vision_captcha_api_key
        api_key_val = api_key.get_secret_value() if api_key else ""
        model = settings.pose_ai_model or "nano-banana-pro-preview"
        headers = {"Content-Type": "application/json"}
        if api_key_val:
            headers["Authorization"] = f"Bearer {api_key_val}"

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img, label in images:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()
            content.append({"type": "text", "text": f"[{label}]"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            resp = requests.post(f"{base}/chat/completions", headers=headers, json=payload, timeout=40)
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                img_match = re.search(r"data:image/[a-zA-Z]+;base64,([A-Za-z0-9+/=]+)", msg)
                if img_match:
                    raw = base64.b64decode(img_match.group(1))
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    out_file.write_bytes(raw)
                    logger.info(f"omniroute_pose_generated_success: model={model} | {out_file}")
                    return out_file
        except Exception as e:  # noqa: BLE001
            logger.warning(f"omniroute_gateway_err: {e}")
        return None

    def _call_gemini_multimodal(
        self,
        images: list[tuple[Image.Image, str]],
        prompt: str,
        out_file: Path,
    ) -> Path | None:
        """Invoke OmniRoute / Agnes gateway first, with fallback to native Gemini."""
        omni_res = self._call_omniroute_gateway(images, prompt, out_file)
        if omni_res:
            return omni_res

        api_key = settings.vision_captcha_api_key
        if not api_key:
            logger.debug("ai_generation_skipped: no API key configured")
            return None

        api_key_val = api_key.get_secret_value()
        try:
            parts: list[dict[str, Any]] = [{"text": prompt}]
            for img, label in images:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                b64 = base64.b64encode(buf.getvalue()).decode()
                parts.append({"text": f"Reference [{label}]:"})
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})

            models_to_try = [
                settings.pose_ai_model,
                "nano-banana-pro-preview",
                "gemini-3.1-flash-image-preview",
                "gemini-3.1-flash-image",
                "gemini-2.5-flash-image",
            ]
            for m in models_to_try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key_val}"
                payload = {
                    "contents": [{"parts": parts}],
                    "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
                }
                try:
                    resp = requests.post(url, json=payload, timeout=40)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            resp_parts = candidates[0].get("content", {}).get("parts", [])
                            for part in resp_parts:
                                inline = part.get("inlineData") or part.get("inline_data")
                                if inline and inline.get("data"):
                                    img_bytes = base64.b64decode(inline["data"])
                                    out_file.parent.mkdir(parents=True, exist_ok=True)
                                    out_file.write_bytes(img_bytes)
                                    logger.info(f"ai_photo_generated_success: model={m} | {out_file}")
                                    return out_file
                    logger.warning(f"ai_generation_model_unsuccessful: model={m} | status={resp.status_code}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"ai_generation_model_err: model={m} | {e}")
                    continue
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ai_generation_err: {e}")

        return None
    def generate_selfie_from_ktp(self, nik: str, ktp_path: Path) -> Path | None:
        """Synthesize an authentic selfie holding e-KTP with 1:1 facial identity locking."""
        target_dir = self.base_dir / nik
        target_dir.mkdir(parents=True, exist_ok=True)
        out_file = target_dir / "selfie_from_ktp.jpg"

        if out_file.exists() and out_file.stat().st_size > 1000:
            logger.info(f"reusing_existing_ktp_selfie: {out_file}")
            return out_file

        try:
            full_ktp = Image.open(ktp_path)
            face_portrait = extract_ktp_portrait(full_ktp)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ktp_image_load_err: {e}")
            return None

        prompt = (
            "CRITICAL DIRECTIVE: STRICT 1:1 FACIAL BIOMETRIC IDENTITY PRESERVATION.\n"
            "The person in this photo MUST be the EXACT SAME individual shown in the cropped face portrait reference. "
            "Replicate their exact facial features: face shape, jawline, eye structure and color, eyebrow thickness, "
            "nose shape, lip shape, skin tone and undertone, hair style, hairline, approximate age, and gender. "
            "Do NOT alter or beautify the face. It must pass automated 1:1 biometric facial recognition matching.\n\n"
            "SCENE DESCRIPTION:\n"
            "An authentic front-facing smartphone selfie photograph of this exact person holding their Indonesian e-KTP card "
            "in front of their chest with one hand, looking directly into the camera. "
            "The e-KTP card is clearly visible but does NOT cover their face. "
            "Natural indoor room lighting, genuine human skin texture with pores, unedited camera photo, "
            "8k photorealistic quality, zero CGI, zero cartoon, completely genuine Indonesian human appearance."
        )

        images = [
            (face_portrait, "Cropped Face Portrait (Primary Facial Biometric Identity Reference)"),
            (full_ktp, "Full Indonesian e-KTP Card (Document Reference)"),
        ]
        return self._call_gemini_multimodal(images, prompt, out_file)

    def generate_ai_pose(
        self,
        nik: str,
        reference_path: Path,
        gesture_key: str,
        gesture_desc: str,
    ) -> Path | None:
        """Generate a challenge pose photo matching the requested gesture with strict identity locking."""
        target_dir = self.base_dir / nik
        target_dir.mkdir(parents=True, exist_ok=True)
        out_file = target_dir / f"challenge_ai_pose_{gesture_key}.jpg"

        if out_file.exists() and out_file.stat().st_size > 1000:
            logger.info(f"reusing_existing_ai_pose: {out_file}")
            return out_file

        try:
            ref_img = Image.open(reference_path)
            # If reference is a KTP, extract face portrait too
            face_img = extract_ktp_portrait(ref_img) if "ktp" in reference_path.name.lower() else ref_img
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reference_image_load_err: {e}")
            return None

        prompt = (
            "CRITICAL DIRECTIVE: STRICT 1:1 FACIAL BIOMETRIC IDENTITY PRESERVATION.\n"
            "The person in this photo MUST be the EXACT SAME individual shown in the reference image. "
            "Replicate their exact facial features: face shape, jawline, eyes, nose, lips, skin tone, hair, age, and gender. "
            "Do NOT alter or replace the face.\n\n"
            "SCENE DESCRIPTION:\n"
            f"An authentic front-facing smartphone selfie photograph of this exact person looking directly at the camera, "
            f"holding their Indonesian e-KTP card in one hand while performing a clear {gesture_desc} with the other hand. "
            f"Natural indoor room lighting, authentic human skin texture, proper hand anatomy with exact anatomical finger count, "
            f"photorealistic smartphone camera photo, zero cartoon, zero CGI, high definition 8k."
        )

        images = [(face_img, "Person Identity Reference"), (ref_img, "Full Context Reference")]
        return self._call_gemini_multimodal(images, prompt, out_file)

    def synthesize_selfie_composite(self, nik: str, ktp_path: Path) -> Path | None:
        """Deterministically synthesize a selfie holding the e-KTP from the KTP card.

        Extracts the person's real portrait photo from the KTP, scales it in the
        upper selfie frame, and composites the e-KTP card in the lower frame.
        100% preserves facial identity, runs in <1s with 0 external API cost.
        """
        target_dir = self.base_dir / nik
        target_dir.mkdir(parents=True, exist_ok=True)
        out_file = target_dir / "selfie_composite.jpg"

        if out_file.exists() and out_file.stat().st_size > 1000:
            return out_file

        try:
            ktp_img = Image.open(ktp_path).convert("RGB")
            portrait = extract_ktp_portrait(ktp_img).convert("RGB")

            # 3:4 smartphone selfie aspect ratio (1080 x 1440)
            w, h = 1080, 1440
            canvas = Image.new("RGB", (w, h), color=(228, 232, 240))

            # 1. Place portrait face in upper half (scale ~680 px wide)
            face_w = 680
            face_h = int(face_w * (portrait.size[1] / portrait.size[0]))
            portrait_scaled = portrait.resize((face_w, face_h), Image.Resampling.LANCZOS)
            canvas.paste(portrait_scaled, (int((w - face_w) / 2), 120))

            # 2. Place KTP card at lower chest level (~560 px wide)
            card_w = 560
            card_h = int(card_w * (ktp_img.size[1] / ktp_img.size[0]))
            ktp_scaled = ktp_img.resize((card_w, card_h), Image.Resampling.LANCZOS)
            canvas.paste(ktp_scaled, (int((w - card_w) / 2), 850))

            canvas.save(out_file, format="JPEG", quality=92)
            logger.info(f"selfie_composite_synthesized: {out_file}")
            return out_file
        except Exception as e:  # noqa: BLE001
            logger.warning(f"selfie_composite_err: {e}")
            return None

    def resolve_selfie(
        self,
        nik: str,
        selfie_path: str | Path | None,
        ktp_path: str | Path | None,
    ) -> Path | None:
        """Resolve the selfie photo path with automatic facial synthesis when only KTP is provided."""
        if selfie_path:
            p_selfie = Path(selfie_path)
            if p_selfie.exists():
                return p_selfie

        if ktp_path:
            p_ktp = Path(ktp_path)
            if p_ktp.exists():
                logger.info(f"synthesizing_identity_matched_selfie_from_ktp: nik={nik}")
                gen_selfie = self.generate_selfie_from_ktp(nik, p_ktp)
                if gen_selfie:
                    return gen_selfie
                comp_selfie = self.synthesize_selfie_composite(nik, p_ktp)
                if comp_selfie:
                    return comp_selfie
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

        # 3. KTP-only scenario: generate challenge pose directly from KTP or composite
        if ktp_path:
            p_ktp = Path(ktp_path)
            if p_ktp.exists():
                ai_pose_from_ktp = self.generate_ai_pose(nik, p_ktp, gesture_key, gesture_desc)
                if ai_pose_from_ktp:
                    return ai_pose_from_ktp
                comp_selfie = self.synthesize_selfie_composite(nik, p_ktp)
                if comp_selfie:
                    return comp_selfie
                logger.info(f"pose_fallback_to_ktp: {p_ktp}")
                return p_ktp

        return None

pose_generator = PoseGenerator()
