"""Tests for notifier module."""

import pytest
from unittest.mock import patch, MagicMock

from slik_checker.notifier import Notifier


class TestNotifier:
    @patch("slik_checker.notifier.requests.post")
    def test_telegram_not_configured(self, mock_post, monkeypatch):
        notifier = Notifier()
        monkeypatch.setattr("slik_checker.notifier.settings.telegram_bot_token", None)
        result = notifier.send_telegram("test")
        assert result is False
        mock_post.assert_not_called()

    @patch("slik_checker.notifier.requests.post")
    def test_telegram_success(self, mock_post, monkeypatch):
        monkeypatch.setattr(
            "slik_checker.notifier.settings.telegram_bot_token",
            MagicMock(get_secret_value=lambda: "token"),
        )
        monkeypatch.setattr("slik_checker.notifier.settings.telegram_chat_id", "123")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        notifier = Notifier()
        result = notifier.send_telegram("hello")
        assert result is True

    @patch("slik_checker.notifier.smtplib.SMTP")
    def test_email_success(self, mock_smtp, monkeypatch):
        monkeypatch.setattr("slik_checker.notifier.settings.smtp_username", "u@test.com")
        monkeypatch.setattr(
            "slik_checker.notifier.settings.smtp_password",
            MagicMock(get_secret_value=lambda: "pass"),
        )
        monkeypatch.setattr("slik_checker.notifier.settings.notify_email", "to@test.com")

        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        notifier = Notifier()
        result = notifier.send_email("Subject", "<b>Body</b>")
        assert result is True
