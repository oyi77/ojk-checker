"""Daftar Debitur — register and manage debiturs."""

import re
import os
import streamlit as st

from slik_checker.models import db
from slik_checker.orchestrator import orchestrator


def show() -> None:
    st.title("Daftar / Edit Debitur")
    db.initialize()

    tab1, tab2 = st.tabs(["Daftarkan Debitur", "List Debitur"])

    with tab1:
        st.subheader("Form Pendaftaran Debitur SLIK")
        col1, col2 = st.columns(2)
        with col1:
            nama = st.text_input("Nama Lengkap *", placeholder="Sesuai KTP")
            nik = st.text_input("NIK * (16 digit)", max_chars=16, placeholder="Nomor KTP")
            tempat_lahir = st.text_input("Tempat Lahir", placeholder="Kota kelahiran")
            tgl = st.date_input("Tanggal Lahir", key="daftar_tgl_lahir")
            tanggal_lahir = tgl.strftime("%d/%m/%Y") if tgl else ""
        with col2:
            jenis_debitur = st.selectbox(
                "Jenis Debitur", ["Perseorangan", "Badan Usaha", "Debitur Meninggal Dunia"]
            )
            kewarganegaraan = st.selectbox("Kewarganegaraan", ["WNI", "WNA"])
            jenis_identitas = st.selectbox("Jenis Identitas", ["KTP", "Paspor", "NPWP"])
            email = st.text_input("Email")
            nomor_hp = st.text_input("Nomor HP", placeholder="08123456789")

        st.markdown("---")
        if st.button("Daftarkan & Cek Sekarang", type="primary"):
            if not nama or not nik:
                st.error("Nama dan NIK wajib diisi!")
            elif not re.match(r"^\d{16}$", nik):
                st.error("NIK harus 16 digit angka!")
            else:
                with st.spinner("Mendaftarkan ke iDebKu..."):
                    result = orchestrator.submit_registration(
                        nama=nama,
                        nik=nik,
                        tempat_lahir=tempat_lahir,
                        tanggal_lahir=tanggal_lahir,
                        jenis_debitur=jenis_debitur,
                        kewarganegaraan=kewarganegaraan,
                        jenis_identitas=jenis_identitas,
                        email=email,
                        nomor_hp=nomor_hp,
                    )
                if result["success"]:
                    st.success(
                        f"Pendaftaran berhasil! No: {result.get('nomor_pendaftaran', 'N/A')}"
                    )
                    st.info(f"Status: {result['status']}")
                elif result["status"] == "QUOTA_FULL":
                    st.warning(result.get("message", "Kuota penuh"))
                else:
                    st.error(result.get("message", "Gagal"))

    with tab2:
        st.subheader("List Debitur Terdaftar")
        debiturs = db.list_debiturs()
        if not debiturs:
            st.info("Belum ada debitur terdaftar")
            return

        for d in debiturs:
            with st.expander(
                f"{d['nama']} — NIK: {d['nik']} ({d.get('nomor_pendaftaran', 'Belum terdaftar')})"
            ):
                c1, c2, c3 = st.columns(3)
                c1.metric("Jenis", d.get("jenis_debitur", "-"))
                c2.metric("No. Pendaftaran", d.get("nomor_pendaftaran", "-"))
                c3.metric("Dibuat", (d.get("created_at") or "-")[:19])

                if st.button("Cek Status", key=f"check_{d['id']}"):
                    with st.spinner("Mengecek..."):
                        result = orchestrator.check_status(debitur_id=d["id"])
                    if result["success"]:
                        st.success(f"Status: {result['status']}")
                    else:
                        st.warning(f"{result.get('status')}: {result.get('message')}")

                if st.button("Hapus", key=f"del_{d['id']}"):
                    db.delete_debitur(d["id"])
                    st.warning(f"Debitur '{d['nama']}' dihapus")
                    st.rerun()
