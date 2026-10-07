from datetime import date

from app.services.complaint_rules import (
    describe_complaint_changes,
    validate_complaint_update,
)


TODAY = date(2026, 10, 7)
COMPLAINT = {"date_received": date(2026, 9, 16), "severity": "Non-serious"}


def errors(status="Investigation complete", summary="Root cause found.",
           capa="", closure=date(2026, 10, 1), complaint=COMPLAINT):
    return validate_complaint_update(
        complaint, status, summary, capa, closure, today=TODAY
    )


def test_complete_with_summary_and_closure_date_is_allowed():
    assert errors() == []


def test_complete_requires_summary_and_closure_date():
    result = errors(summary="  ", closure=None)

    assert any("investigation summary" in e for e in result)
    assert any("closure date" in e for e in result)


def test_closure_date_cannot_precede_receipt_or_be_in_future():
    assert any("before the complaint was received" in e
               for e in errors(closure=date(2026, 9, 1)))
    assert any("in the future" in e for e in errors(closure=date(2026, 10, 8)))


def test_serious_complaint_needs_capa_to_close():
    serious = {**COMPLAINT, "severity": "Serious"}

    assert any("CAPA" in e for e in errors(complaint=serious))
    assert errors(complaint=serious, capa="Batch quarantined.") == []


def test_open_investigation_cannot_have_closure_date():
    result = errors(status="Under investigation", summary="", closure=date(2026, 10, 1))

    assert result == ["Remove the closure date, or mark the investigation complete."]


def test_change_description_keeps_previous_text():
    text = describe_complaint_changes(
        {
            "status": "Under investigation",
            "investigation_summary": "Old finding.",
            "corrective_action": None,
            "closure_date": None,
        },
        {
            "status": "Investigation complete",
            "investigation_summary": "New finding.",
            "corrective_action": "Retrain staff.",
            "closure_date": date(2026, 10, 1),
        },
    )

    assert "Status changed from Under investigation to Investigation complete." in text
    assert "Closure date set to 01 Oct 2026." in text
    assert 'Investigation summary edited (previous: "Old finding.")' in text
    assert "Corrective action / CAPA added." in text


def test_no_changes_is_reported():
    same = {"status": "Under investigation"}

    assert describe_complaint_changes(same, same) == "Saved with no changes."
