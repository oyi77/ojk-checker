"""Tests for the scheduler daemon (slik_checker.scheduler)."""

from __future__ import annotations

from unittest import mock

from slik_checker.scheduler import SchedulerDaemon


def _make_daemon(monkeypatch, db_mock, orch_mock):
    daemon = SchedulerDaemon()
    monkeypatch.setattr("slik_checker.scheduler.db", db_mock)
    monkeypatch.setattr(daemon, "_orchestrator", orch_mock)
    monkeypatch.setattr(daemon, "_scheduler", mock.MagicMock())
    return daemon

def test_execute_schedule_runs_check_status(monkeypatch):
    orch = mock.MagicMock()
    db = mock.MagicMock()
    db.get_schedule.return_value = {
        "id": 1, "name": "daily", "debitur_id": 1, "enabled": True,
        "notify_telegram": True, "notify_email": False, "max_errors": 3,
    }
    daemon = _make_daemon(monkeypatch, db, orch)
    daemon._execute_schedule(1)
    orch.check_status.assert_called_once_with(
        debitur_id=1, schedule_id=1, notify_telegram=True, notify_email=False
    )


def test_execute_schedule_disabled_no_call(monkeypatch):
    orch = mock.MagicMock()
    db = mock.MagicMock()
    db.get_schedule.return_value = {"id": 1, "name": "daily", "enabled": False}
    daemon = _make_daemon(monkeypatch, db, orch)
    daemon._execute_schedule(1)
    orch.check_status.assert_not_called()


def test_execute_schedule_disables_after_max_errors(monkeypatch):
    orch = mock.MagicMock()
    db = mock.MagicMock()
    db.get_schedule.return_value = {
        "id": 1, "name": "daily", "debitur_id": 1, "enabled": True,
        "notify_telegram": False, "notify_email": False, "max_errors": 1,
    }
    db.increment_schedule_errors.return_value = 1
    orch.check_status.side_effect = RuntimeError("boom")
    daemon = _make_daemon(monkeypatch, db, orch)
    daemon._execute_schedule(1)
    db.toggle_schedule.assert_called_once_with(1, False)


def test_load_and_sync_adds_new_and_removes_stale(monkeypatch):
    db = mock.MagicMock()
    db.list_active_schedules.return_value = [
        {"id": 5, "name": "n", "cron_expression": "0 8 * * *"}
    ]
    sched = mock.MagicMock()
    sched.get_jobs.return_value = [mock.MagicMock(id="schedule_99")]
    daemon = SchedulerDaemon()
    monkeypatch.setattr("slik_checker.scheduler.db", db)
    monkeypatch.setattr(daemon, "_scheduler", sched)
    daemon._load_and_sync()
    sched.add_job.assert_called_once()
    sched.remove_job.assert_called_once_with("schedule_99")


def test_load_and_sync_keeps_existing_active(monkeypatch):
    db = mock.MagicMock()
    db.list_active_schedules.return_value = [
        {"id": 5, "name": "n", "cron_expression": "0 8 * * *"}
    ]
    sched = mock.MagicMock()
    sched.get_jobs.return_value = [mock.MagicMock(id="schedule_5")]
    daemon = SchedulerDaemon()
    monkeypatch.setattr("slik_checker.scheduler.db", db)
    monkeypatch.setattr(daemon, "_scheduler", sched)
    daemon._load_and_sync()
    # job already exists for the active schedule -> neither add nor remove
    sched.add_job.assert_not_called()
    sched.remove_job.assert_not_called()


def test_execute_schedule_register_success_disables(monkeypatch):
    orch = mock.MagicMock()
    db = mock.MagicMock()
    db.get_schedule.return_value = {
        "id": 2, "name": "reg", "debitur_id": 7, "enabled": True,
        "action": "register", "max_errors": 3,
    }
    db.get_debitur.return_value = {
        "nama": "Budi", "nik": "123", "tempat_lahir": "", "tanggal_lahir": "01/01/2000",
        "kewarganegaraan": "WNI", "jenis_identitas": "KTP", "email": "", "nomor_hp": "",
        "jenis_debitur": "Perseorangan",
    }
    orch.submit_registration.return_value = {
        "success": True, "status": "REGISTERED", "nomor_pendaftaran": "X1",
    }
    daemon = _make_daemon(monkeypatch, db, orch)
    daemon._execute_schedule(2)
    orch.submit_registration.assert_called_once_with(
        nama="Budi", nik="123", tempat_lahir="", tanggal_lahir="01/01/2000",
        kewarganegaraan="WNI", jenis_identitas="KTP", email="", nomor_hp="",
        jenis_debitur="Perseorangan",
    )
    # Success disables the schedule so it won't re-submit.
    db.toggle_schedule.assert_called_once_with(2, False)


def test_execute_schedule_register_quota_full_keeps_enabled(monkeypatch):
    orch = mock.MagicMock()
    db = mock.MagicMock()
    db.get_schedule.return_value = {
        "id": 2, "name": "reg", "debitur_id": 7, "enabled": True,
        "action": "register", "max_errors": 3,
    }
    db.get_debitur.return_value = {
        "nama": "Budi", "nik": "123", "tempat_lahir": "", "tanggal_lahir": "",
        "kewarganegaraan": "WNI", "jenis_identitas": "KTP", "email": "", "nomor_hp": "",
        "jenis_debitur": "Perseorangan",
    }
    orch.submit_registration.return_value = {"success": False, "status": "QUOTA_FULL"}
    daemon = _make_daemon(monkeypatch, db, orch)
    daemon._execute_schedule(2)
    # Quota-full is transient — leave enabled to retry next window.
    db.toggle_schedule.assert_not_called()
    db.increment_schedule_errors.assert_not_called()


def test_execute_schedule_register_error_counts(monkeypatch):
    orch = mock.MagicMock()
    db = mock.MagicMock()
    db.get_schedule.return_value = {
        "id": 2, "name": "reg", "debitur_id": 7, "enabled": True,
        "action": "register", "max_errors": 1,
    }
    db.get_debitur.return_value = {
        "nama": "Budi", "nik": "123", "tempat_lahir": "", "tanggal_lahir": "",
        "kewarganegaraan": "WNI", "jenis_identitas": "KTP", "email": "", "nomor_hp": "",
        "jenis_debitur": "Perseorangan",
    }
    orch.submit_registration.return_value = {"success": False, "status": "ERROR"}
    db.increment_schedule_errors.return_value = 1
    daemon = _make_daemon(monkeypatch, db, orch)
    daemon._execute_schedule(2)
    db.increment_schedule_errors.assert_called_once_with(2)
    db.toggle_schedule.assert_called_once_with(2, False)


def test_execute_schedule_register_no_debitur_disables(monkeypatch):
    orch = mock.MagicMock()
    db = mock.MagicMock()
    db.get_schedule.return_value = {
        "id": 2, "name": "reg", "debitur_id": 7, "enabled": True,
        "action": "register", "max_errors": 3,
    }
    db.get_debitur.return_value = None
    daemon = _make_daemon(monkeypatch, db, orch)
    daemon._execute_schedule(2)
    orch.submit_registration.assert_not_called()
    db.toggle_schedule.assert_called_once_with(2, False)
