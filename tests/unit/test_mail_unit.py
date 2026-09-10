from app.application.mail import MailMessage
from app.infrastructure.mail import FakeMailSender


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
