from datetime import date

import app.rsi.routes as rsi_routes

DOCS = [
    {"rsi_id": 1, "product_name": "ABPARA", "active_substance": "Paracetamol", "document_type": "SmPC",
     "market": "Uganda", "is_current": True, "verified_terms": 0, "proposed_terms": 0,
     "document_version": "12", "original_filename": "abpara.pdf", "reference_product_name": None},
    {"rsi_id": 2, "product_name": "ABPARA", "active_substance": "Paracetamol", "document_type": "SmPC",
     "market": "Uganda", "is_current": False, "verified_terms": 14, "proposed_terms": 0,
     "document_version": "11", "original_filename": "old.pdf", "reference_product_name": None},
    {"rsi_id": 3, "product_name": "AMOXIL", "active_substance": "Amoxicillin",
     "document_type": "Innovator Reference Safety Information", "market": "Kenya", "is_current": True,
     "verified_terms": 5, "proposed_terms": 3, "document_version": "4", "original_filename": "amox.docx",
     "reference_product_name": "Amoxil"},
]


def ids(**filters):
    return [d["rsi_id"] for d in rsi_routes.filter_rsi_documents(DOCS, filters)]


def test_library_filters():
    assert ids() == [1, 2, 3]
    assert ids(q="amoxicillin") == [3]
    assert ids(q="old.pdf") == [2]
    assert ids(product="ABPARA") == [1, 2]
    assert ids(doc_type="SmPC", status="current") == [1]
    assert ids(status="historic") == [2]
    assert ids(market="Kenya") == [3]
    assert ids(terms="none") == [1]
    assert ids(terms="to_check") == [3]
    assert ids(terms="verified") == [2, 3]


def test_products_on_open_cases_without_a_current_document(monkeypatch):
    monkeypatch.setattr(rsi_routes, "query_all", lambda sql, parameters=(): [
        {"product_name": "ABPARA 500mg", "generic_name": "Paracetamol", "cases": 2},
        {"product_name": "CIPRO-APDL", "generic_name": "Ciprofloxacin", "cases": 1},
        {"product_name": "Amoxil", "generic_name": None, "cases": 1},
        {"product_name": "AMOXIL", "generic_name": None, "cases": 4},
    ])
    missing = rsi_routes._products_without_reference(DOCS)
    assert [m["product_name"] for m in missing] == ["CIPRO-APDL"]
