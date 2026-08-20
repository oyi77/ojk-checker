"""Orchestrator — core engine. One consolidated result per run, no noise."""

import io
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from slik_checker.captcha import captcha_solver
from slik_checker.config import settings
from slik_checker.exceptions import CaptchaSolverError, ScrapingError, SlikError
from slik_checker.logging_config import get_logger
from slik_checker.models import db
from slik_checker.notifier import notifier
from slik_checker.parser import parser
from slik_checker.pose_generator import pose_generator
from slik_checker.scraper import scraper

logger = get_logger(__name__)


@dataclass
class RegistrationInput:
    """All fields required to submit an iDebKu pre-registration and full registration form."""

    nama: str
    nik: str
    tempat_lahir: str = ""
    tanggal_lahir: str = ""
    kewarganegaraan: str = "WNI"
    jenis_identitas: str = "KTP"
    email: str = ""
    nomor_hp: str = ""
    jenis_debitur: str = "Perseorangan"
    jenis_kelamin: str = "L"
    alamat: str = ""
    kode_provinsi: str = "12"
    kode_kota: str = "1204"
    ibu_kandung: str = ""
    tujuan_permohonan: int = 41
    foto_ktp_path: str = ""
    foto_selfie_path: str = ""
    foto_challenge_path: str = ""
    jd_id: int = 1
    kw_id: int = 1
    ident_id: int = 1

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
    # iDebKu captchas are frequently consonant-only, so do NOT require a vowel.
    # Only reject output that isn't plain ASCII alphanumerics; the portal itself
    # validates the actual captcha value on submit (and we retry on rejection).
    return bool(text) and all(
        c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        for c in text
    )

FORM_IDS: dict[tuple[str, str, str], tuple[int, int, int]] = {
    ("Perseorangan", "WNI", "KTP"): (1, 1, 1),
    ("Perseorangan", "WNI", "NPWP"): (1, 1, 22),
    ("Perseorangan", "WNA", "KTP"): (1, 2, 1),
    ("Perseorangan", "WNA", "Paspor"): (1, 2, 21),
    ("Badan Usaha", "WNI", "KTP"): (2, 1, 1),
    ("Badan Usaha", "WNA", "Paspor"): (2, 2, 21),
    ("Debitur Meninggal Dunia", "WNI", "KTP"): (21, 1, 1),
}
MAX_WIZARD_STEPS = 6  # iDebKu pre-register wizard is short; cap defensively.

def _form_ids(jenis: str, warga: str, ident: str) -> tuple[int, int, int]:
    return FORM_IDS.get((jenis, warga, ident), (1, 1, 1))


