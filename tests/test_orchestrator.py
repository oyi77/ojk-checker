"""Tests for the orchestrator engine — registration + status flows.

Scraper, captcha solver, parser, notifier and db are mocked so the suite runs
without network, OCR models, or a live iDebKu session.
"""

import base64
import io
from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from slik_checker.config import settings
from slik_checker.orchestrator import Orchestrator, RegistrationInput
from slik_checker.parser import ParseResult

PRE_REGISTER_HTML = """
<form id="FormPreRegister">
  <script>var serverDateTime = new Date('2026-08-18T18:30:35');</script>
  <input type="hidden" name="__VIEWSTATE" value="vs"/>
  <input type="hidden" name="__EVENTVALIDATION" value="ev"/>
  <input name="JDEBITUR_ID"/>
  <input name="SDEBITUR_ID"/>
  <input name="IDENTITAS_ID"/>
  <input name="TDAFTAR_IDENTITAS_NO"/>
  <input name="NamaLengkap"/>
  <input name="NIK"/>
  <input name="TempatLahir"/>
  <input name="TanggalLahir"/>
  <input name="Email"/>
  <input name="NoHP"/>
  <input name="CaptchaWsCode"/>
</form></body></html>
"""
STATUS_HTML = """
<html><body><form id="FormCekStatus">
  <input type="hidden" name="__VIEWSTATE" value="vs"/>
  <input type="hidden" name="__RequestVerificationToken" value="tok"/>
  <input name="txt_no_pendaftaran"/>
  <input name="CaptchaWsCode"/>
</form></body></html>
"""
KUOTA_HTML = "<html>melebihi kuota layanan kami</html>"

# Step-2 wizard page rendered server-side after a step-1 submit.
STEP2_HTML = """
<form action="/Public/PendaftaranOnline/PreRegister">
  <script>var serverDateTime = new Date('2026-08-18T18:30:35');</script>
  <input type="hidden" name="__RequestVerificationToken" value="t2"/>
  <input name="NamaLengkap"/>
  <input name="TempatLahir"/>
  <input name="CaptchaWsCode"/>
</form>
"""

def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return buf.getvalue()




def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


@pytest.fixture
def mocks(monkeypatch):
    import slik_checker.orchestrator as orch

    scraper = MagicMock()
    captcha = MagicMock()
    parser = MagicMock()
    notifier = MagicMock()
    db = MagicMock()

    monkeypatch.setattr(orch, "scraper", scraper)
    monkeypatch.setattr(orch, "captcha_solver", captcha)
    monkeypatch.setattr(orch, "parser", parser)
    monkeypatch.setattr(orch, "notifier", notifier)
    monkeypatch.setattr(orch, "db", db)
    monkeypatch.setattr(orch.time, "sleep", lambda *a, **k: None)

    scraper.extract_hidden_inputs.return_value = {}
    scraper.detect_kuota.return_value = False
    scraper.fetch_captcha.return_value = _png()
    scraper.post_form.return_value = (200, _soup(PRE_REGISTER_HTML))
    # Default: every page fetch returns the pre-register form.
    scraper.fetch_page.side_effect = lambda url: ("<html></html>", _soup(PRE_REGISTER_HTML))
    captcha.available = True
    captcha.solve_from_bytes.return_value = "ABCD"
    db.upsert_debitur.return_value = 1
    db.get_latest_result_status.return_value = "UNKNOWN"

    return scraper, captcha, parser, notifier, db

# ── _build_registration_payload ────────────────────────────────────────────


