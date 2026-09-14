# VirtuJudge - Supabase Authentication Email Templates

These templates are tailored to match the VirtuJudge monochrome theme (`zinc-50` background `#fafafa`, `white` `#ffffff` cards, `zinc-200` `#e4e4e7` borders, `zinc-900` `#18181b` primary buttons/text). They use table-cell buttons and Outlook/Gmail-tested styling so they render cleanly in all major mail clients (Gmail, Outlook, Apple Mail, mobile apps, and dark mode).

---

## How to Apply in Supabase

1. Open your **[Supabase Dashboard](https://supabase.com/dashboard)**.
2. Select your project.
3. In the left navigation, go to **Authentication** &rarr; **Email Templates**.
4. For each template below:
   - Select the template from the list (e.g. *Confirm signup*).
   - Update the **Subject**.
   - Paste the corresponding HTML content into the **Message Body**.
   - Click **Save**.

---

## Templates Overview

### 1. Confirm Signup (`confirm_signup.html`)
- **Supabase Template**: *Confirm signup*
- **Recommended Subject**: `Confirm your email on VirtuJudge`
- **File**: `confirm_signup.html`
- **Variables**: `{{ .ConfirmationURL }}`
- **Purpose**: Sent when a user signs up to verify their email address.

---

### 2. Invite User (`invite_user.html`)
- **Supabase Template**: *Invite user*
- **Recommended Subject**: `You've been invited to join VirtuJudge`
- **File**: `invite_user.html`
- **Variables**: `{{ .ConfirmationURL }}`
- **Purpose**: Sent when an administrator invites a new user to the platform.

---

### 3. Magic Link (`magic_link.html`)
- **Supabase Template**: *Magic Link*
- **Recommended Subject**: `Your sign-in link for VirtuJudge`
- **File**: `magic_link.html`
- **Variables**: `{{ .ConfirmationURL }}`
- **Purpose**: Sent when a user requests passwordless sign-in via OTP / magic link.

---

### 4. Reset Password (`reset_password.html`)
- **Supabase Template**: *Reset Password*
- **Recommended Subject**: `Reset your password for VirtuJudge`
- **File**: `reset_password.html`
- **Variables**: `{{ .ConfirmationURL }}`
- **Purpose**: Sent when a user requests password recovery.

---

### 5. Change Email Address (`change_email.html`)
- **Supabase Template**: *Change Email Address*
- **Recommended Subject**: `Confirm your email change on VirtuJudge`
- **File**: `change_email.html`
- **Variables**: `{{ .ConfirmationURL }}`, `{{ .NewEmail }}`
- **Purpose**: Sent to confirm updating account email to a new address.

---

## Plain-Text Alternatives

If configuring plain text versions in Supabase or custom auth handlers:

### Confirm Signup:
```text
VirtuJudge

Hello,

Thank you for signing up for VirtuJudge. Please use the following link to confirm your email address:
{{ .ConfirmationURL }}

If you did not create an account on VirtuJudge, you can safely ignore this email.

Best regards,
The VirtuJudge team
```

### Invite User:
```text
VirtuJudge

Hello,

You have been invited to join VirtuJudge. Please use the following link to accept your invitation:
{{ .ConfirmationURL }}

If you were not expecting this invitation, you can safely ignore this email.

Best regards,
The VirtuJudge team
```

### Magic Link:
```text
VirtuJudge

Hello,

Click the link below to securely sign in to your VirtuJudge account:
{{ .ConfirmationURL }}

This link expires shortly. If you did not request this link, you can safely ignore this email.

Best regards,
The VirtuJudge team
```

### Reset Password:
```text
VirtuJudge

Hello,

We received a request to reset your password for VirtuJudge. Use the following link to choose a new password:
{{ .ConfirmationURL }}

If you did not request a password reset, you can safely ignore this email.

Best regards,
The VirtuJudge team
```

### Change Email Address:
```text
VirtuJudge

Hello,

We received a request to change your VirtuJudge account email address to {{ .NewEmail }}. Confirm this change by visiting:
{{ .ConfirmationURL }}

If you did not request this change, please contact support or secure your account immediately.

Best regards,
The VirtuJudge team
```
