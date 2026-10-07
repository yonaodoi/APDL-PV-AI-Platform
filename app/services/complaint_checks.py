"""Date checks and batch trending for product complaints."""

from datetime import date

BATCH_TREND_THRESHOLD = 2


def complaint_date_problems(values, today=None):
    """Return (errors, warnings) for a complaint's dates.

    Errors are impossible combinations that block saving. Warnings are
    plausible but worth a reviewer's attention.
    """
    today = today or date.today()
    received = values.get("date_received")
    manufactured = values.get("manufacturing_date")
    expiry = values.get("expiry_date")
    errors, warnings = [], []

    if received and received > today:
        errors.append("The date received cannot be in the future.")
    if manufactured and manufactured > today:
        errors.append("The manufacturing date cannot be in the future.")
    if manufactured and expiry and expiry <= manufactured:
        errors.append("The expiry date must be after the manufacturing date.")
    if manufactured and received and received < manufactured:
        errors.append(
            "The complaint cannot be received before the batch was "
            "manufactured."
        )

    if expiry and received and expiry < received:
        warnings.append(
            f"The product had expired ({expiry:%d %b %Y}) before the "
            f"complaint was received ({received:%d %b %Y}). Confirm whether "
            "expired stock was supplied or used."
        )
    return errors, warnings


def normalise_batch(value):
    return "".join((value or "").upper().split())


def batch_key(product_name, batch_number):
    batch = normalise_batch(batch_number)
    if not batch:
        return None
    return (" ".join((product_name or "").lower().split()), batch)


def batch_trends(complaints, threshold=BATCH_TREND_THRESHOLD):
    """Map (product, batch) to the complaints sharing it, when at or above
    the threshold."""
    groups = {}
    for complaint in complaints:
        key = batch_key(complaint.get("product_name"), complaint.get("batch_number"))
        if key:
            groups.setdefault(key, []).append(complaint)
    return {key: items for key, items in groups.items() if len(items) >= threshold}
