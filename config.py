import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.environ.get("APDL_PV_SECRET_KEY")
    DATABASE_URL = os.environ.get("DATABASE_URL")

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
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-only-secret"
    DATABASE_URL = "postgresql://unused"