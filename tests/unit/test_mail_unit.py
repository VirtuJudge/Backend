import re
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from app.application.mail import MailMessage
from app.application.services.team_invitation_service import invitation_message
from app.application.templates.invitation import (
    INVITATION_SUBJECT,
    invitation_html_template,
    invitation_text_template,
)
from app.infrastructure.mail import (
    FakeMailSender,
    GmailMailSender,
    ResendMailSender,
)
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


def test_invitation_html_branding() -> None:
    url = "https://frontend.example.com/invitations/dummy-token"
    html_content = invitation_html_template(url)

    assert "VirtuJudge" in html_content
    assert 'class="vj-brand"' in html_content


def test_invitation_html_contains_one_primary_cta() -> None:
    url = "https://frontend.example.com/invitations/dummy-token"
    html_content = invitation_html_template(url)

    cta_matches = re.findall(
        r'<a\s+[^>]*href="([^"]+)"[^>]*class="vj-button"[^>]*>\s*Accept invitation\s*</a>',
        html_content,
    )
    assert len(cta_matches) == 1
    assert cta_matches[0] == url

    assert "#18181b" in html_content
    assert "#ffffff" in html_content
    assert "border-radius: 8px" in html_content


def test_invitation_html_visible_fallback_url() -> None:
    url = "https://frontend.example.com/invitations/dummy-token"
    html_content = invitation_html_template(url)

    assert "If the button above does not work" in html_content
    fallback_match = re.search(
        r'<a\s+[^>]*class="vj-link"[^>]*>([^<]+)</a>',
        html_content,
    )
    assert fallback_match is not None
    assert fallback_match.group(1) == url


def test_invitation_html_escapes_html_significant_characters() -> None:
    url_with_special_chars = (
        'https://frontend.example.com/invitations/test-token?role=<script>alert("xss")</script>'
        "&team='awesome'&tag=\"beta\""
    )
    html_content = invitation_html_template(url_with_special_chars)

    assert "<script>" not in html_content
    assert "</script>" not in html_content
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in html_content
    assert "&amp;team=&#x27;awesome&#x27;" in html_content
    assert "&amp;tag=&quot;beta&quot;" in html_content

    expected_href_part = (
        'href="https://frontend.example.com/invitations/test-token?role=&lt;script&gt;'
    )
    assert expected_href_part in html_content


def test_invitation_text_essential_copy_and_url() -> None:
    url = "https://frontend.example.com/invitations/dummy-token"
    text_content = invitation_text_template(url)

    assert "VirtuJudge" in text_content
    assert "invited to join a team on VirtuJudge" in text_content
    assert url in text_content
    assert "safely ignore this email" in text_content
    assert "The VirtuJudge team" in text_content


def test_invitation_html_no_remote_resources() -> None:
    url = "https://frontend.example.com/invitations/dummy-token"
    html_content = invitation_html_template(url)

    assert "<script" not in html_content.lower()
    assert "<img" not in html_content.lower()
    assert "<svg" not in html_content.lower()
    assert "<iframe" not in html_content.lower()
    assert "<link" not in html_content.lower()
    assert "@import" not in html_content.lower()
    assert "url(" not in html_content.lower()


def test_invitation_message_multipart_behavior() -> None:
    recipient = "invitee@example.com"
    url = "https://frontend.example.com/invitations/token-123"

    message = invitation_message(recipient, url)

    assert message.recipient == recipient
    assert message.subject == INVITATION_SUBJECT
    assert "VirtuJudge" in message.subject
    assert message.body is not None
    assert url in message.body
    assert message.html_body is not None
    assert url in message.html_body


def test_invitation_template_compatible_with_mail_adapters() -> None:
    recipient = "invitee@example.com"
    url = "https://frontend.example.com/invitations/token-123"
    message = invitation_message(recipient, url)

    # 1. Gmail adapter compatibility
    settings_gmail = Settings(
        mail_backend="gmail",
        gmail_smtp_host="smtp.gmail.com",
        gmail_smtp_port=587,
        gmail_smtp_username="sender@example.com",
        gmail_smtp_password=SecretStr("mock-password"),
        gmail_from_address="sender@example.com",
    )
    gmail_sender = GmailMailSender.from_settings(settings_gmail)

    mock_smtp_instance = MagicMock()
    mock_smtp_context = MagicMock()
    mock_smtp_context.__enter__.return_value = mock_smtp_instance

    with patch("smtplib.SMTP", return_value=mock_smtp_context):
        gmail_sender.send(message)

        mock_smtp_instance.send_message.assert_called_once()
        sent_email: EmailMessage = mock_smtp_instance.send_message.call_args[0][0]
        assert sent_email.is_multipart()
        plain_part = sent_email.get_body(preferencelist=("plain",))
        html_part = sent_email.get_body(preferencelist=("html",))
        assert plain_part is not None
        assert html_part is not None
        assert url in plain_part.get_content()
        assert url in html_part.get_content()
        assert "VirtuJudge" in plain_part.get_content()
        assert "VirtuJudge" in html_part.get_content()

    # 2. Resend adapter compatibility
    settings_resend = Settings(
        mail_backend="resend",
        resend_api_key=SecretStr("mock-key"),
        resend_from_address="VirtuJudge <noreply@example.com>",
    )
    resend_sender = ResendMailSender.from_settings(settings_resend)
    mock_response = MagicMock()

    with patch("httpx.post", return_value=mock_response) as mock_post:
        resend_sender.send(message)

        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert payload["to"] == ["invitee@example.com"]
        assert payload["subject"] == INVITATION_SUBJECT
        assert url in payload["text"]
        assert url in payload["html"]
        assert "VirtuJudge" in payload["text"]
        assert "VirtuJudge" in payload["html"]
