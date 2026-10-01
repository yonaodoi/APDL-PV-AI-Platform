from pathlib import Path
from tempfile import TemporaryDirectory

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from app.security import login_required
from app.services.local_speech_to_text import transcribe_audio


bp = Blueprint("voice", __name__, url_prefix="/voice")

MAX_AUDIO_BYTES = 15 * 1024 * 1024
SUPPORTED_AUDIO_EXTENSIONS = {
    ".m4a",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}


@bp.post("/transcribe")
@login_required
def transcribe_voice_entry():
    audio_file = request.files.get("audio")
    if audio_file is None or not audio_file.filename:
        return jsonify(error="Record audio before requesting transcription."), 400

    filename = secure_filename(audio_file.filename)
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_AUDIO_EXTENSIONS:
        return jsonify(
            error="Use a supported browser audio format and try again."
        ), 400

    audio_bytes = audio_file.read(MAX_AUDIO_BYTES + 1)
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        return jsonify(
            error="The recording is too large. Keep recordings under 90 seconds."
        ), 413
    if not audio_bytes:
        return jsonify(error="The audio recording is empty."), 400

    try:
        with TemporaryDirectory(prefix="apdl-voice-") as temp_directory:
            audio_path = Path(temp_directory) / f"recording{extension}"
            audio_path.write_bytes(audio_bytes)
            result = transcribe_audio(audio_path)
    except ValueError as exc:
        current_app.logger.warning("Local voice transcription failed: %s", exc)
        return jsonify(error=str(exc)), 422
    except RuntimeError as exc:
        current_app.logger.error("Local speech model unavailable: %s", exc)
        return jsonify(error=str(exc)), 503
    except Exception:
        current_app.logger.exception("Unexpected local voice transcription error")
        return jsonify(
            error="Transcription failed. Check the local speech model and try again."
        ), 503

    response = jsonify(
        text=result["text"],
        language=result.get("language"),
    )
    response.headers["Cache-Control"] = "no-store"
    return response
