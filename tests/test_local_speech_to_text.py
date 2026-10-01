from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import local_speech_to_text


def test_transcribe_audio_combines_segments_and_returns_detected_language(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "recording.webm"
    audio_path.write_bytes(b"audio")
    model = Mock()
    model.transcribe.return_value = (
        [
            SimpleNamespace(text=" Patient reports"),
            SimpleNamespace(text=" dizziness. "),
        ],
        SimpleNamespace(duration=8.5, language="en"),
    )
    monkeypatch.setattr(local_speech_to_text, "_get_model", lambda: model)

    result = local_speech_to_text.transcribe_audio(audio_path)

    assert result == {
        "text": "Patient reports dizziness.",
        "language": "en",
    }
    model.transcribe.assert_called_once_with(
        str(audio_path),
        beam_size=5,
        vad_filter=True,
    )


def test_transcribe_audio_rejects_recordings_over_duration_limit(
    monkeypatch,
    tmp_path,
):
    model = Mock()
    model.transcribe.return_value = (
        [],
        SimpleNamespace(
            duration=local_speech_to_text.MAX_AUDIO_SECONDS + 1,
            language="en",
        ),
    )
    monkeypatch.setattr(local_speech_to_text, "_get_model", lambda: model)

    with pytest.raises(ValueError, match="no more than"):
        local_speech_to_text.transcribe_audio(tmp_path / "recording.webm")


def test_transcribe_audio_rejects_empty_transcript(monkeypatch, tmp_path):
    model = Mock()
    model.transcribe.return_value = (
        [],
        SimpleNamespace(duration=3, language="en"),
    )
    monkeypatch.setattr(local_speech_to_text, "_get_model", lambda: model)

    with pytest.raises(ValueError, match="No speech was recognized"):
        local_speech_to_text.transcribe_audio(tmp_path / "recording.webm")
