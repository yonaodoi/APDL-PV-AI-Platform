class FollowUpEmailError(RuntimeError):
    """Raised when a follow-up email cannot be sent."""


def send_follow_up_email(app, recipient, subject, body, attachment, filename):
    """Send one email with a Word attachment, by Gmail if connected,
    otherwise by SMTP."""
    from app.services.gmail_oauth import send_gmail_message

    try:
        send_gmail_message(app, recipient, subject, body, attachment, filename)
        return
    except RuntimeError as exc:
        if "not connected" not in str(exc):
            raise FollowUpEmailError(str(exc)) from exc

    if not app.config["SMTP_USERNAME"] or not app.config["SMTP_PASSWORD"]:
        raise FollowUpEmailError(
            "Email delivery is not configured. Set SMTP_USERNAME and "
            "SMTP_PASSWORD in the local .env file."
        )

    import smtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = (
        f"{_sender_name(app)} "
        f"<{app.config['SMTP_SENDER_EMAIL']}>"
    )
    message["To"] = recipient
    message.set_content(body)
    if attachment is not None:
        message.add_attachment(
            attachment.getvalue(),
            maintype="application",
            subtype=(
                "vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            filename=filename,
        )

    try:
        if app.config["SMTP_USE_SSL"]:
            with smtplib.SMTP_SSL(
                app.config["SMTP_HOST"],
                app.config["SMTP_PORT"],
                timeout=30,
            ) as server:
                server.login(
                    app.config["SMTP_USERNAME"],
                    app.config["SMTP_PASSWORD"],
                )
                server.send_message(message)
        else:
            with smtplib.SMTP(
                app.config["SMTP_HOST"],
                app.config["SMTP_PORT"],
                timeout=30,
            ) as server:
                if app.config["SMTP_USE_TLS"]:
                    server.starttls()
                server.login(
                    app.config["SMTP_USERNAME"],
                    app.config["SMTP_PASSWORD"],
                )
                server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise FollowUpEmailError(
            f"Email delivery failed: {exc}"
        ) from exc


def send_notification_email(app, recipient, subject, body):
    """Plain email without an attachment (approval notices)."""
    send_follow_up_email(app, recipient, subject, body, None, None)


def _sender_name(app):
    """Name shown on outgoing email: .env SMTP_SENDER_NAME, else the company's."""
    configured = app.config.get("SMTP_SENDER_NAME")
    if configured:
        return configured
    from app.services.company_profile import company

    with app.app_context():
        return f"{company()['short_name']} Pharmacovigilance"
