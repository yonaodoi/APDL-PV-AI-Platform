"""Which reference safety document is in force.

Only one document may be current for each product, document type and
market. Making a document current marks the others in that group as
historic (superseded).
"""


def rsi_group_key(document):
    return (
        " ".join((document.get("product_name") or "").lower().split()),
        (document.get("document_type") or "").strip().lower(),
        " ".join((document.get("market") or "").lower().split()),
    )


def rsi_group_label(document):
    parts = [document.get("product_name") or "Unnamed product"]
    if document.get("document_type"):
        parts.append(document["document_type"])
    parts.append(document.get("market") or "no market recorded")
    return " · ".join(parts)


def duplicate_current_groups(documents):
    """Groups that have more than one current document, for a warning."""
    groups = {}
    for document in documents:
        if document.get("is_current"):
            groups.setdefault(rsi_group_key(document), []).append(document)
    return [
        {"label": rsi_group_label(items[0]), "documents": items}
        for items in groups.values()
        if len(items) > 1
    ]


SAME_GROUP_SQL = """
    LOWER(TRIM(product_name)) = LOWER(TRIM(%s))
    AND LOWER(TRIM(document_type)) = LOWER(TRIM(%s))
    AND LOWER(TRIM(COALESCE(market, ''))) = LOWER(TRIM(COALESCE(%s, '')))
"""


def make_current(cursor, document):
    """Mark ``document`` current and supersede the rest of its group.

    Returns the number of other documents that were superseded.
    """
    cursor.execute(
        f"""
        UPDATE pv.reference_safety_information
        SET is_current = FALSE
        WHERE rsi_id <> %s
          AND is_current = TRUE
          AND {SAME_GROUP_SQL}
        """,
        (
            document["rsi_id"],
            document["product_name"],
            document["document_type"],
            document.get("market"),
        ),
    )
    superseded = cursor.rowcount
    cursor.execute(
        """
        UPDATE pv.reference_safety_information
        SET is_current = TRUE
        WHERE rsi_id = %s
        """,
        (document["rsi_id"],),
    )
    return superseded
