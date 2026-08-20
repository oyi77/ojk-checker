"""Schedule management page."""

import streamlit as st

from slik_checker.models import db
from slik_checker.orchestrator import orchestrator

CRON_PRESETS = {
    "Sesi iDebKu OJK (Menit 0-10 tiap sesi)": "0-10 7,9,12,14 * * *",
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
        action = st.selectbox(
            "Tipe Aksi",
            ["check", "register"],
            format_func=lambda a: "🔍 Cek Status Layanan" if a == "check" else "📝 Pendaftaran iDebKu Baru",
        )

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
                sid = db.add_schedule(
                    debitur_id=debitur_id,
                    name=name,
                    cron=cron,
                    telegram=notify_telegram,
                    email=notify_email,
                    action=action,
                )
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
            action_label = "📝 Register" if s.get("action") == "register" else "🔍 Check"
            with st.expander(f"{icon} [{action_label}] {s['name']} — {s.get('nama', '-')} ({s['cron_expression']})"):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Aksi", s.get("action", "check").upper())
                c2.metric("Enabled", "Ya" if enabled else "Tidak")
                c3.metric("Last Run", (s.get("last_run") or "-")[:19])
                c4.metric("Errors", f"{s['error_count']}/{s['max_errors']}")

                c1, c2, c3 = st.columns(3)
                if c1.toggle("Enable", enabled, key=f"tog_{s['id']}"):
                    if not enabled:
                        db.toggle_schedule(s["id"], True)
                        st.rerun()
                elif enabled:
                    db.toggle_schedule(s["id"], False)
                    st.rerun()

                if c2.button("Jalankan", key=f"run_{s['id']}"):
                    if s.get("action") == "register":
                        with st.spinner("Mendaftarkan ke iDebKu OJK..."):
                            deb = db.get_debitur(s["debitur_id"])
                            if not deb:
                                st.error("Debitur tidak ditemukan!")
                            else:
                                result = orchestrator.submit_registration(
                                    nama=deb["nama"],
                                    nik=deb["nik"],
                                    tempat_lahir=deb.get("tempat_lahir") or "",
                                    tanggal_lahir=deb.get("tanggal_lahir") or "",
                                    kewarganegaraan=deb.get("kewarganegaraan") or "WNI",
                                    jenis_identitas=deb.get("jenis_identitas") or "KTP",
                                    email=deb.get("email") or "",
                                    nomor_hp=deb.get("nomor_hp") or "",
                                    jenis_debitur=deb.get("jenis_debitur") or "Perseorangan",
                                    ktp_path=deb.get("ktp_path") or "",
                                    jenis_kelamin=deb.get("jenis_kelamin") or "L",
                                    alamat=deb.get("alamat") or "",
                                    kode_provinsi=deb.get("kode_provinsi") or "12",
                                    kode_kota=deb.get("kode_kota") or "1204",
                                    ibu_kandung=deb.get("ibu_kandung") or "",
                                    tujuan_permohonan=deb.get("tujuan_permohonan") or 41,
                                    foto_selfie_path=deb.get("foto_selfie_path") or "",
                                    foto_challenge_path=deb.get("foto_challenge_path") or "",
                                )
                                if result.get("success"):
                                    st.success(f"Pendaftaran Berhasil! No. Daftar: {result.get('nomor_pendaftaran')}")
                                else:
                                    st.warning(f"Status: {result.get('status')} | Pesan: {result.get('message')}")
                    else:
                        with st.spinner("Mengecek status di iDebKu OJK..."):
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
