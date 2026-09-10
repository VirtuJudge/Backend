import smtplib
import ssl
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from app.application.mail import (
    MailConfigurationError,
    MailDeliveryError,
    MailMessage,
)
from app.infrastructure.mail import (
    FakeMailSender,
    GmailMailSender,
    create_mail_sender,
)
from app.main import create_app
from app.settings import Settings


def test_mail_message_and_fake_sender() -> None:
    message = MailMessage(
        recipient="test@example.com",
        subject="Welcome",
        body="Plain text content",
    )
    sender = FakeMailSender()

    assert len(sender.sent_messages) == 0

    sender.send(message)

    assert len(sender.sent_messages) == 1
    assert sender.sent_messages[0] == message


def test_create_mail_sender_defaults_to_fake() -> None:
    settings = Settings(mail_backend="fake")
    sender = create_mail_sender(settings)

    assert isinstance(sender, FakeMailSender)


def test_gmail_sender_missing_configuration_fails_without_leaking_credentials() -> None:
    settings = Settings(
        mail_backend="gmail",
        gmail_smtp_username=None,
        gmail_smtp_password=None,
        gmail_from_address=None,
    )

    with pytest.raises(MailConfigurationError) as exc_info:
        create_mail_sender(settings)

    error_text = str(exc_info.value)
    assert "gmail_smtp_username" in error_text
    assert "gmail_smtp_password" in error_text
    assert "gmail_from_address" in error_text


def test_gmail_sender_sends_via_starttls() -> None:
    settings = Settings(
        mail_backend="gmail",
        gmail_smtp_host="smtp.gmail.com",
        gmail_smtp_port=587,
        gmail_smtp_username="sender@gmail.com",
        gmail_smtp_password=SecretStr("secret-app-password"),
        gmail_from_address="sender@gmail.com",
        gmail_smtp_timeout_seconds=5.0,
    )
    sender = GmailMailSender.from_settings(settings)
    message = MailMessage(
        recipient="recipient@example.com",
        subject="Subject line",
        body="Message body text",
        html_body="<p>Message body HTML</p>",
    )

    mock_smtp_instance = MagicMock()
    mock_smtp_context = MagicMock()
    mock_smtp_context.__enter__.return_value = mock_smtp_instance

    with patch("smtplib.SMTP", return_value=mock_smtp_context) as mock_smtp_cls:
        sender.send(message)

        mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=5.0)
        assert mock_smtp_instance.starttls.call_count == 1
        _, kwargs = mock_smtp_instance.starttls.call_args
        context = kwargs["context"]
        assert isinstance(context, ssl.SSLContext)
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        mock_smtp_instance.login.assert_called_once_with("sender@gmail.com", "secret-app-password")
        assert mock_smtp_instance.send_message.call_count == 1

        sent_msg: EmailMessage = mock_smtp_instance.send_message.call_args[0][0]
        assert sent_msg["From"] == "sender@gmail.com"
        assert sent_msg["To"] == "recipient@example.com"
        assert sent_msg["Subject"] == "Subject line"
        assert sent_msg.is_multipart()
        plain_body = sent_msg.get_body(preferencelist=("plain",))
        html_body = sent_msg.get_body(preferencelist=("html",))
        assert plain_body is not None
        assert html_body is not None
        assert plain_body.get_content().strip() == "Message body text"
        assert html_body.get_content().strip() == "<p>Message body HTML</p>"


def test_gmail_sender_safe_delivery_error_on_smtp_failure() -> None:
    settings = Settings(
        mail_backend="gmail",
        gmail_smtp_host="smtp.gmail.com",
        gmail_smtp_port=587,
        gmail_smtp_username="sender@gmail.com",
        gmail_smtp_password=SecretStr("secret-password"),
        gmail_from_address="sender@gmail.com",
    )
    sender = GmailMailSender.from_settings(settings)
    message = MailMessage(
        recipient="private-recipient@example.com",
        subject="Private Subject",
        body="Private Body containing token=12345",
    )

    mock_smtp_instance = MagicMock()
    mock_smtp_instance.login.side_effect = smtplib.SMTPAuthenticationError(
        535, b"password secret-password"
    )
    mock_smtp_context = MagicMock()
    mock_smtp_context.__enter__.return_value = mock_smtp_instance

    with patch("smtplib.SMTP", return_value=mock_smtp_context):
        with pytest.raises(MailDeliveryError) as exc_info:
            sender.send(message)

        assert exc_info.value.__cause__ is None
        error_msg = str(exc_info.value)
        assert "secret-password" not in error_msg
        assert "private-recipient@example.com" not in error_msg
        assert "token=12345" not in error_msg
        assert error_msg == "Failed to deliver email through Gmail SMTP"


def test_app_wiring_uses_configured_mail_sender() -> None:
    fake_settings = Settings(mail_backend="fake")
    app = create_app(fake_settings)
    assert isinstance(app.state.mail_sender, FakeMailSender)

    gmail_unconfigured = Settings(
        mail_backend="gmail",
        gmail_smtp_username=None,
        gmail_smtp_password=None,
        gmail_from_address=None,
    )
    with pytest.raises(MailConfigurationError):
        create_app(gmail_unconfigured)
