import pytest
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup
from slik_checker.scraper import Scraper, scraper, _base64encode


class TestScraper:
    def test_base64encode(self):
        enc = _base64encode("2026-05-07-08-00-00")
        import base64

        assert base64.b64decode(enc).decode() == "2026-05-07-08-00-00"

    def test_extract_hidden_inputs_from_form(self):
        s = Scraper()
        html = """
        <form id="FormPreRegister">
            <input type="hidden" name="TDAFTAR_ID" value="0">
            <input type="hidden" name="__RequestVerificationToken" value="tok123">
            <input type="text" name="CaptchaWsCode">
        </form>
        """
        soup = BeautifulSoup(html, "html.parser")
        hidden = s.extract_hidden_inputs(soup, "FormPreRegister")
        assert hidden["TDAFTAR_ID"] == "0"
        assert hidden["__RequestVerificationToken"] == "tok123"
        assert "CaptchaWsCode" not in hidden

    def test_extract_hidden_from_raw(self):
        s = Scraper()
        html = '<securehidden><input type="hidden" name="token" value="abc"></securehidden>'
        soup = BeautifulSoup(html, "html.parser")
        hidden = s.extract_hidden_inputs(soup, "FormPreRegister")
        assert hidden["token"] == "abc"

    def test_extract_server_timestamp(self):
        s = Scraper()
        html = "new Date('2026-05-07T08:15:30')"
        ts = s.extract_server_timestamp(html)
        assert ts == (2026, 5, 7, 8, 15, 30)

    def test_build_postm(self):
        s = Scraper()
        html = "new Date('2026-05-07T08:00:00')"
        postm = s.build_postm(html)
        import base64

        assert base64.b64decode(postm).decode() == "2026-05-07-08-00-00"

    def test_detect_kuota_false(self):
        s = Scraper()
        assert s.detect_kuota("<html>Selamat datang</html>") is False

    def test_detect_kuota_true(self):
        s = Scraper()
        assert s.detect_kuota("melebihi kuota layanan kami") is True

    @patch("slik_checker.scraper.requests.Session.get")
    def test_prime_session(self, mock_get):
        mock_resp = MagicMock()
        mock_get.return_value = mock_resp
        s = Scraper()
        s.prime_session(1, 1)
        assert mock_get.call_count == 3
