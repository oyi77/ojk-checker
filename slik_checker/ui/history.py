"""History & Status page."""

import streamlit as st
import pandas as pd

from slik_checker.models import db


def show() -> None:
    st.title("Status & History")
    db.initialize()

    debiturs = db.list_debiturs()
    options = {"Semua": None} | {f"{d['nama']} ({d['nik']})": d["id"] for d in debiturs}

    col1, col2 = st.columns(2)
    with col1:
        selected = st.selectbox("Filter Debitur", list(options.keys()))
    with col2:
        limit = st.number_input("Jumlah data", 10, 500, 50, 10)

    results = db.list_results(debitur_id=options[selected], limit=limit)
    if not results:
        st.info("Belum ada data")
        return

    status_counts = {}
    for r in results:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    cols = st.columns(len(status_counts))
    for i, (status, count) in enumerate(status_counts.items()):
        cols[i].metric(status, count)

    st.markdown("---")
    rows = [
        {
            "Waktu": (r.get("created_at") or "")[:19],
            "Nama": r.get("nama", "-"),
            "NIK": r.get("nik", "-"),
            "No. Daftar": r.get("nomor_pendaftaran", "-"),
            "Status": r["status"],
            "Success": "✅" if r.get("success") else "❌",
        }
        for r in results
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
