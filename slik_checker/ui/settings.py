"""Settings page."""

import streamlit as st

from slik_checker.config import settings
from slik_checker.models import db
from slik_checker.scheduler import SchedulerDaemon


def show() -> None:
    st.title("Settings")
    db.initialize()

    tab1, tab2 = st.tabs(["Scheduler", "About"])

    with tab1:
        st.subheader("Scheduler Control")
        c1, c2 = st.columns(2)
        c1.metric("Database", str(settings.db_path))
        try:
            size = settings.db_path.stat().st_size if settings.db_path.exists() else 0
            c2.metric("DB Size", f"{size / 1024:.1f} KB" if size else "0 KB")
        except Exception:
            c2.metric("DB Size", "N/A")

        st.markdown("---")
        st.markdown("#### Cron Expression Guide")
        st.code(
            """
# ┌────────── minute (0-59)
# │ ┌──────── hour (0-23)
# │ │ ┌────── day of month (1-31)
# │ │ │ ┌──── month (1-12)
# │ │ │ │ ┌── day of week (0-6, 0=Sun)
# │ │ │ │ │
# * * * * *

# Examples:
0 8 * * *      # Every day at 08:00
0 */6 * * *    # Every 6 hours
0 8 * * 1      # Every Monday at 08:00
0 8 1 * *      # Every 1st of month at 08:00
        """.strip()
        )

    with tab2:
        st.markdown("""
        **SLIK Auto-Checker** v1.0

        Sistem pengecekan berkala data SLIK melalui iDebKu OJK.

        - Website: [idebku.ojk.go.id](https://idebku.ojk.go.id)
        - Layanan: Gratis / tidak dipungut biaya
        - Proses: 1 hari kerja

        **Tech Stack:**
        - Python 3.10+
        - Multi-engine captcha (easyocr + ddddocr + tesseract)
        - APScheduler for scheduling
        - SQLite for persistence
        - Streamlit for dashboard
        - Telegram + Email notifications
        """)
