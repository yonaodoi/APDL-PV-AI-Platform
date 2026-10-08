"""Assess a reported event against uploaded reference safety information.

Rules:
* Use the most appropriate current document: innovator / reference safety
  information first, then an SmPC or label, then anything else, and the
  APDL local product information last.
* Never conclude "Not listed" from an empty term list. If the chosen
  document has no extracted reaction terms, report that the event cannot
  be assessed yet.
* A result that relies on terms not yet verified by a reviewer is marked
  provisional.
"""

import re


INNOVATOR_WORDS = ("innovator", "reference safety", "core safety", "ccsi", "rsi")
LABEL_WORDS = ("smpc", "summary of product characteristics", "label", "prescribing information")
APDL_WORDS = ("apdl", "local product information")


def rsi_document_rank(document):
    """Lower is preferred for listedness assessment."""
    document_type = (document.get("document_type") or "").lower()
    if any(word in document_type for word in APDL_WORDS):
        return 3
    if any(word in document_type for word in INNOVATOR_WORDS):
        return 0
    if any(word in document_type for word in LABEL_WORDS):
        return 1
    return 2


def _newest_first_key(document):
    effective = document.get("effective_date")
    return (
        effective is not None,
        effective.toordinal() if effective else 0,
        document.get("rsi_id") or 0,
    )


def choose_rsi_document(documents):
    """The current document to assess against, or None."""
    if not documents:
        return None
    best_rank = min(rsi_document_rank(d) for d in documents)
    candidates = [d for d in documents if rsi_document_rank(d) == best_rank]
    return max(candidates, key=_newest_first_key)


def is_apdl_document(document):
    return rsi_document_rank(document) == 3


def rsi_document_title(document):
    title = (
        f"{document.get('product_name') or 'Product'} — "
        f"{document.get('document_type') or 'Reference Safety Information'}"
    )
    if document.get("document_version"):
        title += f" version {document['document_version']}"
    if document.get("effective_date"):
        title += f", effective {document['effective_date']:%d %b %Y}"
    return title


def normalise_rsi_text(value):
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split()
    )


def matching_terms(event_term, reactions):
    event = normalise_rsi_text(event_term)
    event_words = set(event.split())
    matches = []
    for reaction in reactions:
        term = normalise_rsi_text(reaction.get("reaction_term"))
        if not term:
            continue
        term_words = set(term.split())
        if (
            term in event
            or (event and event in term)
            or (
                len(term_words) >= 2
                and len(event_words & term_words) >= min(2, len(term_words))
            )
        ):
            matches.append(reaction)
    return matches


def assess_event_against_rsi(document, reactions, event_term):
    """Return the assessment result for one document and its terms."""
    title = rsi_document_title(document)

    if not reactions:
        return {
            "available": False,
            "message": (
                f"Cannot assess listedness yet: no reaction terms have been "
                f"extracted from {title}. Open Reference Safety Information, "
                "click Extract terms on this document and verify them, then "
                "run the assessment again."
            ),
        }

    verified = [r for r in reactions if r.get("review_status") == "Verified"]
    unverified = len(reactions) - len(verified)
    provisional = unverified > 0
    matches = matching_terms(event_term, reactions)

    lines = [
        f"Assessed against: {title}.",
        f"Reaction terms used: {len(verified)} verified, {unverified} not yet verified.",
    ]
    if provisional:
        lines.append(
            "PROVISIONAL: some reaction terms have not been verified by a "
            "reviewer, so this result must be confirmed before it is relied on."
        )
    if matches:
        lines.extend(
            f"Matched term: {m['reaction_term']} ({m.get('review_status') or 'status unknown'})\n"
            f"{m.get('source_excerpt') or 'No source excerpt available.'}"
            for m in matches[:3]
        )
    else:
        lines.append(
            "No reaction term in this document matched the reported event."
        )

    return {
        "available": True,
        "rsi_id": document.get("rsi_id"),
        "source": "Uploaded reference safety information",
        "title": title,
        "listedness_status": "Listed" if matches else "Not listed",
        "expectedness_status": "Expected" if matches else "Unexpected",
        "evidence": "\n\n".join(lines),
        "matches": matches,
        "provisional": provisional,
    }
