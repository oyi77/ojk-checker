"""Database models and repository layer."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from slik_checker.config import settings
from slik_checker.exceptions import DatabaseError
from slik_checker.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS debiturs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT NOT NULL,
    nik TEXT UNIQUE NOT NULL,
    tempat_lahir TEXT DEFAULT '',
    tanggal_lahir TEXT DEFAULT '',
    kewarganegaraan TEXT DEFAULT 'WNI',
    jenis_identitas TEXT DEFAULT 'KTP',
    email TEXT DEFAULT '',
    nomor_hp TEXT DEFAULT '',
    jenis_debitur TEXT DEFAULT 'Perseorangan',
    ktp_path TEXT DEFAULT '',
    nomor_pendaftaran TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debitur_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    next_run TEXT,
    notify_telegram INTEGER NOT NULL DEFAULT 1,
    notify_email INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    max_errors INTEGER NOT NULL DEFAULT 10,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (debitur_id) REFERENCES debiturs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debitur_id INTEGER NOT NULL,
    schedule_id INTEGER,
    nomor_pendaftaran TEXT,
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    success INTEGER NOT NULL DEFAULT 0,
    raw_response TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (debitur_id) REFERENCES debiturs(id) ON DELETE CASCADE,
    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    debitur_id INTEGER,
    schedule_id INTEGER,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_debiturs_nik ON debiturs(nik);
CREATE INDEX IF NOT EXISTS idx_schedules_debitur ON schedules(debitur_id);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);
CREATE INDEX IF NOT EXISTS idx_results_debitur ON results(debitur_id);
CREATE INDEX IF NOT EXISTS idx_results_created ON results(created_at);
CREATE INDEX IF NOT EXISTS idx_logs_debitur ON logs(debitur_id);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);
"""


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or settings.db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            if conn:
                conn.rollback()
            logger.error(f"database_error: {e}")
            raise DatabaseError(str(e)) from e
        finally:
            if conn:
                conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
            logger.info(f"database_initialized: {self._db_path}")

    def upsert_debitur(self, **fields: Any) -> int:
        nik = fields.get("nik", "")
        with self.connection() as conn:
            existing = conn.execute("SELECT id FROM debiturs WHERE nik = ?", (nik,)).fetchone()

            now = datetime.now(timezone.utc).isoformat()
            field_names = [
                "nama",
                "tempat_lahir",
                "tanggal_lahir",
                "kewarganegaraan",
                "jenis_identitas",
                "email",
                "nomor_hp",
                "jenis_debitur",
                "ktp_path",
            ]
            values = {k: fields.get(k, "") for k in field_names}

            if existing:
                set_clause = ", ".join(f"{k} = ?" for k in field_names)
                conn.execute(
                    f"UPDATE debiturs SET {set_clause}, updated_at = ? WHERE nik = ?",
                    (*values.values(), now, nik),
                )
                return existing["id"]

            placeholders = ", ".join("?" * len(field_names))
            cursor = conn.execute(
                f"INSERT INTO debiturs ({', '.join(field_names)}, nik, updated_at) "
                f"VALUES ({placeholders}, ?, ?)",
                (*values.values(), nik, now),
            )
            return cursor.lastrowid

    def update_pendaftaran(self, debitur_id: int, nomor: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE debiturs SET nomor_pendaftaran = ?, updated_at = datetime('now') WHERE id = ?",
                (nomor, debitur_id),
            )

    def get_debitur(self, debitur_id: int) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM debiturs WHERE id = ?", (debitur_id,)).fetchone()
            return dict(row) if row else None

    def get_debitur_by_nik(self, nik: str) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM debiturs WHERE nik = ?", (nik,)).fetchone()
            return dict(row) if row else None

    def list_debiturs(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM debiturs ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_debitur(self, debitur_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM results WHERE debitur_id = ?", (debitur_id,))
            conn.execute("DELETE FROM schedules WHERE debitur_id = ?", (debitur_id,))
            conn.execute("DELETE FROM logs WHERE debitur_id = ?", (debitur_id,))
            conn.execute("DELETE FROM debiturs WHERE id = ?", (debitur_id,))

    def add_schedule(
        self, debitur_id: int, name: str, cron: str, telegram: bool = True, email: bool = False
    ) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO schedules (debitur_id, name, cron_expression, notify_telegram, notify_email) "
                "VALUES (?, ?, ?, ?, ?)",
                (debitur_id, name, cron, int(telegram), int(email)),
            )
            return cursor.lastrowid

    def get_schedule(self, schedule_id: int) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            return dict(row) if row else None

    def list_schedules(self, debitur_id: Optional[int] = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if debitur_id:
                rows = conn.execute(
                    """SELECT s.*, d.nama, d.nik FROM schedules s
                       JOIN debiturs d ON s.debitur_id = d.id
                       WHERE s.debitur_id = ? ORDER BY s.created_at DESC""",
                    (debitur_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT s.*, d.nama, d.nik FROM schedules s
                       JOIN debiturs d ON s.debitur_id = d.id
                       ORDER BY s.created_at DESC"""
                ).fetchall()
            return [dict(r) for r in rows]

    def list_active_schedules(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT s.*, d.nama, d.nik, d.nomor_pendaftaran FROM schedules s
                   JOIN debiturs d ON s.debitur_id = d.id
                   WHERE s.enabled = 1 AND s.error_count < s.max_errors
                   ORDER BY s.created_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def update_schedule_last_run(self, schedule_id: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE schedules SET last_run = datetime('now') WHERE id = ?",
                (schedule_id,),
            )

    def increment_schedule_errors(self, schedule_id: int) -> int:
        with self.connection() as conn:
            conn.execute(
                "UPDATE schedules SET error_count = error_count + 1 WHERE id = ?",
                (schedule_id,),
            )
            row = conn.execute(
                "SELECT error_count, max_errors FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()
            return row["error_count"] if row else 0

    def toggle_schedule(self, schedule_id: int, enabled: bool) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE schedules SET enabled = ? WHERE id = ?",
                (int(enabled), schedule_id),
            )

    def delete_schedule(self, schedule_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))

    def add_result(
        self,
        debitur_id: int,
        status: str,
        success: bool,
        nomor: Optional[str] = None,
        schedule_id: Optional[int] = None,
        raw: Optional[str] = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO results (debitur_id, schedule_id, nomor_pendaftaran, status, success, raw_response)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (debitur_id, schedule_id, nomor, status, int(success), (raw or "")[:10000]),
            )

    def list_results(
        self, debitur_id: Optional[int] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if debitur_id:
                rows = conn.execute(
                    """SELECT r.*, d.nama, d.nik FROM results r
                       JOIN debiturs d ON r.debitur_id = d.id
                       WHERE r.debitur_id = ? ORDER BY r.created_at DESC LIMIT ?""",
                    (debitur_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT r.*, d.nama, d.nik FROM results r
                       JOIN debiturs d ON r.debitur_id = d.id
                       ORDER BY r.created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_result_status(self, debitur_id: int, nomor: str) -> str:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT status FROM results
                   WHERE debitur_id = ? AND nomor_pendaftaran = ?
                    ORDER BY created_at DESC, id DESC LIMIT 1""",
                (debitur_id, nomor),
            ).fetchone()
            return row["status"] if row else "UNKNOWN"

    def add_log(
        self,
        message: str,
        level: str = "INFO",
        detail: str = "",
        debitur_id: Optional[int] = None,
        schedule_id: Optional[int] = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO logs (debitur_id, schedule_id, level, message, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (debitur_id, schedule_id, level, message, detail),
            )

    def list_logs(self, debitur_id: Optional[int] = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if debitur_id:
                rows = conn.execute(
                    "SELECT * FROM logs WHERE debitur_id = ? ORDER BY created_at DESC LIMIT ?",
                    (debitur_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, int]:
        with self.connection() as conn:
            total_debiturs = conn.execute("SELECT COUNT(*) as c FROM debiturs").fetchone()["c"]
            active_schedules = conn.execute(
                "SELECT COUNT(*) as c FROM schedules WHERE enabled = 1"
            ).fetchone()["c"]
            total_results = conn.execute("SELECT COUNT(*) as c FROM results").fetchone()["c"]
            return {
                "total_debiturs": total_debiturs,
                "active_schedules": active_schedules,
                "total_results": total_results,
            }


db = Database()
