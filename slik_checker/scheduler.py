"""Persistent scheduler daemon with error recovery and graceful shutdown."""

from __future__ import annotations

import signal
import sys
import time
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from slik_checker.config import settings
from slik_checker.logging_config import get_logger
from slik_checker.models import db
from slik_checker.orchestrator import orchestrator

logger = get_logger(__name__)


class SchedulerDaemon:
    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(
            timezone=settings.scheduler_timezone,
        )
        self._running = False
        self._orchestrator = orchestrator

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return

        db.initialize()

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        self._scheduler.add_job(
            self._load_and_sync,
            trigger="interval",
            minutes=1,
            id="_sync_job",
            name="Schedule sync",
            replace_existing=True,
        )

        self._scheduler.start()
        self._running = True
        logger.info("scheduler_started")

        self._load_and_sync()

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._scheduler.running:
            self._scheduler.shutdown(wait=True)
        logger.info("scheduler_stopped")

    def _load_and_sync(self) -> None:
        try:
            active = db.list_active_schedules()
            current_jobs = {j.id for j in self._scheduler.get_jobs() if not j.id.startswith("_")}

            for sched in active:
                job_id = f"schedule_{sched['id']}"
                if job_id not in current_jobs:
                    try:
                        self._scheduler.add_job(
                            self._execute_schedule,
                            CronTrigger.from_crontab(sched["cron_expression"]),
                            id=job_id,
                            name=sched["name"],
                            args=[sched["id"]],
                            replace_existing=True,
                            misfire_grace_time=300,
                        )
                        logger.info(f"schedule_loaded: id={sched['id']} | name={sched['name']}")
                    except Exception as e:
                        logger.error(f"schedule_load_failed: id={sched['id']} | error={e}")
                        db.add_log(
                            message=f"Gagal load schedule {sched['name']}: {str(e)}",
                            level="ERROR",
                            schedule_id=sched["id"],
                        )

            for job_id in current_jobs:
                sid = int(job_id.replace("schedule_", ""))
                if not any(s["id"] == sid for s in active):
                    try:
                        self._scheduler.remove_job(job_id)
                        logger.info(f"schedule_removed: id={sid}")
                    except Exception as e:
                        logger.warning(f"schedule_remove_failed: id={sid} | error={e}")
                        db.add_log(
                            message=f"Gagal hapus schedule: {str(e)}",
                            level="WARNING",
                            schedule_id=sid,
                        )

        except Exception as e:
            logger.error(f"sync_failed: error={str(e)}")
            db.add_log(
                message=f"Scheduler sync gagal: {str(e)}",
                level="ERROR",
            )

    def _execute_schedule(self, schedule_id: int) -> None:
        sched = db.get_schedule(schedule_id)
        if not sched:
            logger.warning(f"schedule_not_found: id={schedule_id}")
            return

        if not sched["enabled"]:
            return

        logger.info(f"schedule_execute: id={schedule_id} | name={sched['name']}")

        db.update_schedule_last_run(schedule_id)

        if sched.get("action", "check") == "register":
            self._execute_registration(sched)
            return

        try:
            result = self._orchestrator.check_status(
                debitur_id=sched["debitur_id"],
                schedule_id=schedule_id,
                notify_telegram=bool(sched["notify_telegram"]),
                notify_email=bool(sched["notify_email"]),
            )
            logger.info(
                f"schedule_done: id={schedule_id} | name={sched['name']} | status={result.get('status')}"
            )
        except Exception as e:
            logger.error(f"schedule_execute_error: id={schedule_id} | error={e}")
            db.add_log(str(e), "ERROR", debitur_id=sched["debitur_id"], schedule_id=schedule_id)
            errors = db.increment_schedule_errors(schedule_id)
            if errors >= sched["max_errors"]:
                db.toggle_schedule(schedule_id, False)

    def _execute_registration(self, sched: dict[str, Any]) -> None:
        """Submit a pre-registration for the schedule's debitur.

        Unlike status-checking, registration has no prior debitur_id lookup
        need — the identity lives on the debitur row and the orchestrator
        (re)creates it per attempt. The schedule disables itself on success
        so it won't re-submit the same person.
        """
        debitur_id = sched["debitur_id"]
        deb = db.get_debitur(debitur_id)
        if not deb:
            logger.error(
                f"schedule_register_no_debitur: id={sched['id']} | debitur_id={debitur_id}"
            )
            db.toggle_schedule(sched["id"], False)
            return

        try:
            result = self._orchestrator.submit_registration(
                nama=deb["nama"],
                nik=deb["nik"],
                tempat_lahir=deb.get("tempat_lahir", ""),
                tanggal_lahir=deb.get("tanggal_lahir", ""),
                kewarganegaraan=deb.get("kewarganegaraan", "WNI"),
                jenis_identitas=deb.get("jenis_identitas", "KTP"),
                email=deb.get("email", ""),
                nomor_hp=deb.get("nomor_hp", ""),
                jenis_debitur=deb.get("jenis_debitur", "Perseorangan"),
            )
        except Exception as e:
            logger.error(f"schedule_register_error: id={sched['id']} | error={e}")
            db.add_log(str(e), "ERROR", debitur_id=debitur_id, schedule_id=sched["id"])
            self._count_error(sched)
            return

        status = result.get("status")
        success = result.get("success")
        logger.info(
            f"schedule_register_done: id={sched['id']} | status={status} | success={success}"
        )
        if success:
            # Registration accepted — stop retrying.
            db.toggle_schedule(sched["id"], False)
            logger.info(f"schedule_register_disabled_after_success: id={sched['id']}")
        elif status == "ERROR":
            self._count_error(sched)
        # QUOTA_FULL / inconclusive: leave enabled so it retries next window.

    def _count_error(self, sched: dict[str, Any]) -> None:
        errors = db.increment_schedule_errors(sched["id"])
        if errors >= sched["max_errors"]:
            db.toggle_schedule(sched["id"], False)

    def _handle_shutdown(self, signum: int, frame: Any) -> None:
        logger.info(f"scheduler_shutdown_signal: signal={signum}")
        self.stop()
        sys.exit(0)
