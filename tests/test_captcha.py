"""Tests for captcha solver module."""

import io
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import numpy as np

from slik_checker.captcha import CaptchaSolver, captcha_solver
from slik_checker.exceptions import CaptchaSolverError


class TestCaptchaSolver:
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
        solver._engines = {}
        with pytest.raises(CaptchaSolverError):
            img = Image.new("RGBA", (200, 50))
            solver.solve(img)