class Orchestrator:
    # ── internal helpers ───────────────────────────────────────────────

    def _build_registration_payload(
        self,
        html: str,
        soup: Any,
        reg: RegistrationInput,
        captcha_text: str,
        server_ts: tuple[int, int, int, int, int, int] | None = None,
    ) -> dict[str, str]:
        """Assemble the POST body: hidden inputs + debtor fields + captcha.

        Field names are matched by substring so the engine survives minor markup
        changes on the iDebKu side (ASP.NET prefixes, renamed controls).
        `postm` is the base64 server-timestamp the live form sets via cmdEncrypt()
        before submit — required by the server's anti-bot check.
        """
        data: dict[str, str] = dict(scraper.extract_hidden_inputs(soup))
        data["postm"] = scraper.build_postm(html, server_ts)

        challenge_code = data.get("TDAFTAR_KODE_CHALLENGE", "5A_B")
        selfie_path = reg.foto_selfie_path
        if not selfie_path and reg.foto_ktp_path:
            p_selfie = pose_generator.resolve_selfie(reg.nik, reg.foto_selfie_path, reg.foto_ktp_path)
            if p_selfie:
                selfie_path = str(p_selfie)

        chal_path = reg.foto_challenge_path
        if not chal_path:
            p_res = pose_generator.resolve_pose(reg.nik, selfie_path, reg.foto_ktp_path, challenge_code)
            if p_res:
                chal_path = str(p_res)

        exact = {
            "JDEBITUR_ID": str(reg.jd_id),
            "SDEBITUR_ID": str(reg.kw_id),
            "IDENTITAS_ID": str(reg.ident_id),
            "TDAFTAR_IDENTITAS_NO": reg.nik,
            "CaptchaWsCode": captcha_text,
            "Idebku_CaptchaInputText": captcha_text,
            "ReCaptchaToken": "tidakdigunakan",
        }
        # DevExpress renders these controls client-side, so they are ABSENT from the
        # static HTML we scrape. Merge them unconditionally — the portal expects them
        # in the POST body regardless of whether the markup is present in our fetch.
        data.update(exact)
        # (fragment, value) — evaluated in order; first match on a control name wins.
        fragments = [
            ("captcha", captcha_text),
            ("tdaftar_nama", reg.nama),
            ("tdaftar_tempat_lahir", reg.tempat_lahir),
            ("tdaftar_tanggal_lahir", reg.tanggal_lahir),
            ("tdaftar_alamat", reg.alamat),
            ("tdaftar_email", reg.email),
            ("tdaftar_hp", reg.nomor_hp),
            ("tdaftar_ibu_kandung", reg.ibu_kandung),
            ("tdaftar_jenkel", reg.jenis_kelamin),
            ("jenkel", reg.jenis_kelamin),
            ("province_code", reg.kode_provinsi),
            ("city_code", reg.kode_kota),
            ("tmohon_id", str(reg.tujuan_permohonan)),
            ("noidentitas", reg.nik),
            ("noindentitas", reg.nik),
            ("nama", reg.nama),
            ("nik", reg.nik),
            ("tempat", reg.tempat_lahir),
            ("tanggal", reg.tanggal_lahir),
            ("email", reg.email),
            ("hp", reg.nomor_hp),
            ("telepon", reg.nomor_hp),
            ("jdebitur", str(reg.jd_id)),
            ("kewarganegaraan", str(reg.kw_id)),
            ("ssdebitur", str(reg.kw_id)),
            ("identitas", str(reg.ident_id)),
            ("alamat", reg.alamat),
            ("ibu", reg.ibu_kandung),
            ("kandung", reg.ibu_kandung),
        ]
        for control in soup.find_all(["input", "select", "textarea"]):
            name = control.get("name")
            if not name:
                continue
            if name in exact:
                data[name] = exact[name]
                continue
            low = name.lower()
            for fragment, value in fragments:
                if fragment in low:
                    data[name] = value
                    break
        return data

    @staticmethod
    def _form_action(soup: Any) -> str | None:
        """Best-guess submit URL for the next wizard step (defaults to base if absent)."""
        form = soup.find("form")
        if not form:
            return None
        action = form.get("action")
        if not action:
            return None
        if action.startswith(("http://", "https://")):
            return action
        base = str(settings.ideb_base_url).rstrip("/")
        return base + (action if action.startswith("/") else "/" + action)

    def _solve_captcha(self) -> str:
        """Solve the captcha image for the current page, or raise CaptchaSolverError.

        - mode "manual": save the captcha image and have the operator type it
          (100% free, no signup — for environments where automated OCR cannot read
          the captcha font). In a TTY it prompts; otherwise it reads the answer from
          data/captcha_answer.txt after the operator views data/captcha_manual.png.
        - mode "auto" (default): try the vision-LLM solver when enabled (free with a
          free-tier API key such as Gemini 2.5 Flash or GLM-4.6v, or a local Ollama
          vision model), then an external service when a key is configured, then the
          local OCR engines.
        - mode "vision": force the vision-LLM solver (requires vision_captcha_enabled).
        """
        # Operator-provided captcha text (free, no OCR). Return it directly so we
        # DON'T fetch a fresh captcha — the submitted value must match the captcha
        # already issued in this session (the portal stores the expected value
        # server-side per session).
        if settings.captcha_override:
            text = settings.captcha_override
            if _is_captcha_plausible(text):
                logger.info(f"captcha_override_used: {text}")
                return text
            raise CaptchaSolverError(f"captcha_override_implausible: {text!r}")
        captcha_bytes = scraper.fetch_captcha()
        img = Image.open(io.BytesIO(captcha_bytes))

        if settings.captcha_mode == "manual":
            out_dir = Path("data")
            out_dir.mkdir(parents=True, exist_ok=True)
            img_path = out_dir / "captcha_manual.png"
            img.save(img_path)
            import sys as _sys
            if _sys.stdin.isatty():
                text = input(f"[captcha] saved to {img_path} — enter the text shown: ").strip()
            else:
                ans = out_dir / "captcha_answer.txt"
                if ans.exists():
                    text = ans.read_text(encoding="utf-8").strip()
                else:
                    raise CaptchaSolverError(
                        f"manual_captcha_no_input: saved {img_path}; "
                        f"view it and write the answer to {ans}"
                    )
            if text and _is_captcha_plausible(text):
                return text
            raise CaptchaSolverError("manual_captcha_empty")
        # Vision-LLM solver — FREE with a free-tier API key / local Ollama.
        # Preferred over the paid external service when enabled.
        if settings.vision_captcha_enabled or settings.captcha_mode == "vision":
            vision_text = captcha_solver.solve_vision(img)
            if vision_text and _is_captcha_plausible(vision_text):
                logger.info(f"vision_captcha_success: {vision_text}")
                return vision_text
            logger.warning("vision_captcha_failed: falling back")

        external_text: str | None = None
        if settings.external_captcha_api_key:
            external_text = captcha_solver.solve_external(img)
            if external_text and _is_captcha_plausible(external_text):
                logger.info(f"external_captcha_success: {external_text}")
                return external_text
            logger.warning("external_captcha_failed: falling back to local OCR")

        local_text = captcha_solver.solve_from_bytes(captcha_bytes)
        if local_text and _is_captcha_plausible(local_text):
            return local_text

        logger.warning("captcha_failed_all")
        raise CaptchaSolverError("captcha_unsolvable")

    def _run_step(
        self,
        html: str,
        soup: Any,
        reg: RegistrationInput,
        url: str,
        guard_fields: list[str] | None = None,
        server_ts: tuple[int, int, int, int, int, int] | None = None,
    ) -> dict[str, Any]:
        """Solve captcha on the current page, submit its form, return parsed result.

        Includes the response html/soup so the caller can follow a multi-step
        wizard (step 2+ is rendered server-side inside the step-1 response).
        """
        captcha_text = self._solve_captcha()
        data = self._build_registration_payload(html, soup, reg, captcha_text, server_ts)

        if guard_fields:
            missing = [f for f in guard_fields if not data.get(f)]
            if missing:
                # Structural: the form markup changed (controls renamed/removed).
                # Retrying cannot fix this, so raise the base SlikError (fatal).
                raise SlikError(f"form_missing_fields: {missing}")

        status_code, resp_soup = scraper.post_form(url, data)
        if status_code != 200:
            raise ScrapingError(f"form_submit_bad_status: status={status_code}")
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/last_registration_response.html").write_text(
                str(resp_soup), encoding="utf-8"
            )
        except Exception:
            pass


        result = parser.parse_pre_register(str(resp_soup))
        return {
            "success": result.success,
            "status": result.status,
            "nomor_pendaftaran": result.nomor_pendaftaran,
            "message": result.message,
            "html": str(resp_soup),
            "soup": resp_soup,
        }

    def _one_attempt(self, reg: RegistrationInput) -> dict[str, Any]:
        """Complete one full registration attempt — follows the multi-step wizard
        (step 1 → step 2 → …) until a terminal state is reached.

        Raises SlikError subclasses for non-retryable failures so the caller
        stops retrying instead of burning every attempt on a structural bug.
        """
        html, soup = scraper.fetch_page(str(settings.pre_register_url))

        # Fast quota retry during registration attempts (re-fetch with short delay)
        max_quota_retries = settings.quota_max_retries
        quota_delay = settings.quota_retry_delay
        for attempt in range(max_quota_retries):
            if not scraper.detect_kuota(html):
                break
            logger.debug(f"quota_full_fast_retry: attempt={attempt + 1}/{max_quota_retries}")
            if attempt < max_quota_retries - 1:
                time.sleep(quota_delay)
                html, soup = scraper.fetch_page(str(settings.pre_register_url))
        if scraper.detect_kuota(html):
            r = parser.parse_pre_register(html)
            return {
                "success": False,
                "status": "QUOTA_FULL",
                "message": r.message,
                "extra": r.extra,
            }
        # Capture the server-issued timestamp ONCE (from step 1) and reuse it for
        # every wizard step. The portal's cmdEncrypt() derives postm from this same
        # server time, so reusing it avoids clock-skew rejection on later steps.
        server_ts = scraper.extract_server_timestamp(html)

        url = str(settings.pre_register_url)
        guard = [
            "JDEBITUR_ID",
            "SDEBITUR_ID",
            "IDENTITAS_ID",
            "TDAFTAR_IDENTITAS_NO",
            "postm",
        ]
        for step_idx in range(MAX_WIZARD_STEPS):
            try:
                step = self._run_step(
                    html, soup, reg, url,
                    guard_fields=guard if step_idx == 0 else None,
                    server_ts=server_ts,
                )
            except (CaptchaSolverError, ScrapingError):
                raise  # propagate so the outer loop retries the whole attempt
            status = step["status"]
            if status == "REGISTERED" and step.get("nomor_pendaftaran"):
                return step
            if status in ("QUOTA_FULL", "ERROR"):
                return step

            # NEXT_STEP / SUBMITTED → the response is the next step's form.
            html = step["html"]
            soup = step["soup"]
            url = self._form_action(soup) or url

        # Wizard did not reach a terminal state within the step cap.
        return {
            "success": False,
            "status": "ERROR",
            "message": "wizard_exceeded_max_steps",
            "nomor_pendaftaran": None,
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
        jenis_kelamin: str = "L",
        alamat: str = "",
        kode_provinsi: str = "12",
        kode_kota: str = "1204",
        ibu_kandung: str = "",
        tujuan_permohonan: int = 41,
        foto_selfie_path: str = "",
        foto_challenge_path: str = "",
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
            jenis_kelamin=jenis_kelamin,
            alamat=alamat,
            kode_provinsi=kode_provinsi,
            kode_kota=kode_kota,
            ibu_kandung=ibu_kandung,
            tujuan_permohonan=tujuan_permohonan,
            foto_selfie_path=foto_selfie_path,
            foto_challenge_path=foto_challenge_path,
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
        reg = RegistrationInput(
            nama=nama,
            nik=nik,
            tempat_lahir=tempat_lahir,
            tanggal_lahir=tanggal_lahir,
            kewarganegaraan=kewarganegaraan,
            jenis_identitas=jenis_identitas,
            email=email,
            nomor_hp=nomor_hp,
            jenis_debitur=jenis_debitur,
            jenis_kelamin=jenis_kelamin,
            alamat=alamat,
            kode_provinsi=kode_provinsi,
            kode_kota=kode_kota,
            ibu_kandung=ibu_kandung,
            tujuan_permohonan=tujuan_permohonan,
            foto_ktp_path=ktp_path,
            foto_selfie_path=foto_selfie_path,
            foto_challenge_path=foto_challenge_path,
            jd_id=jd_id,
            kw_id=kw_id,
            ident_id=ident_id,
        )
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

        attempt: dict[str, Any] = {"status": "ERROR"}
        for round_idx in range(max_rounds):
            try:
                attempt = self._one_attempt(reg)
            except (CaptchaSolverError, ScrapingError) as e:
                # Retryable: bad captcha / transient form problem. Log and retry.
                logger.warning(f"attempt_error: round={round_idx + 1} | {e}")
                db.add_log(
                    message=f"Attempt gagal: {str(e)}",
                    level="WARNING",
                    debitur_id=debitur_id,
                )
                time.sleep(1)
                continue
            except SlikError as e:
                # Structural / non-retryable failure — stop, don't burn attempts.
                logger.error(f"attempt_fatal: debitur_id={debitur_id} | {e}")
                db.add_log(
                    message=f"Error fatal: {str(e)}",
                    level="ERROR",
                    detail=traceback.format_exc(),
                    debitur_id=debitur_id,
                )
                notifier.notify_error(nama, str(e))
                db.add_result(debitur_id, "ERROR", False)
                return {
                    "debitur_id": debitur_id,
                    "success": False,
                    "status": "ERROR",
                    "message": str(e),
                    "nomor_pendaftaran": None,
                }
            else:
                if attempt["status"] in ("IN_QUEUE", "NEXT_STEP", "SUBMITTED", "REGISTERED"):
                    break
                if attempt["status"] == "QUOTA_FULL":
                    logger.info(f"attempt_quota_full: round={round_idx + 1}/{max_rounds} | retrying in 1s")
                    time.sleep(1)
                    continue
                logger.warning(f"attempt_inconclusive: round={round_idx + 1} | {attempt['status']}")
                time.sleep(1)
        status = attempt["status"]
        is_success = status in ("IN_QUEUE", "NEXT_STEP", "SUBMITTED", "REGISTERED")
        nomor = attempt.get("nomor_pendaftaran")
        db.add_result(debitur_id, status, is_success, nomor=nomor)

        if is_success:
            if nomor:
                db.update_pendaftaran(debitur_id, nomor)
            logger.info(f"register: nama={nama} → {status} ({nomor})")
            notifier.notify_registration(nama, nomor or "-")
            return {
                "debitur_id": debitur_id,
                "success": True,
                "status": status,
                "nomor_pendaftaran": nomor,
                "message": attempt.get("message", ""),
            }
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
        logger.warning(f"register: nama={nama} → GAGAL ({max_rounds}x)")
        notifier.notify_error(nama, f"Gagal mendaftar setelah {max_rounds}x percobaan")
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

        # ── Phase 1: ensure we have a registration number ──
        if not nomor:
            jd_id, kw_id, ident_id = _form_ids(
                debitur.get("jenis_debitur", "Perseorangan"),
                debitur.get("kewarganegaraan", "WNI"),
                debitur.get("jenis_identitas", "KTP"),
            )
            reg = RegistrationInput(
                nama=nama,
                nik=nik,
                tempat_lahir=debitur.get("tempat_lahir", ""),
                tanggal_lahir=debitur.get("tanggal_lahir", ""),
                kewarganegaraan=debitur.get("kewarganegaraan", "WNI"),
                jenis_identitas=debitur.get("jenis_identitas", "KTP"),
                email=debitur.get("email", ""),
                nomor_hp=debitur.get("nomor_hp", ""),
                jenis_debitur=debitur.get("jenis_debitur", "Perseorangan"),
                jd_id=jd_id,
                kw_id=kw_id,
                ident_id=ident_id,
            )
            for _ in range(max_rounds):
                try:
                    a = self._one_attempt(reg)
                except (CaptchaSolverError, ScrapingError) as e:
                    logger.warning(f"auto_reg_attempt_error: {e}")
                    time.sleep(1)
                    continue
                except SlikError as e:
                    logger.error(f"auto_reg_fatal: debitur_id={debitur_id} | {e}")
                    db.add_result(debitur_id, "ERROR", False, schedule_id=schedule_id)
                    db.add_log(
                        message=f"Auto-reg fatal: {str(e)}", level="ERROR", debitur_id=debitur_id
                    )
                    notifier.notify_error(nama, str(e))
                    return {
                        "debitur_id": debitur_id,
                        "success": False,
                        "status": "ERROR",
                        "nomor_pendaftaran": None,
                        "message": str(e),
                    }
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
                if a["status"] in ("NEXT_STEP", "SUBMITTED", "REGISTERED"):
                    auto_nomor = a.get("nomor_pendaftaran")
                    if auto_nomor:
                        db.update_pendaftaran(debitur_id, auto_nomor)
                        nomor = auto_nomor
                        break
                    # Registered but number not returned yet — retry to obtain it.
                    time.sleep(1)
                    continue
                time.sleep(1)
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

                cap = self._solve_captcha()
                if not cap:
                    logger.warning("status_captcha_failed: retrying")
                    continue
                hidden = scraper.extract_hidden_inputs(soup)
                data = dict(hidden)
                data["txt_no_pendaftaran"] = nomor
                data["CaptchaWsCode"] = cap
                data["ReCaptchaToken"] = "tidakdigunakan"

                sc, rs = scraper.post_form(str(settings.status_url), data)
                if sc != 200:
                    raise ScrapingError(f"status_submit_bad_status: status={sc}")
                result = parser.parse_status(str(rs))
                # A misread captcha comes back as "Captcha tidak valid" — retry
                # with a fresh captcha instead of recording a false failure.
                if "captcha" in str(rs).lower() and result.status in ("ERROR", "UNKNOWN"):
                    db.add_log(
                        message="Status captcha mismatch, retrying",
                        level="WARNING",
                        debitur_id=debitur_id,
                    )
                    time.sleep(1)
                    continue
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
                    "success": result.success,
                    "status": result.status,
                    "nomor_pendaftaran": nomor,
                    "message": result.message,
                }
            except (CaptchaSolverError, ScrapingError) as e:
                logger.warning(f"status_attempt_error: {e}")
                time.sleep(1)
                continue
            except SlikError as e:
                logger.error(f"status_fatal: debitur_id={debitur_id} | {e}")
                db.add_result(debitur_id, "ERROR", False, nomor=nomor, schedule_id=schedule_id)
                db.add_log(
                    message=f"Status fatal: {str(e)}", level="ERROR", debitur_id=debitur_id
                )
                notifier.notify_error(nama, str(e))
                return {
                    "debitur_id": debitur_id,
                    "success": False,
                    "status": "ERROR",
                    "nomor_pendaftaran": nomor,
                    "message": str(e),
                }

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
