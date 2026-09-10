import smtplib
import ssl
from email.message import EmailMessage

from pydantic import SecretStr

from app.application.mail import (
    MailConfigurationError,
    MailDeliveryError,
    MailMessage,
    MailSender,
)
from app.settings import Settings


class FakeMailSender:
    def __init__(self) -> None:
        self.sent_messages: list[MailMessage] = []

    def send(self, message: MailMessage) -> None:
        self.sent_messages.append(message)


class GmailMailSender:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: SecretStr,
        from_address: str,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "GmailMailSender":
        missing: list[str] = []
        if not settings.gmail_smtp_host:
            missing.append("gmail_smtp_host")
        if not settings.gmail_smtp_port:
            missing.append("gmail_smtp_port")
        if not settings.gmail_smtp_username:
            missing.append("gmail_smtp_username")
        if not settings.gmail_smtp_password or not settings.gmail_smtp_password.get_secret_value():
            missing.append("gmail_smtp_password")
        if not settings.gmail_from_address:
            missing.append("gmail_from_address")

        if missing:
            raise MailConfigurationError(
                f"Missing required Gmail SMTP settings: {', '.join(missing)}"
            )

        assert settings.gmail_smtp_username is not None
        assert settings.gmail_smtp_password is not None
        assert settings.gmail_from_address is not None

        return cls(
            host=settings.gmail_smtp_host,
            port=settings.gmail_smtp_port,
            username=settings.gmail_smtp_username,
            password=settings.gmail_smtp_password,
            from_address=settings.gmail_from_address,
            timeout=settings.gmail_smtp_timeout_seconds,
        )

    def send(self, message: MailMessage) -> None:
        try:
            email_message = EmailMessage()
            email_message["From"] = self.from_address
            email_message["To"] = message.recipient
            email_message["Subject"] = message.subject
            email_message.set_content(message.body)
            if message.html_body is not None:
                email_message.add_alternative(message.html_body, subtype="html")

            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
                context = ssl.create_default_context()
                client.starttls(context=context)
                client.login(self.username, self.password.get_secret_value())
                client.send_message(email_message)
        except (smtplib.SMTPException, OSError, ValueError):
            raise MailDeliveryError("Failed to deliver email through Gmail SMTP") from None


def create_mail_sender(settings: Settings) -> MailSender:
    if settings.mail_backend == "fake":
        return FakeMailSender()
    return GmailMailSender.from_settings(settings)
