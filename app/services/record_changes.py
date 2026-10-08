"""Plain-language descriptions of edits, for the audit trail."""

from datetime import date


def _shown(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return "empty"
    if isinstance(value, date):
        return f"{value:%d %b %Y}"
    text = " ".join(str(value).split())
    return f'"{text[:120]}{"…" if len(text) > 120 else ""}"'


def _same(a, b):
    def norm(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return " ".join(value.split())
        return value

    return norm(a) == norm(b)


def describe_changes(before, after, labels):
    """'Label: old → new; …' for each field in ``labels`` that changed.

    ``labels`` maps field name -> human label, in display order.
    Returns an empty string when nothing changed.
    """
    parts = [
        f"{label}: {_shown(before.get(field))} → {_shown(after.get(field))}"
        for field, label in labels.items()
        if not _same(before.get(field), after.get(field))
    ]
    return "; ".join(parts)