def test_build_payload_fills_exact_and_fragment_fields():
    orch = Orchestrator()
    reg = RegistrationInput(
        nama="Budi Santoso",
        nik="1234567890123456",
        tempat_lahir="Jakarta",
        tanggal_lahir="01/01/1990",
        email="budi@example.com",
        nomor_hp="08123456789",
        jd_id=2,
        kw_id=1,
        ident_id=22,
    )
    data = orch._build_registration_payload(PRE_REGISTER_HTML, _soup(PRE_REGISTER_HTML), reg, "ABCD")
    # Anti-bot base64 server-timestamp (postm) must be populated from the page
    # and match the portal's cmdEncrypt() format: YYYY-M-D-HH-MM-SS (month/day
    # UNpadded, h/m/s padded).
    decoded = base64.b64decode(data["postm"]).decode()
    assert "2026-8-18" in decoded and "18-30-35" in decoded
    # Exact control names
    assert data["JDEBITUR_ID"] == "2"
    assert data["SDEBITUR_ID"] == "1"
    assert data["IDENTITAS_ID"] == "22"
    assert data["TDAFTAR_IDENTITAS_NO"] == reg.nik
    assert data["CaptchaWsCode"] == "ABCD"
    # Fragment-matched free-text fields
    assert data["NamaLengkap"] == "Budi Santoso"
    assert data["NIK"] == reg.nik
    assert data["TempatLahir"] == "Jakarta"
    assert data["TanggalLahir"] == "01/01/1990"
    assert data["Email"] == "budi@example.com"
    assert data["NoHP"] == "08123456789"
    # Hidden inputs preserved
    assert data["__VIEWSTATE"] == "vs"


# ── submit_registration: success ──────────────────────────────────────────


def test_submit_registration_success(mocks):
    scraper, captcha, parser, notifier, db = mocks
    parser.parse_pre_register.return_value = ParseResult(
        success=True, status="REGISTERED", nomor_pendaftaran="REG-1"
    )

    orch = Orchestrator()
    result = orch.submit_registration(nama="Budi", nik="1234567890123456")

    assert result["success"] is True
    assert result["status"] == "REGISTERED"
    assert result["nomor_pendaftaran"] == "REG-1"
    # Number persisted + user notified
    db.update_pendaftaran.assert_called_once_with(1, "REG-1")
    notifier.notify_registration.assert_called_once()
    db.add_result.assert_called_with(1, "REGISTERED", True, nomor="REG-1")
    captcha.solve_from_bytes.assert_called()


def test_submit_registration_no_captcha_engines(mocks):
    scraper, captcha, parser, notifier, db = mocks
    captcha.available = False

    orch = Orchestrator()
    result = orch.submit_registration(nama="Budi", nik="1234567890123456")

    assert result["success"] is False
    assert result["status"] == "ERROR"
    notifier.notify_registration.assert_not_called()


def test_submit_registration_quota_full(mocks):
    scraper, captcha, parser, notifier, db = mocks
    scraper.detect_kuota.return_value = True
    scraper.fetch_page.side_effect = lambda url: (KUOTA_HTML, _soup(PRE_REGISTER_HTML))
    parser.parse_pre_register.return_value = ParseResult(
        success=False, status="QUOTA_FULL", message="Kuota penuh"
    )

    orch = Orchestrator()
    result = orch.submit_registration(nama="Budi", nik="1234567890123456")

    assert result["status"] == "QUOTA_FULL"
    assert result["success"] is False
    notifier.notify_registration.assert_not_called()


def test_submit_registration_captcha_unsolvable_retries_then_errors(mocks):
    scraper, captcha, parser, notifier, db = mocks
    captcha.solve_from_bytes.return_value = None
    captcha.solve_external.return_value = None

    orch = Orchestrator()
    result = orch.submit_registration(nama="Budi", nik="1234567890123456")

    # All rounds exhausted, no success, no registration notification.
    assert result["success"] is False
    assert result["status"] == "ERROR"
    # All rounds exhausted → final failure is reported to the operator.
    assert result["status"] == "ERROR"
    assert result["success"] is False
    assert captcha.solve_from_bytes.call_count == settings.captcha_max_attempts
    notifier.notify_registration.assert_not_called()
    notifier.notify_error.assert_called_once()

def test_submit_registration_missing_form_fields_is_fatal(mocks):
    scraper, captcha, parser, notifier, db = mocks
    # Soup without the required control names → base SlikError (fatal).
    empty_soup = _soup("<form><input type='hidden' name='x' value='y'/></form>")
    scraper.fetch_page.side_effect = lambda url: ("<html></html>", empty_soup)
    captcha.solve_from_bytes.return_value = "ABCD"

    orch = Orchestrator()
    result = orch.submit_registration(nama="Budi", nik="1234567890123456")

    assert result["success"] is False
    assert result["status"] == "ERROR"
    # Fatal structural failure must be surfaced to the operator.
    notifier.notify_error.assert_called_once()


