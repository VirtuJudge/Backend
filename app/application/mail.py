from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MailMessage:
    recipient: str
    subject: str
    body: str
    html_body: str | None = None


class MailSender(Protocol):
    def send(self, message: MailMessage) -> None: ...


class MailConfigurationError(ValueError):
    pass


class MailDeliveryError(RuntimeError):
    pass
