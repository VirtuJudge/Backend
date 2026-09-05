from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from app.infrastructure.settings import Settings
from local_stack.gmail_smoke import (
    SYNTHETIC_BODY,
    SYNTHETIC_SUBJECT,
    SmokeGuardError,
    check_smoke_guard,
    main,
    run_smoke,
)


def test_smoke_guard_rejects_when_send_flag_omitted() -> None:
    with pytest.raises(SmokeGuardError, match="--send"):
        check_smoke_guard(
            recipient="test@example.com",
            allowlist_raw="test@example.com",
            send=False,
        )


def test_smoke_guard_rejects_when_recipient_not_in_allowlist() -> None:
    with pytest.raises(SmokeGuardError, match="GMAIL_SMOKE_ALLOWLIST"):
        check_smoke_guard(
            recipient="notallowed@example.com",
            allowlist_raw="test@example.com, other@example.com",
            send=True,
        )


def test_smoke_guard_rejects_when_allowlist_is_empty() -> None:
    with pytest.raises(SmokeGuardError, match="GMAIL_SMOKE_ALLOWLIST"):
        check_smoke_guard(
            recipient="test@example.com",
            allowlist_raw=None,
            send=True,
        )


def test_smoke_run_rejects_and_does_not_construct_smtp_when_guard_fails() -> None:
    settings = Settings(
        gmail_smoke_allowlist="allowed@example.com",
        gmail_smtp_username="sender@gmail.com",
        gmail_smtp_password=SecretStr("pw"),
        gmail_from_address="sender@gmail.com",
    )

    with patch("smtplib.SMTP") as mock_smtp:
        with pytest.raises(SmokeGuardError):
            run_smoke(
                recipient="forbidden@example.com",
                send=True,
                settings=settings,
            )
        mock_smtp.assert_not_called()

    with patch("smtplib.SMTP") as mock_smtp:
        with pytest.raises(SmokeGuardError):
            run_smoke(
                recipient="allowed@example.com",
                send=False,
                settings=settings,
            )
        mock_smtp.assert_not_called()


def test_smoke_run_succeeds_with_fixed_synthetic_message() -> None:
    settings = Settings(
        gmail_smoke_allowlist="allowed@example.com",
        gmail_smtp_username="sender@gmail.com",
        gmail_smtp_password=SecretStr("secret-pw"),
        gmail_from_address="sender@gmail.com",
    )

    mock_smtp_instance = MagicMock()
    mock_smtp_context = MagicMock()
    mock_smtp_context.__enter__.return_value = mock_smtp_instance

    with patch("smtplib.SMTP", return_value=mock_smtp_context) as mock_smtp:
        run_smoke(
            recipient="allowed@example.com",
            send=True,
            settings=settings,
        )

        mock_smtp.assert_called_once()
        mock_smtp_instance.login.assert_called_once_with("sender@gmail.com", "secret-pw")
        sent_msg: EmailMessage = mock_smtp_instance.send_message.call_args[0][0]
        assert sent_msg["To"] == "allowed@example.com"
        assert sent_msg["From"] == "sender@gmail.com"
        assert sent_msg["Subject"] == SYNTHETIC_SUBJECT
        assert sent_msg.get_content().strip() == SYNTHETIC_BODY


def test_smoke_cli_main(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(
        gmail_smoke_allowlist="allowed@example.com",
        gmail_smtp_username="sender@gmail.com",
        gmail_smtp_password=SecretStr("secret-pw"),
        gmail_from_address="sender@gmail.com",
    )

    # Missing recipient
    code = main([], settings=settings)
    assert code == 1
    captured = capsys.readouterr()
    assert "required" in captured.err

    # Missing --send
    code = main(["--recipient", "allowed@example.com"], settings=settings)
    assert code == 1
    captured = capsys.readouterr()
    assert "Guard error" in captured.err

    # Non-allowlisted recipient
    code = main(["--recipient", "hacker@example.com", "--send"], settings=settings)
    assert code == 1
    captured = capsys.readouterr()
    assert "Guard error" in captured.err

    # Successful run with mocked SMTP
    mock_smtp_instance = MagicMock()
    mock_smtp_context = MagicMock()
    mock_smtp_context.__enter__.return_value = mock_smtp_instance

    with patch("smtplib.SMTP", return_value=mock_smtp_context):
        code = main(["--recipient", "allowed@example.com", "--send"], settings=settings)
        assert code == 0
        captured = capsys.readouterr()
        assert "successfully" in captured.out
