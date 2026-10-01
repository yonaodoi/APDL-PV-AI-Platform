from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.voice.routes import bp


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(bp)
    return app


def test_transcription_route_uses_temporary_audio_file_and_returns_text():
    app = create_app()
    captured_paths = []

    def transcribe(path):
        captured_paths.append(Path(path))
        assert Path(path).read_bytes() == b"test audio"
        return {"text": "The patient reported dizziness.", "language": "en"}

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 5
        with patch("app.voice.routes.transcribe_audio", side_effect=transcribe):
            response = client.post(
                "/voice/transcribe",
                data={"audio": (BytesIO(b"test audio"), "voice.webm")},
                content_type="multipart/form-data",
            )

    assert response.status_code == 200
    assert response.json == {
        "text": "The patient reported dizziness.",
        "language": "en",
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert captured_paths
    assert not captured_paths[0].exists()


def test_transcription_route_rejects_unsupported_file_type():
    app = create_app()

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 5
        response = client.post(
            "/voice/transcribe",
            data={"audio": (BytesIO(b"not audio"), "recording.exe")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 400
    assert "supported browser audio format" in response.json["error"]


def test_transcription_route_rejects_empty_recordings():
    app = create_app()

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 5
        response = client.post(
            "/voice/transcribe",
            data={"audio": (BytesIO(b""), "voice.webm")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 400
    assert "empty" in response.json["error"]
