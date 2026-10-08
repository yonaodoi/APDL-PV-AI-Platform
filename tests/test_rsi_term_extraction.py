from pathlib import Path

import app.rsi.routes as rsi_routes
from app import create_app
from config import TestingConfig


def _run(monkeypatch, text):
    monkeypatch.setattr(rsi_routes, "rsi_file_path", lambda name: Path(__file__))
    monkeypatch.setattr(rsi_routes, "extract_document_text", lambda path: text)
    app = create_app(TestingConfig)
    with app.app_context():
        return rsi_routes.extract_terms_for_document(1, "file.pdf", 1)


def test_image_only_pdf_gets_a_clear_message(monkeypatch):
    count, message = _run(monkeypatch, "  \n\f  ")

    assert count == 0
    assert message == rsi_routes.IMAGE_ONLY_MESSAGE


def test_readable_text_without_heading_still_says_no_heading(monkeypatch):
    count, message = _run(monkeypatch, "Paracetamol solution for infusion. " * 20)

    assert count == 0
    assert "No adverse-reactions or undesirable-effects heading" in message
