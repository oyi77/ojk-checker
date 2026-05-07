"""Command-line interface for SLIK Checker."""

from __future__ import annotations

import argparse
import sys

from slik_checker.config import settings
from slik_checker.logging_config import get_logger, setup_logging
from slik_checker.models import db

logger = get_logger(__name__)


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        prog="slik-checker",
        description="Automated periodic SLIK data checking via iDebKu OJK",
    )
    sub = parser.add_subparsers(dest="command")

    # --- init ---
    init_parser = sub.add_parser("init", help="Initialize database")

    # --- register ---
    reg_parser = sub.add_parser("register", help="Submit a new registration")
    reg_parser.add_argument("--nama", required=True)
    reg_parser.add_argument("--nik", required=True)
    reg_parser.add_argument("--tempat-lahir", default="")
    reg_parser.add_argument("--tanggal-lahir", default="")
    reg_parser.add_argument("--jenis-debitur", default="Perseorangan")
    reg_parser.add_argument("--kewarganegaraan", default="WNI")
    reg_parser.add_argument("--jenis-identitas", default="KTP")
    reg_parser.add_argument("--email", default="")
    reg_parser.add_argument("--nomor-hp", default="")

    # --- check ---
    check_parser = sub.add_parser("check", help="Check status of a registration")
    check_parser.add_argument("--debitur-id", type=int, required=True)
    check_parser.add_argument("--nomor", default="")

    # --- schedule ---
    sched_parser = sub.add_parser("schedule", help="Manage schedules")
    sched_subs = sched_parser.add_subparsers(dest="schedule_action")

    sched_add = sched_subs.add_parser("add", help="Add a schedule")
    sched_add.add_argument("--debitur-id", type=int, required=True)
    sched_add.add_argument("--name", required=True)
    sched_add.add_argument("--cron", required=True, help="Cron expression e.g. '0 8 * * *'")

    sched_list = sched_subs.add_parser("list", help="List schedules")
    sched_list.add_argument("--debitur-id", type=int)

    sched_remove = sched_subs.add_parser("remove", help="Remove a schedule")
    sched_remove.add_argument("--schedule-id", type=int, required=True)

    # --- list ---
    sub.add_parser("list", help="List all debiturs")

    # --- run scheduler ---
    sub.add_parser("run", help="Run the scheduler daemon")

    # --- ui ---
    sub.add_parser("ui", help="Launch the Streamlit dashboard")

    args = parser.parse_args()

    if args.command == "init":
        db.initialize()
        print("Database initialized at", settings.db_path)

    elif args.command == "register":
        db.initialize()
        from slik_checker.orchestrator import orchestrator

        result = orchestrator.submit_registration(
            nama=args.nama,
            nik=args.nik,
            tempat_lahir=args.tempat_lahir,
            tanggal_lahir=args.tanggal_lahir,
            jenis_debitur=args.jenis_debitur,
            kewarganegaraan=args.kewarganegaraan,
            jenis_identitas=args.jenis_identitas,
            email=args.email,
            nomor_hp=args.nomor_hp,
        )
        print(f"Status: {result['status']}")
        print(f"Success: {result['success']}")
        print(f"Nomor Pendaftaran: {result.get('nomor_pendaftaran')}")
        print(f"Message: {result.get('message', '')}")

    elif args.command == "check":
        db.initialize()
        from slik_checker.orchestrator import orchestrator

        result = orchestrator.check_status(
            debitur_id=args.debitur_id,
            nomor_pendaftaran=args.nomor,
        )
        print(f"Status: {result['status']}")
        print(f"Message: {result.get('message', '')}")

    elif args.command == "schedule":
        db.initialize()
        if args.schedule_action == "add":
            db.add_schedule(args.debitur_id, args.name, args.cron)
            print(f"Schedule '{args.name}' added")
        elif args.schedule_action == "list":
            for s in db.list_schedules(args.debitur_id):
                print(
                    f"  [{s['id']}] {s['name']} — {s['cron_expression']} (enabled={s['enabled']})"
                )
        elif args.schedule_action == "remove":
            db.delete_schedule(args.schedule_id)
            print(f"Schedule {args.schedule_id} removed")
        else:
            sched_parser.print_help()

    elif args.command == "list":
        db.initialize()
        for d in db.list_debiturs():
            print(
                f"  [{d['id']}] {d['nama']} — NIK: {d['nik']} "
                f"(no: {d.get('nomor_pendaftaran', '-')})"
            )

    elif args.command == "run":
        db.initialize()
        from slik_checker.scheduler import SchedulerDaemon

        daemon = SchedulerDaemon()
        daemon.start()

    elif args.command == "ui":
        import subprocess

        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", "slik_checker/ui/app.py"], check=False
        )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
