"""Orchestrator — core engine. One consolidated result per run, no noise."""

from __future__ import annotations

import time
import traceback
from typing import Any, Optional

from slik_checker.captcha import captcha_solver
from slik_checker.config import settings
from slik_checker.exceptions import QuotaFullError
from slik_checker.logging_config import get_logger
from slik_checker.models import db
from slik_checker.notifier import notifier
from slik_checker.parser import parser
from slik_checker.scraper import scraper

logger = get_logger(__name__)

# Fallback captcha text for when all OCR attempts fail (should be replaced with manual input in production)
FALLBACK_CAPTCHA = "FALLBACK123"


def _is_captcha_plausible(text: str) -> bool:
    """Heuristic to check if captcha text looks human-readable."""
    if not text:
        return False
    # Allow only alphabetic characters
    if not text.isalpha():
        return False
    # Length typical for iDebKu captcha
    if not (4 <= len(text) <= 6):
        return False
    # Contains at least one vowel
    vowels = set("aeiouAEIOU")
    if not any(c in vowels for c in text):
        return False
    return True

FORM_IDS: dict[tuple[str, str, str], tuple[int, int, int]] = {
    ("Perseorangan", "WNI", "KTP"): (1, 1, 1),
    ("Perseorangan", "WNI", "NPWP"): (1, 1, 22),
    ("Perseorangan", "WNA", "KTP"): (1, 2, 1),
    ("Perseorangan", "WNA", "Paspor"): (1, 2, 21),
    ("Badan Usaha", "WNI", "KTP"): (2, 1, 1),
    ("Badan Usaha", "WNA", "Paspor"): (2, 2, 21),
    ("Debitur Meninggal Dunia", "WNI", "KTP"): (21, 1, 1),
}


def _form_ids(jenis: str, warga: str, ident: str) -> tuple[int, int, int]:
    return FORM_IDS.get((jenis, warga, ident), (1, 1, 1))


