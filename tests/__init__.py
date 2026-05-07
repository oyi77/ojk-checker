"""Test configuration and shared fixtures."""

import pytest
from slik_checker.config import settings


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    settings.ensure_data_dir()
    return settings
