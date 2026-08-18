"""Tests for the CLI entrypoint (slik_checker.cli)."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest import mock

from slik_checker import cli


def test_cli_register_calls_orchestrator(monkeypatch):
    orch = mock.MagicMock()
    orch.submit_registration.return_value = {
        "status": "REGISTERED",
        "success": True,
        "nomor_pendaftaran": "REG-1",
        "message": "ok",
    }
    db = mock.MagicMock()
    monkeypatch.setattr(cli, "db", db)
    # Patch the source module attribute: cli re-imports it lazily inside main().
    monkeypatch.setattr("slik_checker.orchestrator.orchestrator", orch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "slik-checker", "register", "--nama", "Budi",
            "--nik", "1234567890123456", "--tempat-lahir", "Jakarta",
            "--tanggal-lahir", "01/01/2000", "--email", "a@b.com", "--nomor-hp", "0812",
        ],
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main()
    orch.submit_registration.assert_called_once()
    kw = orch.submit_registration.call_args.kwargs
    assert kw["nama"] == "Budi" and kw["nik"] == "1234567890123456"
    assert "REG-1" in buf.getvalue()


def test_cli_check_calls_orchestrator(monkeypatch):
    orch = mock.MagicMock()
    orch.check_status.return_value = {"status": "PROCESSING", "message": "ok"}
    db = mock.MagicMock()
    monkeypatch.setattr(cli, "db", db)
    monkeypatch.setattr("slik_checker.orchestrator.orchestrator", orch)
    monkeypatch.setattr(
        "sys.argv", ["slik-checker", "check", "--debitur-id", "1", "--nomor", "REG-9"]
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main()
    orch.check_status.assert_called_once_with(debitur_id=1, nomor_pendaftaran="REG-9")


def test_cli_init(monkeypatch):
    db = mock.MagicMock()
    monkeypatch.setattr(cli, "db", db)
    monkeypatch.setattr("sys.argv", ["slik-checker", "init"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main()
    db.initialize.assert_called_once()
    assert "Database initialized" in buf.getvalue()


def test_cli_list(monkeypatch):
    db = mock.MagicMock()
    db.list_debiturs.return_value = [
        {"id": 1, "nama": "Budi", "nik": "123", "nomor_pendaftaran": "REG-9"}
    ]
    monkeypatch.setattr(cli, "db", db)
    monkeypatch.setattr("sys.argv", ["slik-checker", "list"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main()
    assert "Budi" in buf.getvalue()
