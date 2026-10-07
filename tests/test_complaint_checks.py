from datetime import date

from app.services.complaint_checks import (
    batch_key,
    batch_trends,
    complaint_date_problems,
)


TODAY = date(2026, 10, 7)


def problems(**values):
    return complaint_date_problems(values, today=TODAY)


def test_valid_dates_have_no_problems():
    assert problems(
        date_received=date(2026, 9, 16),
        manufacturing_date=date(2026, 1, 1),
        expiry_date=date(2028, 1, 1),
    ) == ([], [])


def test_impossible_dates_are_errors():
    errors, _ = problems(
        date_received=date(2026, 12, 1),
        manufacturing_date=date(2027, 1, 1),
        expiry_date=date(2026, 6, 1),
    )

    assert "The date received cannot be in the future." in errors
    assert "The manufacturing date cannot be in the future." in errors
    assert "The expiry date must be after the manufacturing date." in errors


def test_received_before_manufacture_is_error():
    errors, _ = problems(
        date_received=date(2025, 12, 1),
        manufacturing_date=date(2026, 1, 1),
    )

    assert any("before the batch was manufactured" in e for e in errors)


def test_expired_product_is_warning_not_error():
    errors, warnings = problems(
        date_received=date(2026, 9, 16),
        expiry_date=date(2026, 8, 31),
    )

    assert errors == []
    assert "expired (31 Aug 2026)" in warnings[0]


def test_batch_key_ignores_case_and_spaces():
    assert batch_key("ABPARA", " ab 55-66 ") == ("abpara", "AB55-66")
    assert batch_key("ABPARA", "  ") is None


def test_batch_trends_group_same_product_and_batch():
    complaints = [
        {"complaint_id": 1, "product_name": "ABPARA", "batch_number": "5566"},
        {"complaint_id": 2, "product_name": "abpara", "batch_number": " 5566"},
        {"complaint_id": 3, "product_name": "ABPARA", "batch_number": "7788"},
        {"complaint_id": 4, "product_name": "OTHER", "batch_number": "5566"},
        {"complaint_id": 5, "product_name": "ABPARA", "batch_number": None},
    ]

    trends = batch_trends(complaints)

    assert list(trends) == [("abpara", "5566")]
    assert [c["complaint_id"] for c in trends[("abpara", "5566")]] == [1, 2]
