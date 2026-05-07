"""Dashboard overview page."""

import streamlit as st
import pandas as pd

from slik_checker.models import db


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
    st.subheader("Log Aktivitas")
    logs = db.list_logs(limit=20)
    if logs:
        for log in logs:
            level = log["level"]
            icon = "🔴" if level == "ERROR" else "🟡" if level == "WARNING" else "🟢"
            st.text(f"{icon} [{(log.get('created_at') or '')[:19]}] {log['message']}")
    else:
        st.info("Belum ada log")
