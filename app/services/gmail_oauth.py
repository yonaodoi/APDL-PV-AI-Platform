import base64
from email.message import EmailMessage

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow
from googleapiclient.discovery import build


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def build_authorization_url(app, state):
    if not app.config["GMAIL_OAUTH_CLIENT_SECRET_PATH"].exists():
        raise RuntimeError(
            "Gmail OAuth client credentials are not installed."
        )
    flow = Flow.from_client_secrets_file(
        str(app.config["GMAIL_OAUTH_CLIENT_SECRET_PATH"]),
        scopes=[GMAIL_SEND_SCOPE],
        state=state,
    )
    flow.redirect_uri = app.config["GMAIL_OAUTH_REDIRECT_URI"]
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


def authorize_desktop_app(app):
    if not app.config["GMAIL_OAUTH_CLIENT_SECRET_PATH"].exists():
        raise RuntimeError(
            "Gmail OAuth client credentials are not installed."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(app.config["GMAIL_OAUTH_CLIENT_SECRET_PATH"]),
        scopes=[GMAIL_SEND_SCOPE],
    )
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )
    app.config["GMAIL_OAUTH_TOKEN_PATH"].write_text(
        credentials.to_json(),
        encoding="utf-8",
    )


def complete_authorization(app, authorization_response, state):
    flow = Flow.from_client_secrets_file(
        str(app.config["GMAIL_OAUTH_CLIENT_SECRET_PATH"]),
        scopes=[GMAIL_SEND_SCOPE],
        state=state,
    )
    flow.redirect_uri = app.config["GMAIL_OAUTH_REDIRECT_URI"]
    flow.fetch_token(authorization_response=authorization_response)
    app.config["GMAIL_OAUTH_TOKEN_PATH"].write_text(
        flow.credentials.to_json(),
        encoding="utf-8",
    )


def send_gmail_message(app, recipient, task, document):
    token_path = app.config["GMAIL_OAUTH_TOKEN_PATH"]
    if not token_path.exists():
        raise RuntimeError(
            "Gmail is not connected. Use the Connect Gmail action first."
        )
    credentials = Credentials.from_authorized_user_file(
        str(token_path),
        scopes=[GMAIL_SEND_SCOPE],
    )
    if credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request

        credentials.refresh(Request())
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise RuntimeError(
            "Gmail authorization has expired. Connect Gmail again."
        )

    message = EmailMessage()
    message["Subject"] = f"Case follow-up required - {task['case_number']}"
    message["From"] = app.config["SMTP_SENDER_EMAIL"]
    message["To"] = recipient
    message.set_content(
        "Please find attached the Abacus case follow-up form for "
        f"case {task['case_number']}. Please complete and return the form "
        "to the Pharmacovigilance team."
    )
    message.add_attachment(
        document.getvalue(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"case-follow-up-{task['case_number']}-{task['task_id']}.docx",
    )
    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode("utf-8")
    build("gmail", "v1", credentials=credentials).users().messages().send(
        userId="me",
        body={"raw": encoded_message},
    ).execute()
