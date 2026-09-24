class FollowUpEmailError(RuntimeError):
    """Raised when a follow-up email cannot be sent."""


def send_follow_up_email(app, recipient, task, document):
    from app.services.gmail_oauth import send_gmail_message

    try:
        send_gmail_message(app, recipient, task, document)
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
    message["Subject"] = (
        f"Case follow-up required - {task['case_number']}"
    )
    message["From"] = (
        f"{app.config['SMTP_SENDER_NAME']} "
        f"<{app.config['SMTP_SENDER_EMAIL']}>"
    )
    message["To"] = recipient
    message.set_content(
        "Please find attached the Abacus case follow-up form for "
        f"case {task['case_number']}. Please complete and return the form "
        "to the Pharmacovigilance team."
    )
    message.add_attachment(
        document.getvalue(),
        maintype="application",
        subtype=(
            "vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        filename=f"case-follow-up-{task['case_number']}-{task['task_id']}.docx",
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