def test_submit_registration_bad_status_is_fatal(mocks):
    scraper, captcha, parser, notifier, db = mocks
    scraper.post_form.return_value = (500, _soup(PRE_REGISTER_HTML))

    orch = Orchestrator()
    result = orch.submit_registration(nama="Budi", nik="1234567890123456")

    assert result["status"] == "ERROR"
    notifier.notify_error.assert_called_once()


def test_submit_registration_follows_multi_step_wizard(mocks):
    scraper, captcha, parser, notifier, db = mocks
    scraper.fetch_page.side_effect = lambda url: ("<html></html>", _soup(PRE_REGISTER_HTML))
    # Step 1 submit → server renders step 2; step 2 submit → REGISTERED.
    scraper.post_form.side_effect = [
        (200, _soup(STEP2_HTML)),
        (200, _soup(PRE_REGISTER_HTML)),
    ]
    parser.parse_pre_register.side_effect = [
        ParseResult(success=True, status="NEXT_STEP"),
        ParseResult(success=True, status="REGISTERED", nomor_pendaftaran="REG-2"),
    ]

    orch = Orchestrator()
    result = orch.submit_registration(
        nama="Budi Santoso",
        nik="1234567890123456",
        tempat_lahir="Jakarta",
        tanggal_lahir="01/01/1990",
        email="budi@example.com",
        nomor_hp="08123456789",
    )

    assert result["success"] is True
    assert result["status"] == "REGISTERED"
    assert result["nomor_pendaftaran"] == "REG-2"
    # Both wizard steps were submitted.
    assert scraper.post_form.call_count == 2
    db.update_pendaftaran.assert_called_once_with(1, "REG-2")
    notifier.notify_registration.assert_called_once()


# ── check_status ──────────────────────────────────────────────────────────


def test_check_status_auto_registers_then_reports(mocks):
    scraper, captcha, parser, notifier, db = mocks
    db.get_debitur.return_value = {
        "nama": "Budi",
        "nik": "1234567890123456",
        "nomor_pendaftaran": "",
        "jenis_debitur": "Perseorangan",
        "kewarganegaraan": "WNI",
        "jenis_identitas": "KTP",
        "tempat_lahir": "",
        "tanggal_lahir": "",
        "email": "",
        "nomor_hp": "",
    }
    scraper.fetch_page.side_effect = lambda url: (
        ("<html></html>", _soup(PRE_REGISTER_HTML))
        if "PreRegister" in url
        else ("<html></html>", _soup(STATUS_HTML))
    )
    scraper.post_form.return_value = (200, _soup(STATUS_HTML))
    # Phase 1: auto-register succeeds with a number.
    parser.parse_pre_register.return_value = ParseResult(
        success=True, status="REGISTERED", nomor_pendaftaran="REG-9"
    )
    # Phase 2: status lookup.
    parser.parse_status.return_value = ParseResult(
        success=True, status="COMPLETED", nomor_pendaftaran="REG-9"
    )

    orch = Orchestrator()
    result = orch.check_status(debitur_id=1)

    assert result["success"] is True
    assert result["status"] == "COMPLETED"
    assert result["nomor_pendaftaran"] == "REG-9"
    db.update_pendaftaran.assert_called_once_with(1, "REG-9")
    notifier.notify_status_change.assert_called_once()

    # Status POST must carry the RE-confirmed field names.
    status_post = scraper.post_form.call_args_list[-1]
    status_data = status_post.args[1]
    assert status_data["txt_no_pendaftaran"] == "REG-9"
    assert status_data.get("CaptchaWsCode")


