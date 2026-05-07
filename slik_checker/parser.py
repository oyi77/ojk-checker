"""Response parser for iDebKu OJK pages — detects statuses, kuota, and SweetAlert popups."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from bs4 import BeautifulSoup

from slik_checker.logging_config import get_logger

logger = get_logger(__name__)

KUOTA_PATTERNS = [
    r"melebihi\s+kuota",
    r"kuota\s+layanan\s+kami",
    r"pendaftaran.*ditutup",
    r"kuota\s+terpenuhi",
]

SESSION_REGEX = re.compile(
    r"Sesi\s+([IVX]+)\s*:\s*Pukul\s*(\d{2}\.\d{2})\s*WIB\s*s\.d\.\s*kuota\s*terpenuhi",
    re.IGNORECASE,
)


@dataclass
class ParseResult:
    success: bool
    status: str = "UNKNOWN"
    nomor_pendaftaran: Optional[str] = None
    message: str = ""
    extra: Optional[dict[str, Any]] = None


class Parser:
    def parse_pre_register(self, html: str) -> ParseResult:
        text_lower = BeautifulSoup(html, "html.parser").get_text().lower()

        if self._detect_kuota(html):
            sessions = SESSION_REGEX.findall(html)
            return ParseResult(
                success=False,
                status="QUOTA_FULL",
                message=self._format_kuota(sessions),
                extra={"sessions": [{"session": s, "time": t} for s, t in sessions]},
            )

        swal_msg = self._extract_sweetalert(html)
        if swal_msg:
            return ParseResult(success=False, status="ERROR", message=swal_msg)

        nomor = self._extract_nomor_pendaftaran(BeautifulSoup(html, "html.parser").get_text())
        if nomor:
            return ParseResult(
                success=True,
                status="REGISTERED",
                nomor_pendaftaran=nomor,
                message=f"Pendaftaran berhasil: {nomor}",
            )

        if self._has_success(text_lower):
            return ParseResult(
                success=True, status="NEXT_STEP", message="Form valid, lanjut ke step berikutnya"
            )

        if self._has_error(text_lower):
            return ParseResult(
                success=False, status="ERROR", message=self._extract_error_text(html)
            )

        return ParseResult(success=True, status="SUBMITTED", message="Form terkirim")

    def parse_status(self, html: str) -> ParseResult:
        text_lower = BeautifulSoup(html, "html.parser").get_text().lower()

        if self._detect_kuota(html):
            sessions = SESSION_REGEX.findall(html)
            return ParseResult(
                success=False,
                status="QUOTA_FULL",
                message=self._format_kuota(sessions),
            )

        swal_msg = self._extract_sweetalert(html)
        if swal_msg:
            nomor = self._extract_nomor_pendaftaran(BeautifulSoup(html, "html.parser").get_text())
            return ParseResult(
                success=False, status="ERROR", nomor_pendaftaran=nomor, message=swal_msg
            )

        nomor = self._extract_nomor_pendaftaran(BeautifulSoup(html, "html.parser").get_text())
        status = self._determine_status(text_lower)

        return ParseResult(
            success=True, status=status, nomor_pendaftaran=nomor, message=text_lower[:500]
        )

    @staticmethod
    def _detect_kuota(html: str) -> bool:
        return any(re.search(p, html, re.IGNORECASE | re.DOTALL) for p in KUOTA_PATTERNS)

    @staticmethod
    def _format_kuota(sessions: list) -> str:
        if not sessions:
            return "Kuota layanan iDebKu OJK sudah penuh."
        lines = [f"Sesi {n}: Pukul {t} WIB" for n, t in sessions]
        return "Kuota layanan iDebKu OJK sudah penuh.\n" + "\n".join(lines)

    @staticmethod
    def _extract_sweetalert(html: str) -> Optional[str]:
        matches = re.findall(r"swal\s*\(\s*\{[^}]*?\}", html, re.DOTALL)
        for m in matches:
            msg_type = re.search(r"type\s*:\s*'(\w+)'", m)
            if msg_type and msg_type.group(1) not in ("error", "warning", "info"):
                continue

            html_msg = re.search(r"html\s*:\s*'(.+?)'(?:\s*[,\)\}])", m, re.DOTALL)
            title = re.search(r"title\s*:\s*'(.+?)'(?:\s*[,\)\}])", m, re.DOTALL)

            parts = []
            if title:
                parts.append(title.group(1))
            if html_msg:
                parts.append(re.sub(r"<[^>]+>", "", html_msg.group(1)).strip())
            if parts:
                return "; ".join(parts)
        return None

    @staticmethod
    def _extract_nomor_pendaftaran(text: str) -> Optional[str]:
        m = re.search(r"nomor\s+pendaftaran[:\s]*(\S+)", text, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"no\.?\s*pendaftaran[:\s]*(\S+)", text, re.IGNORECASE)
        return m.group(1) if m else None

    @staticmethod
    def _has_success(text: str) -> bool:
        return any(
            kw in text
            for kw in [
                "berhasil",
                "selanjutnya",
                "lengkapi data",
                "unggah",
                "registrasi berhasil",
            ]
        )

    @staticmethod
    def _has_error(text: str) -> bool:
        return any(
            kw in text
            for kw in [
                "gagal",
                "error",
                "tidak valid",
                "salah",
                "captcha tidak sesuai",
            ]
        )

    @staticmethod
    def _determine_status(text: str) -> str:
        if "sedang diproses" in text or "diproses" in text:
            return "PROCESSING"
        if "selesai" in text:
            return "COMPLETED"
        if "ditolak" in text:
            return "REJECTED"
        if "menunggu" in text or "antrian" in text:
            return "WAITING"
        return "UNKNOWN"

    @staticmethod
    def _extract_error_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for cls in ["alert-danger", "validation-summary-errors", "field-validation-error"]:
            el = soup.find(class_=re.compile(cls, re.IGNORECASE))
            if el:
                return el.get_text(strip=True)
        return "Server error"


parser = Parser()
