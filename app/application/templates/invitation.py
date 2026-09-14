import html

INVITATION_SUBJECT = "You're invited to join a team on VirtuJudge"


def invitation_text_template(url: str) -> str:
    return (
        "VirtuJudge\n\n"
        "Hello,\n\n"
        "You have been invited to join a team on VirtuJudge.\n\n"
        "To accept the invitation, please use the following link:\n"
        f"{url}\n\n"
        "If you were not expecting this invitation, you can safely ignore this email.\n\n"
        "Best regards,\n"
        "The VirtuJudge team"
    )


def invitation_html_template(url: str) -> str:
    escaped_url = html.escape(url, quote=True)

    return (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">\n'
        '<html lang="en" xmlns="http://www.w3.org/1999/xhtml">\n'
        "<head>\n"
        '  <meta charset="utf-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        '  <meta http-equiv="X-UA-Compatible" content="IE=edge" />\n'
        '  <meta name="color-scheme" content="light dark" />\n'
        '  <meta name="supported-color-schemes" content="light dark" />\n'
        "  <title>VirtuJudge Invitation</title>\n"
        "  <!--[if mso]>\n"
        "  <noscript>\n"
        "    <xml>\n"
        "      <o:OfficeDocumentSettings>\n"
        "        <o:PixelsPerInch>96</o:PixelsPerInch>\n"
        "      </o:OfficeDocumentSettings>\n"
        "    </xml>\n"
        "  </noscript>\n"
        '  <style type="text/css">\n'
        "    body, table, td, h1, p, a, span {\n"
        "      font-family: Arial, Helvetica, sans-serif !important;\n"
        "    }\n"
        "  </style>\n"
        "  <![endif]-->\n"
        '  <style type="text/css">\n'
        "    :root {\n"
        "      color-scheme: light dark;\n"
        "      supported-color-schemes: light dark;\n"
        "    }\n"
        "    @media (prefers-color-scheme: dark) {\n"
        "      body, .vj-body {\n"
        "        background-color: #09090b !important;\n"
        "      }\n"
        "      .vj-card {\n"
        "        background-color: #18181b !important;\n"
        "        border-color: #27272a !important;\n"
        "      }\n"
        "      .vj-card-cell {\n"
        "        background-color: #18181b !important;\n"
        "      }\n"
        "      .vj-brand, .vj-heading {\n"
        "        color: #f4f4f5 !important;\n"
        "      }\n"
        "      .vj-text {\n"
        "        color: #d4d4d8 !important;\n"
        "      }\n"
        "      .vj-muted {\n"
        "        color: #a1a1aa !important;\n"
        "      }\n"
        "      .vj-button-cell {\n"
        "        background-color: #f4f4f5 !important;\n"
        "      }\n"
        "      .vj-button {\n"
        "        background-color: #f4f4f5 !important;\n"
        "        color: #18181b !important;\n"
        "        border-color: #f4f4f5 !important;\n"
        "      }\n"
        "      .vj-link {\n"
        "        color: #f4f4f5 !important;\n"
        "      }\n"
        "      .vj-divider {\n"
        "        background-color: #27272a !important;\n"
        "      }\n"
        "      .vj-footer {\n"
        "        color: #71717a !important;\n"
        "      }\n"
        "    }\n"
        "  </style>\n"
        "</head>\n"
        '<body class="vj-body" bgcolor="#fafafa" style="margin: 0; padding: 0; '
        "width: 100% !important; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; "
        "background-color: #fafafa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', "
        'Roboto, Helvetica, Arial, sans-serif;">\n'
        '  <center style="width: 100%; background-color: #fafafa;" class="vj-body">\n'
        '    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" '
        'bgcolor="#fafafa" class="vj-body" style="border-collapse: collapse; '
        'background-color: #fafafa;">\n'
        "      <tr>\n"
        '        <td align="center" bgcolor="#fafafa" class="vj-body" style="padding: 40px 16px; '
        'background-color: #fafafa;">\n'
        '          <div style="max-width: 560px; margin: 0 auto; width: 100%; text-align: left;">\n'
        "            <!--[if (gte mso 9)|(IE)]>\n"
        '            <table role="presentation" align="center" border="0" cellpadding="0" '
        'cellspacing="0" width="560" style="width: 560px;">\n'
        "            <tr>\n"
        "            <td>\n"
        "            <![endif]-->\n"
        '            <table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        'width="100%" style="border-collapse: collapse;">\n'
        "              <tr>\n"
        '                <td style="padding-bottom: 24px; text-align: center;">\n'
        '                  <span class="vj-brand" style="font-size: 20px; font-weight: 700; '
        'letter-spacing: -0.025em; color: #18181b; text-decoration: none;">VirtuJudge</span>\n'
        "                </td>\n"
        "              </tr>\n"
        "              <tr>\n"
        "                <td>\n"
        '                  <table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        'width="100%" bgcolor="#ffffff" class="vj-card" style="background-color: #ffffff; '
        "border: 1px solid #e4e4e7; border-radius: 8px; border-collapse: separate; "
        'overflow: hidden;">\n'
        "                    <tr>\n"
        '                      <td bgcolor="#ffffff" class="vj-card-cell" '
        'style="padding: 32px 28px; background-color: #ffffff; border-radius: 8px;">\n'
        '                        <h1 class="vj-heading" style="margin: 0 0 16px 0; '
        "font-size: 20px; font-weight: 600; line-height: 28px; letter-spacing: -0.015em; "
        'color: #18181b; mso-line-height-rule: exactly;">'
        "You&#39;re invited to join a team on VirtuJudge</h1>\n"
        '                        <p class="vj-text" style="margin: 0 0 24px 0; font-size: 15px; '
        'line-height: 24px; color: #3f3f46; mso-line-height-rule: exactly;">'
        "You have been invited to join a team on VirtuJudge. "
        "Click the button below to accept your invitation and access your workspace.</p>\n"
        '                        <table role="presentation" border="0" cellpadding="0" '
        'cellspacing="0" style="border-collapse: separate; margin: 0 0 28px 0;">\n'
        "                          <tr>\n"
        '                            <td align="center" bgcolor="#18181b" class="vj-button-cell" '
        'style="border-radius: 8px; background-color: #18181b; mso-padding-alt: 12px 24px;">\n'
        f'                              <a href="{escaped_url}" class="vj-button" target="_blank" '
        'style="display: inline-block; padding: 12px 24px; font-family: -apple-system, '
        "BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; "
        "font-weight: 600; line-height: 20px; color: #ffffff !important; text-decoration: none; "
        'border-radius: 8px; background-color: #18181b; border: 1px solid #18181b;">'
        "Accept invitation</a>\n"
        "                            </td>\n"
        "                          </tr>\n"
        "                        </table>\n"
        '                        <p class="vj-muted" style="margin: 0 0 8px 0; font-size: 13px; '
        'line-height: 20px; color: #71717a; mso-line-height-rule: exactly;">'
        "If the button above does not work, copy and paste this link into your browser:</p>\n"
        '                        <p style="margin: 0 0 28px 0; font-size: 13px; line-height: 20px; '
        "word-break: break-all; word-wrap: break-word; overflow-wrap: break-word; "
        'mso-line-height-rule: exactly;">\n'
        f'                          <a href="{escaped_url}" class="vj-link" target="_blank" '
        'style="color: #18181b !important; text-decoration: underline;">'
        f"{escaped_url}</a>\n"
        "                        </p>\n"
        '                        <table role="presentation" border="0" cellpadding="0" '
        'cellspacing="0" width="100%" style="border-collapse: collapse; margin: 0 0 24px 0;">\n'
        "                          <tr>\n"
        '                            <td height="1" bgcolor="#e4e4e7" class="vj-divider" '
        'style="height: 1px; line-height: 1px; font-size: 1px; background-color: #e4e4e7; '
        'mso-line-height-rule: exactly;">&zwnj;</td>\n'
        "                          </tr>\n"
        "                        </table>\n"
        '                        <p class="vj-muted" style="margin: 0 0 16px 0; font-size: 13px; '
        'line-height: 20px; color: #71717a; mso-line-height-rule: exactly;">'
        "If you were not expecting this invitation, you can safely ignore this email.</p>\n"
        '                        <p class="vj-text" style="margin: 0; font-size: 14px; '
        'line-height: 20px; color: #18181b; mso-line-height-rule: exactly;">'
        "Best regards,<br />The VirtuJudge team</p>\n"
        "                      </td>\n"
        "                    </tr>\n"
        "                  </table>\n"
        "                </td>\n"
        "              </tr>\n"
        "              <tr>\n"
        '                <td class="vj-footer" style="padding-top: 24px; text-align: center; '
        'font-size: 12px; line-height: 18px; color: #71717a; mso-line-height-rule: exactly;">\n'
        "                  &copy; VirtuJudge. AI-assisted pitch analysis and rehearsal.\n"
        "                </td>\n"
        "              </tr>\n"
        "            </table>\n"
        "            <!--[if (gte mso 9)|(IE)]>\n"
        "            </td>\n"
        "            </tr>\n"
        "            </table>\n"
        "            <![endif]-->\n"
        "          </div>\n"
        "        </td>\n"
        "      </tr>\n"
        "    </table>\n"
        "  </center>\n"
        "</body>\n"
        "</html>"
    )
