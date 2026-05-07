"""Tests for database models."""

import pytest
from slik_checker.models import Database, db
from slik_checker.exceptions import DatabaseError


class TestDatabase:
    def test_initialize_creates_tables(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()

        with test_db.connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        names = [t["name"] for t in tables]
        assert "debiturs" in names
        assert "schedules" in names
        assert "results" in names
        assert "logs" in names

    def test_upsert_debitur_new(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        did = test_db.upsert_debitur(nama="Test", nik="1234567890123456")
        assert did >= 1

        d = test_db.get_debitur(did)
        assert d is not None
        assert d["nama"] == "Test"
        assert d["nik"] == "1234567890123456"

    def test_upsert_debitur_update(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        did1 = test_db.upsert_debitur(nama="Old", nik="1111111111111111")
        did2 = test_db.upsert_debitur(nama="New", nik="1111111111111111")
        assert did1 == did2

        d = test_db.get_debitur(did2)
        assert d["nama"] == "New"

    def test_list_debiturs(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        test_db.upsert_debitur(nama="A", nik="1111111111111111")
        test_db.upsert_debitur(nama="B", nik="2222222222222222")
        assert len(test_db.list_debiturs()) == 2

    def test_delete_cascades(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        did = test_db.upsert_debitur(nama="X", nik="9999999999999999")
        test_db.add_schedule(did, "S1", "0 * * * *")
        test_db.add_result(did, "OK", True)
        test_db.add_log("test", debitur_id=did)

        test_db.delete_debitur(did)
        assert test_db.get_debitur(did) is None
        assert len(test_db.list_schedules()) == 0
        assert len(test_db.list_results()) == 0
        assert len(test_db.list_logs()) == 0

    def test_schedule_crud(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        did = test_db.upsert_debitur(nama="S", nik="3333333333333333")

        sid = test_db.add_schedule(did, "Daily", "0 8 * * *", telegram=True, email=False)
        assert sid >= 1

        sched = test_db.get_schedule(sid)
        assert sched["name"] == "Daily"
        assert sched["cron_expression"] == "0 8 * * *"
        assert sched["enabled"] == 1

        test_db.toggle_schedule(sid, False)
        assert test_db.get_schedule(sid)["enabled"] == 0

        test_db.toggle_schedule(sid, True)
        assert test_db.get_schedule(sid)["enabled"] == 1

        test_db.delete_schedule(sid)
        assert test_db.get_schedule(sid) is None

    def test_active_schedules(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        did = test_db.upsert_debitur(nama="A", nik="1111111111111111")
        sid = test_db.add_schedule(did, "Active", "0 8 * * *")
        test_db.add_schedule(did, "Disabled", "0 9 * * *")
        test_db.toggle_schedule(sid + 1, False)

        active = test_db.list_active_schedules()
        assert len(active) == 1
        assert active[0]["name"] == "Active"

    def test_result_tracking(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        did = test_db.upsert_debitur(nama="R", nik="4444444444444444")

        test_db.add_result(did, "PROCESSING", True, nomor="ABC-123")
        test_db.add_result(did, "COMPLETED", True, nomor="ABC-123")

        results = test_db.list_results(debitur_id=did)
        assert len(results) == 2

        assert test_db.get_latest_result_status(did, "ABC-123") == "COMPLETED"

    def test_stats(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        test_db.upsert_debitur(nama="A", nik="1111111111111111")
        test_db.upsert_debitur(nama="B", nik="2222222222222222")

        stats = test_db.get_stats()
        assert stats["total_debiturs"] == 2
        assert stats["total_results"] == 0

    def test_update_pendaftaran(self, patch_settings):
        test_db = Database(patch_settings.db_path)
        test_db.initialize()
        did = test_db.upsert_debitur(nama="P", nik="5555555555555555")
        test_db.update_pendaftaran(did, "REG-999")
        assert test_db.get_debitur(did)["nomor_pendaftaran"] == "REG-999"
