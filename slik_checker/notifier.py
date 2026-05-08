"""Notification manager for Telegram and Email alerts."""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from slik_checker.config import settings
from slik_checker.logging_config import get_logger
from slik_checker.models import db

logger = get_logger(__name__)


class Notifier:
    def send_telegram(self, message: str) -> bool:
        token = settings.telegram_bot_token
        chat_id = settings.telegram_chat_id

        if not token or not chat_id:
            logger.debug("telegram_not_configured")
            return False

        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token.get_secret_value()}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                logger.info("telegram_sent")
                return True
            logger.error(f"telegram_api_error: response={data}")
            return False
        except Exception as e:
            logger.error(f"telegram_send_failed: error={str(e)}")
            db.add_log(message=f"Telegram gagal: {str(e)}", level="ERROR")
            return False

    def send_email(self, subject: str, html_body: str) -> bool:
        if not settings.smtp_username or not settings.smtp_password:
            logger.debug("email_not_configured")
            return False

        to_addr = settings.notify_email or settings.smtp_username

        msg = MIMEMultipart("alternative")
        msg["From"] = settings.smtp_username
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(settings.smtp_username, settings.smtp_password.get_secret_value())
                server.send_message(msg)
            logger.info(f"email_sent: to={to_addr}")
            return True
        except Exception as e:
            logger.error(f"email_send_failed: error={str(e)}")
            db.add_log(message=f"Email gagal: {str(e)}", level="ERROR")
            return False

    def notify_registration(
        self, nama: str, nomor: str, telegram: bool = True, email: bool = False
    ) -> None:
        msg = f"<b>SLIK Checker — Pendaftaran</b>\n\nNama: {nama}\nNo: <code>{nomor}</code>"
        if telegram:
            self.send_telegram(msg)
        if email:
            self.send_email("SLIK Checker — Pendaftaran Berhasil", msg.replace("\n", "<br>"))

    def notify_status_change(
        self, nama: str, nomor: str, status: str, telegram: bool = True, email: bool = False
    ) -> None:
        msg = f"<b>SLIK Checker — Status Update</b>\n\nNama: {nama}\nNo: <code>{nomor}</code>\nStatus: <b>{status}</b>"
        if telegram:
            self.send_telegram(msg)
        if email:
            self.send_email("SLIK Checker — Status Update", msg.replace("\n", "<br>"))

    def notify_error(
        self, nama: str, error_msg: str, telegram: bool = True, email: bool = False
    ) -> None:
        msg = f"<b>SLIK Checker — Error</b>\n\nNama: {nama}\nError: {error_msg}"
        if telegram:
            self.send_telegram(msg)
        if email:
            self.send_email("SLIK Checker — Error", msg.replace("\n", "<br>"))


notifier = Notifier()
