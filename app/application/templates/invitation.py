# app/application/templates/invitation.py

import html

INVITATION_SUBJECT = "You're invited to join a team!"


def invitation_text_template(url: str) -> str:
    return (
        "Hello,\n\n"
        "You have been invited to join the team.\n\n"
        f"Please use the following link to accept the invitation:\n{url}\n\n"
        "Best regards,\n"
        "Team"
    )


def invitation_html_template(url: str) -> str:
    escaped_url = html.escape(url, quote=True)

    return (
        "<!DOCTYPE html>"
        "<html>"
        "<body>"
        "<p>Hello,</p>"
        "<p>You have been invited to join the team.</p>"
        f'<p><a href="{escaped_url}">Accept invitation</a></p>'
        "<p>Best regards,<br>Team</p>"
        "</body>"
        "</html>"
    )
