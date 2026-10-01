import os
from functools import lru_cache


WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
MAX_AUDIO_SECONDS = 90


@lru_cache(maxsize=1)
def _get_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Offline voice transcription is not installed. Install the "
            "application dependencies, including faster-whisper."
        ) from exc

    return WhisperModel(
        WHISPER_MODEL,
        device=os.environ.get("WHISPER_DEVICE", "cpu"),
        compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
    )


def transcribe_audio(file_path):
    model = _get_model()
    segments, info = model.transcribe(
        str(file_path),
        beam_size=5,
        vad_filter=True,
    )

    duration = getattr(info, "duration", None)
    if duration is not None and duration > MAX_AUDIO_SECONDS:
        raise ValueError(
            f"Record no more than {MAX_AUDIO_SECONDS} seconds at a time."
        )

    transcript = " ".join(
        segment.text.strip()
        for segment in segments
        if segment.text.strip()
    ).strip()
    if not transcript:
        raise ValueError(
            "No speech was recognized. Try speaking more clearly and closer "
            "to the microphone."
        )

    return {
        "text": transcript,
        "language": getattr(info, "language", None),
    }
