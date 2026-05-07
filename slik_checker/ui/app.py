"""Streamlit dashboard — main entry point."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from slik_checker.logging_config import setup_logging

setup_logging(output_format="console")

st.set_page_config(
    page_title="SLIK Auto-Checker",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.title("🔍 SLIK Auto-Checker")
st.sidebar.caption("Pengecekan Data SLIK Berkala")

menu = st.sidebar.radio(
    "Menu",
    ["Dashboard", "Daftar Debitur", "Status & History", "Schedules", "Settings"],
    label_visibility="collapsed",
)

if menu == "Dashboard":
    from slik_checker.ui import dashboard as page
elif menu == "Daftar Debitur":
    from slik_checker.ui import daftar as page
elif menu == "Status & History":
    from slik_checker.ui import history as page
elif menu == "Schedules":
    from slik_checker.ui import schedules as page
else:
    from slik_checker.ui import settings as page

page.show()