def test_check_status_known_number_reports(mocks):
    scraper, captcha, parser, notifier, db = mocks
    db.get_debitur.return_value = {
        "nama": "Budi",
        "nik": "1234567890123456",
        "nomor_pendaftaran": "REG-9",
    }
    scraper.fetch_page.side_effect = lambda url: ("<html></html>", _soup(STATUS_HTML))
    scraper.post_form.return_value = (200, _soup(STATUS_HTML))
    parser.parse_status.return_value = ParseResult(
        success=True, status="PROCESSING", nomor_pendaftaran="REG-9"
    )

    orch = Orchestrator()
    result = orch.check_status(debitur_id=1, nomor_pendaftaran="REG-9")

    assert result["status"] == "PROCESSING"
    # No auto-registration attempt should have happened.
    parser.parse_pre_register.assert_not_called()

    # Status POST must carry the RE-confirmed field names.
    status_post = scraper.post_form.call_args
    status_data = status_post.args[1]
    assert status_data["txt_no_pendaftaran"] == "REG-9"
    assert status_data.get("CaptchaWsCode")


def test_check_status_retries_on_captcha_mismatch(mocks):
    scraper, captcha, parser, notifier, db = mocks
    db.get_debitur.return_value = {
        "nama": "Budi",
        "nik": "1234567890123456",
        "nomor_pendaftaran": "REG-9",
    }
    captcha_err = "<html><body>Captcha tidak valid</body></html>"
    scraper.fetch_page.side_effect = lambda url: ("<html></html>", _soup(STATUS_HTML))
    scraper.post_form.side_effect = [
        (200, _soup(captcha_err)),
        (200, _soup(STATUS_HTML)),
    ]
    parser.parse_status.side_effect = [
        ParseResult(success=False, status="ERROR", message="Captcha tidak valid"),
        ParseResult(success=True, status="PROCESSING", nomor_pendaftaran="REG-9"),
    ]
    orch = Orchestrator()
    result = orch.check_status(debitur_id=1, nomor_pendaftaran="REG-9")
    # Recovered on the second attempt with a fresh captcha.
    assert result["status"] == "PROCESSING"
    assert scraper.post_form.call_count == 2


def test_solve_captcha_prefers_vision_when_enabled(mocks, monkeypatch):
    import slik_checker.orchestrator as orch

    monkeypatch.setattr(orch.settings, "vision_captcha_enabled", True)
    scraper, captcha, parser, notifier, db = mocks
    captcha.solve_vision.return_value = "VISN"
    captcha.solve_external.return_value = "EXTN"
    captcha.solve_from_bytes.return_value = "LOCL"
    result = orch.Orchestrator()._solve_captcha()
    assert result == "VISN"
    captcha.solve_vision.assert_called_once()
    captcha.solve_external.assert_not_called()
    captcha.solve_from_bytes.assert_not_called()


def test_solve_captcha_manual_reads_answer_file(mocks, monkeypatch, tmp_path):
    import sys
    from pathlib import Path as RealPath

    import slik_checker.orchestrator as orch

    monkeypatch.setattr(orch.settings, "captcha_mode", "manual")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "captcha_answer.txt").write_text("WXYZ", encoding="utf-8")

    real_path = RealPath

    def fake_path(*args, **kwargs):
        if args and args[0] == "data":
            return data_dir
        return real_path(*args, **kwargs)

    monkeypatch.setattr(orch, "Path", fake_path)

    scraper, captcha, parser, notifier, db = mocks
    result = orch.Orchestrator()._solve_captcha()
    assert result == "WXYZ"
    captcha.solve_vision.assert_not_called()
    captcha.solve_external.assert_not_called()
    captcha.solve_from_bytes.assert_not_called()


def test_solve_captcha_override_returns_text_without_fetch(mocks, monkeypatch):
    import slik_checker.orchestrator as orch

    monkeypatch.setattr(orch.settings, "captcha_override", "ABCD")
    scraper, captcha, parser, notifier, db = mocks
    result = orch.Orchestrator()._solve_captcha()
    assert result == "ABCD"
    scraper.fetch_captcha.assert_not_called()
    captcha.solve_vision.assert_not_called()
    captcha.solve_external.assert_not_called()
    captcha.solve_from_bytes.assert_not_called()


def test_solve_captcha_override_implausible_raises(mocks, monkeypatch):
    import slik_checker.orchestrator as orch
    from slik_checker.exceptions import CaptchaSolverError

    monkeypatch.setattr(orch.settings, "captcha_override", "ab")  # too short
    scraper, captcha, parser, notifier, db = mocks
    with pytest.raises(CaptchaSolverError):
        orch.Orchestrator()._solve_captcha()