class Orchestrator:
    # ── internal helpers ───────────────────────────────────────────────

    def _one_attempt(self, nik: str, jd_id: int, kw_id: int, ident_id: int) -> dict[str, Any]:
        """Single attempt: fetch page → solve captcha → submit → parse."""
        html, soup = scraper.fetch_page(str(settings.pre_register_url))

        # Quota handling with retries
        max_quota_retries = 3
        quota_delay = 60  # seconds

        for attempt in range(max_quota_retries):
            if not scraper.detect_kuota(html):
                break
            logger.debug(f"Quota check failed: attempt={attempt+1}")
            if attempt < max_quota_retries - 1:
                time.sleep(quota_delay)
                quota_delay *= 2
                # re-fetch page for next attempt
                html, soup = scraper.fetch_page(str(settings.pre_register_url))
                logger.debug(f"Re-fetching page after quota delay {quota_delay}s")
        else:
            # still quota after retries
            r = parser.parse_pre_register(html)
            return {
                "success": False,
                "status": "QUOTA_FULL",
                "message": r.message,
                "extra": r.extra,
            }

        captcha_bytes = scraper.fetch_captcha()
        captcha_text = captcha_solver.solve_from_bytes(captcha_bytes)
        if not captcha_text:
            logger.warning("captcha_failed_fallback")
            captcha_text = FALLBACK_CAPTCHA

        # Pre-submission validation
        required_fields = ['JDEBITUR_ID', 'SDEBITUR_ID', 'IDENTITAS_ID', 'TDAFTAR_IDENTITAS_NO', 'CaptchaWsCode']
        for field in required_fields:
            if field not in data:
                logger.error(f"Missing required field: {field}")
                return {
                    "success": False,
                    "status": "MISSING_FIELDS",
                    "message": f"Missing required field: {field}",
                }

        # HTTP status check
        status_code, resp_soup = scraper.post_form(str(settings.pre_register_url), data)
        if status_code != 200:
            logger.warning(f"form_submit_bad_status: status={status_code}")
            return {
                "success": False,
                "status": "HTTP_ERROR",
                "message": f"Form submission returned status {status_code}",
            }

        result = parser.parse_pre_register(str(resp_soup))
        return {
            "success": result.success,
            "status": result.status,
            "nomor_pendaftaran": result.nomor_pendaftaran,
            "message": result.message,
        }

    # ── public API ─────────────────────────────────────────────────────

    def submit_registration(
        self,
        nama: str,
        nik: str,
        tempat_lahir: str = "",
        tanggal_lahir: str = "",
        kewarganegaraan: str = "WNI",
        jenis_identitas: str = "KTP",
        email: str = "",
        nomor_hp: str = "",
        jenis_debitur: str = "Perseorangan",
        ktp_path: str = "",
    ) -> dict[str, Any]:
        logger.info(f"register: nama={nama} | nik={nik}")

        debitur_id = db.upsert_debitur(
            nama=nama,
            nik=nik,
            tempat_lahir=tempat_lahir,
            tanggal_lahir=tanggal_lahir,
            kewarganegaraan=kewarganegaraan,
            jenis_identitas=jenis_identitas,
            email=email,
            nomor_hp=nomor_hp,
            jenis_debitur=jenis_debitur,
            ktp_path=ktp_path,
        )

        if not captcha_solver.available:
            db.add_result(debitur_id, "ERROR", False)
            return {
                "debitur_id": debitur_id,
                "success": False,
                "status": "ERROR",
                "message": "No captcha engines",
                "nomor_pendaftaran": None,
            }

        jd_id, kw_id, ident_id = _form_ids(jenis_debitur, kewarganegaraan, jenis_identitas)
        max_rounds = settings.captcha_max_attempts

        try:
            scraper.reset()
            scraper.prime_session(jd_id, kw_id)
        except Exception as e:
            db.add_result(debitur_id, "ERROR", False)
            db.add_log(
                message=f"Error prime_session: {str(e)}",
                level="ERROR",
                detail=traceback.format_exc(),
                debitur_id=debitur_id,
            )
            logger.error(f"prime_session_error: debitur_id={debitur_id} | {e}")
            return {
                "debitur_id": debitur_id,
                "success": False,
                "status": "ERROR",
                "message": str(e),
                "nomor_pendaftaran": None,
            }

        for _ in range(max_rounds):
            try:
                attempt = self._one_attempt(nik, jd_id, kw_id, ident_id)
                if attempt["status"] in ("QUOTA_FULL", "NEXT_STEP", "SUBMITTED", "REGISTERED"):
                    break
            except Exception as e:
                db.add_log(
                    message=f"Error attempt: {str(e)}",
                    level="ERROR",
                    detail=traceback.format_exc(),
                    debitur_id=debitur_id,
                )
                logger.error(f"attempt_error: debitur_id={debitur_id} | {e}")
                time.sleep(1)
                continue

        status = attempt["status"]
        is_success = status in ("NEXT_STEP", "SUBMITTED", "REGISTERED")
        db.add_result(debitur_id, status, is_success, nomor=attempt.get("nomor_pendaftaran"))

        if status == "QUOTA_FULL":
            logger.info(f"register: nama={nama} → QUOTA_FULL")
            return {
                "debitur_id": debitur_id,
                "success": False,
                "status": "QUOTA_FULL",
                "nomor_pendaftaran": None,
                "message": attempt.get("message", "Kuota penuh"),
                "extra": attempt.get("extra"),
            }
        if is_success:
            logger.info(f"register: nama={nama} → {status}")
            return {
                "debitur_id": debitur_id,
                "success": True,
                "status": status,
                "nomor_pendaftaran": attempt.get("nomor_pendaftaran"),
                "message": attempt.get("message", ""),
            }
        logger.warning(f"register: nama={nama} → GAGAL ({max_rounds}x)")
        return {
            "debitur_id": debitur_id,
            "success": False,
            "status": "ERROR",
            "nomor_pendaftaran": None,
            "message": f"Gagal setelah {max_rounds}x percobaan",
        }

    def check_status(
        self,
        debitur_id: int,
        schedule_id: int | None = None,
        nomor_pendaftaran: str | None = None,
        notify_telegram: bool = True,
        notify_email: bool = False,
    ) -> dict[str, Any]:
        max_rounds = settings.captcha_max_attempts * 2
        debitur = db.get_debitur(debitur_id)
        if not debitur:
            return {"success": False, "status": "ERROR", "message": "Debitur not found"}
        nama = debitur["nama"]
        nik = debitur["nik"]
        nomor = nomor_pendaftaran or debitur.get("nomor_pendaftaran", "")

        # ── Phase 1: ensure nomor ──
        if not nomor:
            for _ in range(max_rounds):
                time.sleep(1)
                try:
                    a = self._one_attempt(nik, 1, 1, 1)
                    if a["status"] == "QUOTA_FULL":
                        db.add_result(debitur_id, "QUOTA_FULL", False, schedule_id=schedule_id)
                        logger.info(f"status: nama={nama} → QUOTA_FULL (auto-reg)")
                        return {
                            "debitur_id": debitur_id,
                            "success": False,
                            "status": "QUOTA_FULL",
                            "nomor_pendaftaran": None,
                            "message": a.get("message", "Kuota penuh"),
                        }
                except Exception:
                    continue
            debitur = db.get_debitur(debitur_id) or {}
            nomor = debitur.get("nomor_pendaftaran", "")
        if not nomor:
            db.add_result(debitur_id, "ERROR", False, schedule_id=schedule_id)
            logger.warning(f"status: nama={nama} → auto-reg GAGAL")
            return {
                "debitur_id": debitur_id,
                "success": False,
                "status": "ERROR",
                "nomor_pendaftaran": None,
                "message": "Gagal auto-register",
            }

        # ── Phase 2: check status ──
        old = db.get_latest_result_status(debitur_id, nomor)
        logger.info(f"status: nama={nama} | nomor={nomor}")

        for _ in range(max_rounds):
            time.sleep(1)
            try:
                scraper.reset()
                scraper.prime_session()
                html, soup = scraper.fetch_page(str(settings.status_url))

                if scraper.detect_kuota(html):
                    db.add_result(
                        debitur_id, "QUOTA_FULL", False, schedule_id=schedule_id, raw=html
                    )
                    logger.info(f"status: nama={nama} → QUOTA_FULL")
                    return {
                        "debitur_id": debitur_id,
                        "success": False,
                        "status": "QUOTA_FULL",
                        "nomor_pendaftaran": nomor,
                        "message": "Kuota penuh",
                    }

                caps = scraper.fetch_captcha()
                cap = captcha_solver.solve_from_bytes(caps)
                hidden = scraper.extract_hidden_inputs(soup)
                data = dict(hidden)
                for inp in soup.find_all("input"):
                    fn = inp.get("name", "")
                    if inp.get("type") in ("hidden", "submit", "button"):
                        continue
                    if "nomor" in fn.lower() or "pendaftaran" in fn.lower():
                        data[fn] = nomor
                    elif "captcha" in fn.lower() and cap:
                        data[fn] = cap

                sc, rs = scraper.post_form(str(settings.status_url), data)
                result = parser.parse_status(str(rs))
                db.add_result(
                    debitur_id,
                    result.status,
                    result.success,
                    nomor=nomor,
                    schedule_id=schedule_id,
                    raw=str(rs),
                )

                if result.status != old and result.status not in ("UNKNOWN",):
                    notifier.notify_status_change(
                        nama,
                        nomor,
                        result.status,
                        telegram=notify_telegram,
                        email=notify_email,
                    )

                logger.info(f"status: nama={nama} → {result.status}")
                return {
                    "debitur_id": debitur_id,
                    "success": True,
                    "status": result.status,
                    "nomor_pendaftaran": nomor,
                    "message": result.message,
                }
            except Exception:
                continue

        db.add_result(debitur_id, "ERROR", False, nomor=nomor, schedule_id=schedule_id)
        logger.warning(f"status: nama={nama} → GAGAL ({max_rounds}x)")
        return {
            "debitur_id": debitur_id,
            "success": False,
            "status": "ERROR",
            "nomor_pendaftaran": nomor,
            "message": f"Gagal cek ({max_rounds}x)",
        }


orchestrator = Orchestrator()
