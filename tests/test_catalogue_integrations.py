import io
import zipfile
from decimal import Decimal

import pytest

from app.services import product_catalogue as catalogue
from app.services import secrets_store
from app.services.tabular_import import ImportFileError, read_table


def _app():
    from app import create_app
    from config import TestingConfig

    return create_app(TestingConfig)


# --------------------------------------------------------------- secrets

def test_credentials_round_trip_without_the_cryptography_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_cryptography(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    with _app().app_context():
        monkeypatch.setattr(builtins, "__import__", no_cryptography)
        token = secrets_store.encrypt("sk-live-ABCDEFGH1234")
        assert token.startswith("h1:") and "ABCDEFGH" not in token
        assert secrets_store.decrypt(token) == "sk-live-ABCDEFGH1234"
        tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        with pytest.raises(secrets_store.SecretError):
            secrets_store.decrypt(tampered)
    assert secrets_store.hint("sk-live-ABCDEFGH1234") == "••••1234"
    assert secrets_store.redact("failed for key sk-live-ABCDEFGH1234", ["sk-live-ABCDEFGH1234"]) == "failed for key [credential]"


def test_a_changed_key_cannot_read_old_credentials(monkeypatch):
    app = _app()
    with app.app_context():
        token = secrets_store.encrypt("secret-value-9876")
        monkeypatch.setenv("PV_SECRETS_KEY", "a-different-key")
        with pytest.raises(secrets_store.SecretError):
            secrets_store.decrypt(token)


# --------------------------------------------------------------- files

def _xlsx(rows):
    """A minimal .xlsx built with the standard library."""
    def cell(ref, value):
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    sheet_rows = []
    for r, row in enumerate(rows, 1):
        cells = "".join(cell(f"{chr(65 + c)}{r}", v) for c, v in enumerate(row) if v is not None)
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             + "".join(sheet_rows) + "</sheetData></worksheet>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


def test_excel_and_csv_are_read_the_same_way():
    data = _xlsx([["Product ID", "Brand", "Strength"], ["AB-001", "ABPARA", "1 g"], [None, None, None], ["AB-002", "AMOXIL", 500]])
    headers, rows = read_table("products.xlsx", data)
    assert headers == ["product_id", "brand", "strength"]
    assert [(r["product_id"], r["brand"], r["strength"]) for r in rows] == [("AB-001", "ABPARA", "1 g"), ("AB-002", "AMOXIL", "500")]
    headers, rows = read_table("products.csv", "Product ID;Brand\r\nAB-001;ABPARA\r\n".encode("utf-8-sig"))
    assert rows[0]["product_id"] == "AB-001" and rows[0]["brand"] == "ABPARA"
    with pytest.raises(ImportFileError):
        read_table("old.xls", b"x")


# --------------------------------------------------------------- catalogue

def test_import_preview_flags_errors_duplicates_and_changes():
    existing = [
        {"product_id": 1, "product_code": "AB-001", "brand_name": "ABPARA", "generic_name": "Paracetamol", "strength": "1 g/100 mL",
         "dosage_form": "Solution for infusion", "routes": None, "manufacturer": None, "mah": None,
         "registration_number": "NDA/12/34", "market": "Uganda", "status": "Active", "notes": None},
    ]
    headers = ["product_id", "brand", "active_ingredients", "strength", "form", "registration_no", "country", "status"]
    rows = [
        {"__line": 2, "product_id": "ab-001", "brand": "ABPARA", "active_ingredients": "Paracetamol", "strength": "10 mg/mL",
         "form": "Solution for infusion", "registration_no": "NDA/12/34", "country": "Uganda", "status": "Active"},
        {"__line": 3, "product_id": "AB-009", "brand": "ABPARA", "active_ingredients": "Paracetamol", "strength": "1 g/100 mL",
         "form": "Solution for infusion", "registration_no": "NDA/12/34", "country": "Uganda", "status": "current"},
        {"__line": 4, "product_id": "", "brand": "Nameless", "status": "Active"},
        {"__line": 5, "product_id": "AB-009", "brand": "Again", "status": "Active"},
        {"__line": 6, "product_id": "AB-010", "brand": "New one", "status": "Active"},
    ]
    preview, summary = catalogue.build_preview(headers, rows, existing)
    actions = {p["line"]: p["action"] for p in preview}
    assert actions == {2: "update", 3: "review", 4: "error", 5: "error", 6: "create"}
    assert any("Strength" in d for d in preview[0]["differences"])
    assert preview[1]["product"]["status"] == "Active"                      # "current" understood
    assert "same registration number and market" in preview[1]["similar"][0]["reasons"]
    assert summary["mapping"]["product_code"] == "product_id" and summary["missing_columns"] == []


def test_products_are_tidied_and_checked():
    product, problems = catalogue.clean_product({"product_code": " ab-1 ", "brand_name": "  ABPARA  ", "status": "Banana"})
    assert product["product_code"] == "AB-1" and product["brand_name"] == "ABPARA"
    assert any("Unknown status" in p for p in problems)
    _, problems = catalogue.clean_product({"brand_name": "X"})
    assert any("Product identifier is required" in p for p in problems)
    assert catalogue.picker_label({"brand_name": "ABPARA", "strength": "1 g", "dosage_form": "Infusion", "market": "Uganda",
                                   "product_code": "AB-1", "status": "Suspended"}) == "ABPARA 1 g Infusion (Uganda · AB-1) – Suspended"


def test_only_managers_change_the_catalogue(monkeypatch):
    monkeypatch.setattr("app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0)
    client = _app().test_client()
    with client.session_transaction() as s:
        s["user_id"], s["full_name"], s["role"] = 3, "Officer", "PV Officer"
    assert client.get("/products/new").status_code in (302, 403)
    assert client.post("/products/import", data={}).status_code in (302, 403)
    assert client.get("/administration/integrations/").status_code in (302, 403)


# --------------------------------------------------------------- AI calls

def _config(**extra):
    base = {"provider": "anthropic", "model": "m", "endpoint": "https://example.invalid", "api_key": "sk-1234567890",
            "timeout": 5, "retries": 2, "monthly_limit": None, "price_in": Decimal("3"), "price_out": Decimal("15"), "currency": "USD"}
    base.update(extra)
    return base


def test_temporary_failures_are_retried_and_usage_recorded(monkeypatch):
    from app.services import llm

    calls, usage = [], []
    monkeypatch.setattr(llm.time if hasattr(llm, "time") else __import__("time"), "sleep", lambda s: None)

    def flaky(prompt, system, max_tokens, temperature, json_output, timeout, config):
        calls.append(1)
        if len(calls) < 3:
            raise llm.AITransientError("Claude API error 529: overloaded")
        return "OK", {"input_tokens": 1000, "output_tokens": 200}

    monkeypatch.setattr(llm, "_anthropic", flaky)
    monkeypatch.setattr("app.services.integrations.record_ai_usage", lambda *a, **k: usage.append(a))
    with _app().app_context():
        assert llm.generate_text("hi", config=_config()) == "OK"
    assert len(calls) == 3
    statuses = [u[7] for u in usage]
    assert statuses == ["failed", "failed", "ok"]
    assert usage[-1][5] == Decimal("0.006")          # 1000×3 + 200×15 per million


def test_spending_limit_stops_calls_but_says_why(monkeypatch):
    from app.services import llm

    monkeypatch.setattr("app.services.integrations.month_spend", lambda: (Decimal("12.50"), 40))
    monkeypatch.setattr(llm, "_anthropic", lambda *a, **k: pytest.fail("must not call the API"))
    with _app().app_context():
        try:
            llm.generate_text("hi", config=_config(monthly_limit=Decimal("10")))
        except llm.AIUnavailableError as error:
            message = str(error)
        else:
            raise AssertionError("the limit was not applied")
    assert "monthly AI spending limit" in message and "keeps working" in message


def test_documents_go_to_the_ai_service_only_when_allowed(monkeypatch):
    from app.services import llm

    monkeypatch.setattr("app.services.integrations.active_ai_config", lambda: _config(provider="anthropic", use_for_documents=False))
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("AI_EXTRACTION_PROVIDER", raising=False)
    assert llm.effective_config(llm.PURPOSE_ASSESSMENT)["provider"] == "anthropic"
    assert llm.effective_config(llm.PURPOSE_EXTRACTION)["source"] == "environment"
    monkeypatch.setattr("app.services.integrations.active_ai_config", lambda: _config(provider="anthropic", use_for_documents=True))
    assert llm.effective_config(llm.PURPOSE_EXTRACTION)["provider"] == "anthropic"
