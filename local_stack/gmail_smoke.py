import argparse
import sys

from app.application.mail import (
    MailConfigurationError,
    MailDeliveryError,
    MailMessage,
)
from app.infrastructure.mail import GmailMailSender
from app.settings import Settings

SYNTHETIC_SUBJECT = "VirtuJudge Gmail Smoke Test"
SYNTHETIC_BODY = "This is a synthetic smoke test message from VirtuJudge local stack."


class SmokeGuardError(ValueError):
    pass


def parse_allowlist(allowlist_raw: str | None) -> list[str]:
    if not allowlist_raw:
        return []
    return [item.strip().lower() for item in allowlist_raw.split(",") if item.strip()]


def check_smoke_guard(recipient: str, allowlist_raw: str | None, send: bool) -> None:
    if not send:
        raise SmokeGuardError("Explicit --send flag is required to perform smoke test")
    allowlist = parse_allowlist(allowlist_raw)
    if not allowlist:
        raise SmokeGuardError("GMAIL_SMOKE_ALLOWLIST is empty or not configured")
    if recipient.strip().lower() not in allowlist:
        raise SmokeGuardError("Recipient does not match configured GMAIL_SMOKE_ALLOWLIST")


def run_smoke(recipient: str, send: bool, settings: Settings | None = None) -> None:
    resolved_settings = settings or Settings()
    check_smoke_guard(recipient, resolved_settings.gmail_smoke_allowlist, send)
    sender = GmailMailSender.from_settings(resolved_settings)
    message = MailMessage(
        recipient=recipient.strip(),
        subject=SYNTHETIC_SUBJECT,
        body=SYNTHETIC_BODY,
    )
    sender.send(message)


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description="VirtuJudge Gmail smoke test")
    parser.add_argument("--recipient", help="Recipient email address")
    parser.add_argument("--send", action="store_true", help="Explicit confirmation to send mail")

    args = parser.parse_args(argv)
    recipient = args.recipient
    if not recipient:
        sys.stderr.write("Error: Recipient email address is required\n")
        return 1

    try:
        run_smoke(recipient=recipient, send=args.send, settings=settings)
        sys.stdout.write("Gmail smoke test message sent successfully\n")
        return 0
    except SmokeGuardError as exc:
        sys.stderr.write(f"Guard error: {exc}\n")
        return 1
    except MailConfigurationError as exc:
        sys.stderr.write(f"Configuration error: {exc}\n")
        return 1
    except MailDeliveryError as exc:
        sys.stderr.write(f"Delivery error: {exc}\n")
        return 1
    except Exception:
        sys.stderr.write("Error: An unexpected error occurred during Gmail smoke test\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
