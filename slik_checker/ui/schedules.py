"""Schedule management page."""

import streamlit as st

from slik_checker.models import db
from slik_checker.orchestrator import orchestrator
from slik_checker.scheduler import SchedulerDaemon

CRON_PRESETS = {
    "Setiap 6 jam": "0 */6 * * *",
    "Setiap 12 jam": "0 */12 * * *",
    "Setiap hari (06:00)": "0 6 * * *",
    "Setiap hari (18:00)": "0 18 * * *",
    "Setiap Senin (08:00)": "0 8 * * 1",
    "Setiap 2 hari": "0 8 */2 * *",
    "Setiap 1 minggu": "0 8 * * 0",
    "Setiap 1 bulan": "0 8 1 * *",
}


def show() -> None:
    st.title("Schedule Management")
    db.initialize()

    tab1, tab2 = st.tabs(["Buat Schedule", "List Schedules"])

    with tab1:
        st.subheader("Buat Schedule Baru")
        debiturs = db.list_debiturs()
        if not debiturs:
            st.info("Daftarkan debitur terlebih dahulu di menu Daftar Debitur")
            return

        labels = [f"{d['nama']} ({d['nik']})" for d in debiturs]
        selected = st.selectbox("Pilih Debitur", labels)
        idx = labels.index(selected)
        debitur_id = debiturs[idx]["id"]

        name = st.text_input("Nama Schedule", placeholder="Cth: Cek harian pagi")

        preset = st.selectbox("Preset Cron", ["Custom"] + list(CRON_PRESETS.keys()))
        if preset == "Custom":
            cron = st.text_input("Cron Expression", placeholder="0 8 * * *")
            st.caption("Format: menit jam hari bulan hari_dalam_minggu")
        else:
            cron = st.text_input("Cron Expression", value=CRON_PRESETS[preset], disabled=True)

        c1, c2 = st.columns(2)
        notify_telegram = c1.checkbox("Notifikasi Telegram", value=True)
        notify_email = c2.checkbox("Notifikasi Email", value=False)

        if st.button("Buat Schedule", type="primary"):
            if not name or not cron:
                st.error("Nama dan cron wajib diisi!")
            else:
                sid = db.add_schedule(debitur_id, name, cron, notify_telegram, notify_email)
                st.success(f"Schedule '{name}' dibuat (ID: {sid})")

    with tab2:
        st.subheader("Daftar Schedule")
        schedules = db.list_schedules()
        if not schedules:
            st.info("Belum ada schedule")
            return

        for s in schedules:
            enabled = bool(s["enabled"])
            icon = "🟢" if enabled else "🔴"
            with st.expander(f"{icon} {s['name']} — {s.get('nama', '-')} ({s['cron_expression']})"):
                c1, c2, c3 = st.columns(3)
                c1.metric("Enabled", "Ya" if enabled else "Tidak")
                c2.metric("Last Run", (s.get("last_run") or "-")[:19])
                c3.metric("Errors", f"{s['error_count']}/{s['max_errors']}")

                c1, c2, c3 = st.columns(3)
                if c1.toggle("Enable", enabled, key=f"tog_{s['id']}"):
                    if not enabled:
                        db.toggle_schedule(s["id"], True)
                        st.rerun()
                elif enabled:
                    db.toggle_schedule(s["id"], False)
                    st.rerun()

                if c2.button("Jalankan", key=f"run_{s['id']}"):
                    with st.spinner("Mengecek..."):
                        result = orchestrator.check_status(
                            debitur_id=s["debitur_id"],
                            schedule_id=s["id"],
                            notify_telegram=bool(s["notify_telegram"]),
                            notify_email=bool(s["notify_email"]),
                        )
                    if result.get("success"):
                        st.success(f"Status: {result['status']}")
                    else:
                        st.warning(f"{result.get('status')}: {result.get('message')}")

                if c3.button("Hapus", key=f"del_{s['id']}"):
                    db.delete_schedule(s["id"])
                    st.rerun()
