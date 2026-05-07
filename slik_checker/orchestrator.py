"""Orchestrator — core engine that ties all modules together and handles the submission flow."""

from __future__ import annotations

import time
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
        logger.info(f"registration_start: nama={nama} | nik={nik}")

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
            logger.error(f"registration_failed: reason={'no_captcha_engines'}")
            db.add_result(debitur_id, "ERROR", False, raw="No captcha engines available")
            return {
                "debitur_id": debitur_id,
                "success": False,
                "status": "ERROR",
                "message": "No captcha engines available",
                "nomor_pendaftaran": None,
            }

        jd_id, kw_id, ident_id = _form_ids(jenis_debitur, kewarganegaraan, jenis_identitas)

        try:
            scraper.reset()
            scraper.prime_session(jd_id, kw_id)
        except Exception as e:
            logger.error(f"session_prime_failed: error={str(e)}")
            db.add_result(debitur_id, "ERROR", False, raw=str(e))
            return {
                "debitur_id": debitur_id,
                "success": False,
                "status": "ERROR",
                "message": f"Session init failed: {e}",
                "nomor_pendaftaran": None,
            }

        last_status = "ERROR"
        last_captcha: Optional[str] = None
        max_attempts = settings.captcha_max_attempts

        for attempt in range(1, max_attempts + 1):
            try:
                html, soup = scraper.fetch_page(str(settings.pre_register_url))

                if scraper.detect_kuota(html):
                    result = parser.parse_pre_register(html)
                    db.add_result(debitur_id, "QUOTA_FULL", False, raw=html)
                    logger.info(f"registration_quota: nama={nama} | attempt={attempt}")
                    return self._quota_response(debitur_id, result)

                captcha_bytes = scraper.fetch_captcha()
                captcha_text = captcha_solver.solve_from_bytes(captcha_bytes)
                last_captcha = captcha_text

                if not captcha_text:
                    logger.warning(f"captcha_ocr_none: attempt={attempt}")
                    continue

                hidden = scraper.extract_hidden_inputs(soup, "FormPreRegister")
                data = dict(hidden)
                data.update(
                    {
                        "JDEBITUR_ID": str(jd_id),
                        "SDEBITUR_ID": str(kw_id),
                        "IDENTITAS_ID": str(ident_id),
                        "TDAFTAR_IDENTITAS_NO": nik,
                        "CaptchaWsCode": captcha_text,
                        "ReCaptchaToken": "tidakdigunakan",
                        "postm": scraper.build_postm(html),
                    }
                )

                status_code, resp_soup = scraper.post_form(
                    str(settings.pre_register_url),
                    data,
                )
                result = parser.parse_pre_register(str(resp_soup))
                last_status = result.status

                logger.info(
                    f"registration_attempt: attempt={attempt} | captcha={captcha_text} | status={result.status} | http={status_code}"
                )

                if result.status == "QUOTA_FULL":
                    db.add_result(debitur_id, "QUOTA_FULL", False, raw=str(resp_soup))
                    return self._quota_response(debitur_id, result)

                if result.status in ("NEXT_STEP", "SUBMITTED", "REGISTERED"):
                    db.add_result(
                        debitur_id,
                        result.status,
                        True,
                        nomor=result.nomor_pendaftaran,
                        raw=str(resp_soup),
                    )

                    if result.nomor_pendaftaran:
                        db.update_pendaftaran(debitur_id, result.nomor_pendaftaran)
                        notifier.notify_registration(nama, result.nomor_pendaftaran)

                    logger.info(
                        "registration_success",
                        nama=nama,
                        status=result.status,
                        nomor=result.nomor_pendaftaran,
                        attempts=attempt,
                    )
                    return {
                        "debitur_id": debitur_id,
                        "success": True,
                        "status": result.status,
                        "nomor_pendaftaran": result.nomor_pendaftaran,
                        "message": result.message,
                    }

            except QuotaFullError:
                raise
            except Exception as e:
                logger.error(f"registration_attempt_failed: attempt={attempt}")
                time.sleep(1)

        db.add_result(
            debitur_id,
            last_status,
            False,
            raw=f"All {max_attempts} attempts failed. Last captcha: {last_captcha}",
        )
        logger.warning(
            f"registration_all_attempts_failed: nama={nama} | attempts={max_attempts} | last_captcha={last_captcha}"
        )

        return {
            "debitur_id": debitur_id,
            "success": False,
            "status": "ERROR",
            "nomor_pendaftaran": None,
            "message": f"Gagal setelah {max_attempts} percobaan. Last captcha: {last_captcha}",
        }

    def check_status(
        self,
        debitur_id: int,
        schedule_id: Optional[int] = None,
        nomor_pendaftaran: Optional[str] = None,
        notify_telegram: bool = True,
        notify_email: bool = False,
    ) -> dict[str, Any]:
        debitur = db.get_debitur(debitur_id)
        if not debitur:
            return {"success": False, "status": "ERROR", "message": "Debitur not found"}

        # --- Phase 1: ensure we have a nomor_pendaftaran ---
        attempts = 0
        max_attempts = settings.captcha_max_attempts * 2
        debitur = db.get_debitur(debitur_id) or {}
        nomor = nomor_pendaftaran or debitur.get("nomor_pendaftaran", "")

        while not nomor and attempts < max_attempts:
            attempts += 1
            logger.info(f"retry_register: attempt={attempts}/{max_attempts}")

            reg = self.submit_registration(
                nama=debitur["nama"],
                nik=debitur["nik"],
                tempat_lahir=debitur.get("tempat_lahir", ""),
                tanggal_lahir=debitur.get("tanggal_lahir", ""),
                jenis_debitur=debitur.get("jenis_debitur", "Perseorangan"),
                kewarganegaraan=debitur.get("kewarganegaraan", "WNI"),
                jenis_identitas=debitur.get("jenis_identitas", "KTP"),
            )
            if reg.get("status") == "QUOTA_FULL":
                return {
                    "debitur_id": debitur_id,
                    "success": False,
                    "status": "QUOTA_FULL",
                    "message": reg.get("message", ""),
                    "nomor_pendaftaran": None,
                }
            debitur = db.get_debitur(debitur_id) or {}
            nomor = debitur.get("nomor_pendaftaran", "")
            if nomor:
                break
            time.sleep(1)

        if not nomor:
            return {
                "debitur_id": debitur_id,
                "success": False,
                "status": "ERROR",
                "message": f"Gagal mendaftar setelah {max_attempts}x percobaan",
                "nomor_pendaftaran": None,
            }

        # --- Phase 2: check status with retries ---
        nama = debitur["nama"]
        logger.info(f"status_check_begin: nama={nama} | nomor={nomor}")
        old_status = db.get_latest_result_status(debitur_id, nomor)
        result = None

        for attempt in range(1, max_attempts + 1):
            try:
                scraper.reset()
                scraper.prime_session()

                html, soup = scraper.fetch_page(str(settings.status_url))
                if scraper.detect_kuota(html):
                    r = parser.parse_status(html)
                    db.add_result(
                        debitur_id, "QUOTA_FULL", False, schedule_id=schedule_id, raw=html
                    )
                    return {
                        "debitur_id": debitur_id,
                        "success": False,
                        "status": "QUOTA_FULL",
                        "message": r.message,
                        "nomor_pendaftaran": nomor,
                    }

                captcha_bytes = scraper.fetch_captcha()
                captcha_text = captcha_solver.solve_from_bytes(captcha_bytes)

                hidden = scraper.extract_hidden_inputs(soup)
                data = dict(hidden)
                for inp in soup.find_all("input"):
                    fn = inp.get("name", "")
                    if inp.get("type") in ("hidden", "submit", "button"):
                        continue
                    if "nomor" in fn.lower() or "pendaftaran" in fn.lower():
                        data[fn] = nomor
                    elif "captcha" in fn.lower() and captcha_text:
                        data[fn] = captcha_text

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

                logger.info(f"status_check_attempt: attempt={attempt} | status={result.status}")

                if result.status in (
                    "QUOTA_FULL",
                    "COMPLETED",
                    "PROCESSING",
                    "REJECTED",
                    "WAITING",
                ):
                    if result.status != old_status and result.status != "UNKNOWN":
                        notifier.notify_status_change(
                            nama,
                            nomor,
                            result.status,
                            telegram=notify_telegram,
                            email=notify_email,
                        )
                    return {
                        "debitur_id": debitur_id,
                        "success": True,
                        "status": result.status,
                        "nomor_pendaftaran": nomor,
                        "message": result.message,
                    }

                time.sleep(1)

            except Exception as e:
                logger.warning(f"status_check_attempt_failed: attempt={attempt} | error={e}")
                time.sleep(1)

        fallback = result or parser.ParseResult(
            True, status="UNKNOWN", message="No definitive result"
        )
        return {
            "debitur_id": debitur_id,
            "success": False,
            "status": "ERROR",
            "nomor_pendaftaran": nomor,
            "message": f"Gagal cek status setelah {max_attempts}x percobaan. "
            f"Terakhir: {fallback.status}",
        }

    def _quota_response(self, debitur_id: int, result) -> dict[str, Any]:
        return {
            "debitur_id": debitur_id,
            "success": False,
            "status": "QUOTA_FULL",
            "nomor_pendaftaran": None,
            "message": result.message,
            "extra": result.extra,
        }


orchestrator = Orchestrator()
