"""Daftar Debitur — register and manage debiturs."""

import re

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
            jenkel_sel = st.selectbox("Jenis Kelamin", ["Laki-laki (L)", "Perempuan (P)"])
            jenis_kelamin = "L" if "Laki" in jenkel_sel else "P"
            ibu_kandung = st.text_input("Nama Ibu Kandung", placeholder="Nama lengkap ibu kandung")
        with col2:
            jenis_debitur = st.selectbox(
                "Jenis Debitur", ["Perseorangan", "Badan Usaha", "Debitur Meninggal Dunia"]
            )
            kewarganegaraan = st.selectbox("Kewarganegaraan", ["WNI", "WNA"])
            jenis_identitas = st.selectbox("Jenis Identitas", ["KTP", "Paspor", "NPWP"])
            email = st.text_input("Email", placeholder="email@example.com")
            nomor_hp = st.text_input("Nomor HP", placeholder="08123456789")
            alamat = st.text_input("Alamat Lengkap", placeholder="Dusun, Desa, RT/RW")

        st.markdown("##### 📷 Berkas Dokumen (Opsional)")
        doc1, doc2, doc3 = st.columns(3)
        with doc1:
            f_ktp = st.file_uploader("Foto KTP Asli", type=["jpg", "jpeg", "png"], key="u_ktp")
        with doc2:
            f_selfie = st.file_uploader("Foto Selfie KTP", type=["jpg", "jpeg", "png"], key="u_selfie")
        with doc3:
            f_challenge = st.file_uploader("Foto Pose Challenge", type=["jpg", "jpeg", "png"], key="u_chal")

        st.markdown("---")
        if st.button("Daftarkan & Cek Sekarang", type="primary"):
            if not nama or not nik:
                st.error("Nama dan NIK wajib diisi!")
            elif not re.match(r"^\d{16}$", nik):
                st.error("NIK harus 16 digit angka!")
            else:
                from pathlib import Path
                ktp_dir = Path("data/ktp")
                ktp_dir.mkdir(parents=True, exist_ok=True)

                p_ktp = ""
                if f_ktp:
                    p_ktp = str(ktp_dir / f"ktp_{nik}.jpg")
                    Path(p_ktp).write_bytes(f_ktp.getvalue())

                p_selfie = ""
                if f_selfie:
                    p_selfie = str(ktp_dir / f"selfie_{nik}.jpg")
                    Path(p_selfie).write_bytes(f_selfie.getvalue())

                p_challenge = ""
                if f_challenge:
                    p_challenge = str(ktp_dir / f"challenge_{nik}.jpg")
                    Path(p_challenge).write_bytes(f_challenge.getvalue())

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
                        jenis_kelamin=jenis_kelamin,
                        alamat=alamat,
                        ibu_kandung=ibu_kandung,
                        ktp_path=p_ktp,
                        foto_selfie_path=p_selfie,
                        foto_challenge_path=p_challenge,
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
