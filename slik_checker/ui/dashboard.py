"""Dashboard overview page with live log viewer."""

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd

from slik_checker.models import db


def _read_log_file(filepath: Path, max_lines: int = 200) -> list[str]:
    """Read last N lines from a log file efficiently."""
    if not filepath.exists():
        return []
    with open(filepath, "r") as f:
        # Seek near end for large files
        try:
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            # Read last 32KB or whole file if smaller
            chunk_size = min(fsize, 32768)
            f.seek(fsize - chunk_size)
            lines = f.read().splitlines()
            # Trim to first line boundary
            if len(lines) > 1:
                lines = lines[-(max_lines + 1):]
        except Exception:
            f.seek(0)
            lines = f.read().splitlines()[-max_lines:]
    return lines[-max_lines:]


def _parse_log_level(line: str) -> str:
    """Extract log level from structured log line."""
    m = re.search(r"\[(INFO|WARNING|ERROR|DEBUG)\s*\]", line)
    if m:
        return m.group(1)
    if "ERROR" in line or "GAGAL" in line or "Failed" in line or "failed" in line:
        return "ERROR"
    if "WARNING" in line or "warn" in line.lower():
        return "WARNING"
    return "INFO"


def _format_log_line(line: str) -> str:
    """Clean up log line for display."""
    if "pin_memory" in line or "UserWarning" in line:
        return None
    return line


def _count_by_level(logs: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {"INFO": 0, "WARNING": 0, "ERROR": 0, "DEBUG": 0}
    for line in logs:
        level = _parse_log_level(line)
        counts[level] = counts.get(level, 0) + 1
    return counts


def show() -> None:
    st.title("Dashboard")
    st.markdown("### Overview Pengecekan Data SLIK")

    db.initialize()
    stats = db.get_stats()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Debitur", stats["total_debiturs"])
    col2.metric("Schedules Aktif", stats["active_schedules"])
    col3.metric("Total Pengecekan", stats["total_results"])

    st.markdown("---")

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Status Terbaru")
        results = db.list_results(limit=10)
        if results:
            rows = [
                {
                    "Nama": r.get("nama"),
                    "NIK": r.get("nik"),
                    "No. Daftar": r.get("nomor_pendaftaran"),
                    "Status": r["status"],
                    "Waktu": (r.get("created_at") or "")[:19],
                }
                for r in results
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("Belum ada data")

    with col_right:
        st.subheader("Schedules Aktif")
        active = db.list_active_schedules()
        if active:
            rows = [
                {
                    "Nama": s.get("nama"),
                    "Schedule": s["name"],
                    "Cron": s["cron_expression"],
                    "Last Run": (s.get("last_run") or "-")[:19],
                    "Errors": s["error_count"],
                }
                for s in active
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("Belum ada schedule aktif")

    st.markdown("---")

    # ── Live Log Viewer ───────────────────────────────────────────────────
    st.subheader("📋 Live System Logs (stdout/stderr)")
    st.caption(
        "Log dari scheduler, captcha solver, dan proses registrasi. "
        "Halaman ini tidak auto-refresh — gunakan tombol 'Refresh' atau shortcut browser."
    )

    log_dir = Path(__file__).resolve().parent.parent.parent / "logs"

    # Filter controls
    filter_col1, filter_col2, filter_col3, _ = st.columns([1, 1, 1, 3])
    with filter_col1:
        level_filter = st.selectbox(
            "Filter Level",
            ["ALL", "INFO", "WARNING", "ERROR", "DEBUG"],
        )
    with filter_col2:
        max_lines = st.selectbox("Baris", [50, 100, 200, 500], index=2)
    with filter_col3:
        source_filter = st.selectbox("Sumber", ["stdout + stderr", "stdout", "stderr"])

    if st.button("🔄 Refresh", type="primary", use_container_width=False):
        pass  # Streamlit reruns on any interaction

    # Read log files
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"

    if source_filter == "stdout + stderr":
        raw_lines = _read_log_file(stdout_path, max_lines) + _read_log_file(stderr_path, max_lines)
    elif source_filter == "stdout":
        raw_lines = _read_log_file(stdout_path, max_lines)
    else:
        raw_lines = _read_log_file(stderr_path, max_lines)

    # Clean and filter
    formatted_lines = []
    for line in raw_lines:
        cleaned = _format_log_line(line)
        if cleaned is None:
            continue
        level = _parse_log_level(cleaned)
        if level_filter != "ALL" and level != level_filter:
            continue
        formatted_lines.append((level, cleaned))

    # Show level summary
    if raw_lines:
        counts = _count_by_level(raw_lines)
        summary_cols = st.columns(4)
        level_icons = {"INFO": "🟢", "WARNING": "🟡", "ERROR": "🔴", "DEBUG": "⚪"}
        for i, (lvl, icon) in enumerate(level_icons.items()):
            c = counts.get(lvl, 0)
            if c > 0:
                summary_cols[i].metric(f"{icon} {lvl}", c)
            else:
                summary_cols[i].metric(f"{icon} {lvl}", 0)

        # Display logs in a scrollable container
        with st.container(height=500):
            if not formatted_lines:
                st.info("Tidak ada log dengan filter tersebut")
            else:
                for level, line in formatted_lines:
                    if level == "ERROR":
                        st.error(line, icon="🚨")
                    elif level == "WARNING":
                        st.warning(line, icon="⚠️")
                    elif level == "DEBUG":
                        st.caption(line)
                    else:
                        st.info(line, icon="ℹ️")
    else:
        st.info("Belum ada log. Jalankan scheduler dengan `./slik run`")

    st.divider()

    # ── Captcha Sample Gallery ────────────────────────────────────────────
    st.subheader("🧪 Captcha Samples (Training Data)")
    captcha_dir = log_dir.parent / "data" / "captcha_samples"
    if captcha_dir.exists():
        samples = sorted(captcha_dir.glob("*.png"))
        sample_count = len(samples)
        st.caption(f"{sample_count} sampel captcha terkumpul — cocok untuk analisis akurasi OCR")

        if sample_count > 0:
            meta_files = sorted(captcha_dir.glob("*.meta.txt"))

            # Show some stats
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Total Captcha", sample_count)

            # Show last few samples
            with st.expander(f"Lihat {min(6, sample_count)} sampel terakhir", expanded=False):
                cols = st.columns(3)
                for i, sample_path in enumerate(samples[-6:]):
                    with cols[i % 3]:
                        st.image(str(sample_path), width=180)
                        meta_path = sample_path.with_suffix(".meta.txt")
                        if meta_path.exists():
                            with open(meta_path) as mf:
                                meta_content = mf.read().strip()
                            # Show only final line
                            final_line = [l for l in meta_content.split("\n") if l.startswith("final")]
                            if final_line:
                                st.caption(f"OCR: {final_line[0].split(': ')[-1]}")
    else:
        st.info("Belum ada sampel captcha")

    # ── Database Logs (fallback) ────────────────────────────────────────────
    st.subheader("🗄️ Database Logs")
    logs = db.list_logs(limit=20)
    if logs:
        for log in logs:
            level = log["level"]
            icon = "🔴" if level == "ERROR" else "🟡" if level == "WARNING" else "🟢"
            st.text(f"{icon} [{(log.get('created_at') or '')[:19]}] {log['message']}")
    else:
        st.info("Belum ada log di database")
