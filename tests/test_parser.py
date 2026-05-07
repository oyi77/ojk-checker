"""Tests for parser module."""

import pytest
from slik_checker.parser import Parser, ParseResult


class TestParser:
    def test_parse_preregister_success(self):
        p = Parser()
        html = "<html><body>Pendaftaran berhasil, silakan lanjutkan<br>Nomor Pendaftaran: REG-001</body></html>"
        result = p.parse_pre_register(html)
        assert result.success is True
        assert result.status == "REGISTERED"
        assert result.nomor_pendaftaran == "REG-001"

    def test_parse_preregister_captcha_error(self):
        p = Parser()
        html = "<html><body><span class='alert-danger'>Captcha tidak valid</span></body></html>"
        result = p.parse_pre_register(html)
        assert result.success is False
        assert result.status == "ERROR"

    def test_parse_preregister_kuota_full(self):
        p = Parser()
        html = "melebihi kuota layanan kami. Sesi I: Pukul 07.00 WIB s.d. kuota terpenuhi"
        result = p.parse_pre_register(html)
        assert result.status == "QUOTA_FULL"
        assert result.success is False

    def test_parse_preregister_next_step(self):
        p = Parser()
        html = "<html><body>Selanjutnya, lengkapi data unggah dokumen</body></html>"
        result = p.parse_pre_register(html)
        assert result.success is True
        assert result.status == "NEXT_STEP"

    def test_parse_status_processing(self):
        p = Parser()
        html = "<html><body>Status: Sedang diproses</body></html>"
        result = p.parse_status(html)
        assert result.status == "PROCESSING"

    def test_parse_status_completed(self):
        p = Parser()
        html = "<html><body>Selesai. Silakan unduh hasil.</body></html>"
        result = p.parse_status(html)
        assert result.status == "COMPLETED"

    def test_parse_status_rejected(self):
        p = Parser()
        html = "<html><body>Status: ditolak</body></html>"
        result = p.parse_status(html)
        assert result.status == "REJECTED"

    def test_sweetalert_extraction(self):
        p = Parser()
        html = "swal({type: 'error', html: 'Captcha tidak valid'})"
        result = p.parse_pre_register(html)
        assert result.status == "ERROR"
        assert "Captcha tidak valid" in result.message

    def test_sweetalert_kuota(self):
        p = Parser()
        html = "swal({type: 'error', html: 'melebihi kuota layanan kami. Sesi I: Pukul 07.00 WIB s.d. kuota terpenuhi Sesi II: Pukul 09.00 WIB s.d. kuota terpenuhi'})"
        result = p.parse_pre_register(html)
        assert result.status == "QUOTA_FULL"
        assert result.extra
        assert len(result.extra.get("sessions", [])) == 2
