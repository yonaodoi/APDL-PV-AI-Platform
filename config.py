import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.environ.get("APDL_PV_SECRET_KEY")
    DATABASE_URL = os.environ.get("DATABASE_URL")
    DEV_AUTO_LOGIN = (
        os.environ.get("DEV_AUTO_LOGIN", "false").lower() == "true"
        and os.environ.get("FLASK_ENV") != "production"
    )
    DEV_AUTO_LOGIN_USERNAME = os.environ.get(
        "DEV_AUTO_LOGIN_USERNAME",
        "yona.odoi",
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = (
        os.environ.get("FLASK_ENV") == "production"
    )

    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(
            os.environ.get("SESSION_MINUTES", "30")
        )
    )

    MAX_CONTENT_LENGTH = (
        int(os.environ.get("MAX_UPLOAD_MB", "20"))
        * 1024
        * 1024
    )

    UPLOAD_ROOT = Path(
        os.environ.get(
            "UPLOAD_ROOT",
            BASE_DIR / "instance" / "uploads",
        )
    )

    ADR_TEMPLATE_PATH = Path(
        os.environ.get(
            "ADR_TEMPLATE_PATH",
            BASE_DIR
            / "controlled_templates"
            / "8.1ADR REPORTING FORM.docx",
        )
    )

    PSUR_TEMPLATE_PATH = Path(
        os.environ.get(
            "PSUR_TEMPLATE_PATH",
            BASE_DIR
            / "controlled_templates"
            / "17.3 PSUR TEMPLATE REVISED.docx",
        )
    )

    ABACUS_FOLLOW_UP_TEMPLATE_PATH = Path(
        os.environ.get(
            "ABACUS_FOLLOW_UP_TEMPLATE_PATH",
            BASE_DIR
            / "controlled_templates"
            / "case followup form.pdf",
        )
    )

    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
    SMTP_SENDER_EMAIL = os.environ.get(
        "SMTP_SENDER_EMAIL",
        "odoiwilber2@gmail.com",
    )
    SMTP_SENDER_NAME = os.environ.get(
        "SMTP_SENDER_NAME",
        "APDL Pharmacovigilance",
    )
    GMAIL_OAUTH_CLIENT_SECRET_PATH = Path(
        os.environ.get(
            "GMAIL_OAUTH_CLIENT_SECRET_PATH",
            BASE_DIR / "instance" / "gmail_client_secret.json",
        )
    )
    GMAIL_OAUTH_TOKEN_PATH = Path(
        os.environ.get(
            "GMAIL_OAUTH_TOKEN_PATH",
            BASE_DIR / "instance" / "gmail_token.json",
        )
    )
    GMAIL_OAUTH_REDIRECT_URI = os.environ.get(
        "GMAIL_OAUTH_REDIRECT_URI",
        "http://localhost:5000/",
    )
    SMTP_USERNAME = os.environ.get(
        "SMTP_USERNAME",
        SMTP_SENDER_EMAIL,
    )
    # App passwords are shown in groups with gaps; copying them often brings
    # invisible non-breaking spaces or quotes, which make Gmail hang up.
    SMTP_PASSWORD = (
        "".join(
            ch for ch in os.environ.get("SMTP_PASSWORD", "")
            if not ch.isspace() and ch not in "\"'\u200b\ufeff"
        )
        or None
    )
    SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "true").lower() == "true"
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").lower() == "true"

    # Automatic follow-up. Requests and reminders are emailed without a click
    # once email sending is set up (Gmail connection or SMTP_PASSWORD).
    # Set FOLLOW_UP_AUTO_SEND=false in .env to switch it off.
    FOLLOW_UP_AUTO_SEND = (
        os.environ.get("FOLLOW_UP_AUTO_SEND", "true").lower() == "true"
    )
    FOLLOW_UP_GRACE_HOURS = int(os.environ.get("FOLLOW_UP_GRACE_HOURS", "24"))
    FOLLOW_UP_REMINDER_DAYS = int(os.environ.get("FOLLOW_UP_REMINDER_DAYS", "7"))
    FOLLOW_UP_MAX_REMINDERS = int(os.environ.get("FOLLOW_UP_MAX_REMINDERS", "2"))
    FOLLOW_UP_CHECK_MINUTES = int(os.environ.get("FOLLOW_UP_CHECK_MINUTES", "60"))
    @classmethod
    def validate(cls):
        missing = []

        if not cls.SECRET_KEY:
            missing.append("APDL_PV_SECRET_KEY")

        if not cls.DATABASE_URL:
            missing.append("DATABASE_URL")

        if missing:
            raise RuntimeError(
                "Missing required environment settings: "
                + ", ".join(missing)
            )

        if len(cls.SECRET_KEY) < 32:
            raise RuntimeError(
                "APDL_PV_SECRET_KEY must contain "
                "at least 32 characters."
            )


class TestingConfig(Config):
    TESTING = True
    FOLLOW_UP_AUTO_SEND = False
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-only-secret"
    DATABASE_URL = "postgresql://unused"