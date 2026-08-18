"""Tests for captcha solver module."""

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from slik_checker.captcha import CaptchaSolver
from slik_checker.config import settings
from slik_checker.exceptions import CaptchaSolverError


class TestCaptchaSolver:
    @pytest.mark.parametrize(
        "cfg",
        [
            {"vision_captcha_enabled": True, "vision_captcha_api_key": "fake-key"},
            {"external_captcha_api_key": "fake-key"},
            {"captcha_mode": "manual"},
            {"captcha_override": "ABCD"},
        ],
    )
    def test_available_non_local_solvers(self, patch_settings, monkeypatch, cfg):
        for k, v in cfg.items():
            monkeypatch.setattr(settings, k, v)
        solver = CaptchaSolver()
        # Force the local-engine discovery to report "no local OCR".
        solver._engines = {}
        solver._engines_ready = True
        assert solver.available is True

    def test_unavailable_with_no_solver(self, patch_settings, monkeypatch):
        monkeypatch.setattr(settings, "vision_captcha_enabled", False)
        monkeypatch.setattr(settings, "external_captcha_api_key", None)
        monkeypatch.setattr(settings, "captcha_mode", "auto")
        monkeypatch.setattr(settings, "captcha_override", None)
        solver = CaptchaSolver()
        solver._engines = {}
        solver._engines_ready = True
        assert solver.available is False

    def test_available_with_engines(self, patch_settings):
        solver = CaptchaSolver()
        assert solver.available is True
        assert solver.engine_count >= 1

    def test_solve_blank_image(self, patch_settings):
        solver = CaptchaSolver()
        img = Image.new("RGB", (200, 80), "white")
        result = solver.solve(img)
        assert result is None

    def test_solve_from_bytes(self, patch_settings):
        solver = CaptchaSolver()
        buf = io.BytesIO()
        img = Image.new("RGBA", (200, 50), (255, 255, 255, 255))
        img.save(buf, format="PNG")
        result = solver.solve_from_bytes(buf.getvalue())
        assert result is None

    def test_validate_length(self, patch_settings):
        solver = CaptchaSolver()
        assert solver._validate("abc") is None  # too short
        assert solver._validate("abcd") == "abcd"
        assert solver._validate("abcdef") == "abcdef"
        assert solver._validate("abcdefg") == "abcdef"  # trimmed

    @patch("slik_checker.captcha.Image.open")
    def test_solve_from_path(self, mock_open, patch_settings):
        mock_img = MagicMock(spec=Image.Image)
        mock_open.return_value = mock_img
        solver = CaptchaSolver()
        solver._engines = {"mock": lambda x: "WXYZ"}
        result = solver.solve_from_path("fake.png")
        assert result == "WXYZ"

    def test_no_engines_raises_error(self, patch_settings):
        solver = CaptchaSolver()
        # Simulate: engine discovery already ran and found nothing.
        solver._engines = {}
        solver._engines_ready = True
        with pytest.raises(CaptchaSolverError):
            img = Image.new("RGBA", (200, 50))
            solver.solve(img)

    @patch("slik_checker.captcha.requests.post")
    def test_solve_vision_disabled_returns_none(self, mock_post, patch_settings, monkeypatch):
        monkeypatch.setattr(settings, "vision_captcha_enabled", False)
        solver = CaptchaSolver()
        img = Image.new("RGB", (200, 80), "white")
        assert solver.solve_vision(img) is None

    @patch("slik_checker.captcha.requests.post")
    def test_solve_vision_success(self, mock_post, patch_settings, monkeypatch):
        monkeypatch.setattr(settings, "vision_captcha_enabled", True)
        monkeypatch.setattr(settings, "vision_captcha_api_base", "https://api.example.com/v1")
        monkeypatch.setattr(settings, "vision_captcha_model", "test-model")
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"choices": [{"message": {"content": "Xb Dp Kw"}}]}
        mock_post.return_value = fake_resp
        solver = CaptchaSolver()
        img = Image.new("RGB", (200, 80), "white")
        assert solver.solve_vision(img) == "XbDpKw"

    @patch("slik_checker.captcha.requests.post")
    def test_solve_vision_bad_length(self, mock_post, patch_settings, monkeypatch):
        monkeypatch.setattr(settings, "vision_captcha_enabled", True)
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"choices": [{"message": {"content": "ab"}}]}
        mock_post.return_value = fake_resp
        solver = CaptchaSolver()
        img = Image.new("RGB", (200, 80), "white")
        assert solver.solve_vision(img) is None

    @patch("slik_checker.captcha.requests.post")
    def test_solve_vision_request_error(self, mock_post, patch_settings, monkeypatch):
        monkeypatch.setattr(settings, "vision_captcha_enabled", True)
        mock_post.side_effect = RuntimeError("boom")
        solver = CaptchaSolver()
        img = Image.new("RGB", (200, 80), "white")
        assert solver.solve_vision(img) is None
