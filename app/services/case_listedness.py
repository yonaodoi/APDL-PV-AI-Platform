"""Automatic listedness check run as part of case processing.

When a case is saved or its events are coded, every reported event is
checked against the current reference safety information for the suspect
product. The result is saved as an *automatic* assessment, which a reviewer
then confirms. A reviewer-confirmed assessment is never overwritten.

Order of sources:
1. Uploaded current reference document for the product (innovator RSI
   first), using its extracted reaction terms. Product names are matched
   ignoring strength and dosage form ("ABPARA 500mg tablets" = "ABPARA"),
   or by active substance. A document for the case's country is preferred;
   otherwise one for any market is used and the result says so.
   If the chosen document has no reaction terms yet, they are extracted
   from its file automatically first.
2. Only if switched on (RSI_ONLINE_FALLBACK), the online DailyMed label (US),
   marked provisional.
3. Otherwise the event is recorded as not assessable, with the reason.
"""

import re

from flask import current_app

from app.db import get_db, query_all, query_one, transaction
from app.services.rsi_assessment import (
    assess_event_against_rsi,
    choose_rsi_document,
    rsi_document_title,
)

ONLINE_TIMEOUT_SECONDS = 8

LISTED = "Listed"
NOT_LISTED = "Not listed"
INSUFFICIENT = "Insufficient information"


# --------------------------------------------------------------------------
# Pure helpers (tested without a database)
# --------------------------------------------------------------------------

def events_to_assess(case, event_terms):
    """[(label, text_to_match)] for each reported event.

    Coded events use the preferred term plus the reported wording, so either
    can match the reference document. Falls back to the event description.
    """
    events = []
    seen = set()
    for term in event_terms or []:
        verbatim = (term.get("verbatim_term") or "").strip()
        preferred = (term.get("preferred_term") or "").strip()
        label = preferred or verbatim
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        text = f"{preferred} {verbatim}".strip() if preferred else verbatim
        events.append((label, text))

    if not events and (case.get("event_description") or "").strip():
        description = case["event_description"].strip()
        events.append((description, description))
    return events


def combine_event_results(event_results):
    """Case-level outcome from per-event results.

    Any unlisted event makes the case unlisted. Otherwise, if any event could
    not be assessed, the case has insufficient information.
    """
    statuses = [r["listedness_status"] for r in event_results]
    if NOT_LISTED in statuses:
        listedness, expectedness = NOT_LISTED, "Unexpected"
    elif INSUFFICIENT in statuses or not statuses:
        listedness, expectedness = INSUFFICIENT, "Not assessable"
    else:
        listedness, expectedness = LISTED, "Expected"

    provisional = any(
        r.get("provisional") or r["listedness_status"] == INSUFFICIENT
        for r in event_results
    )
    return listedness, expectedness, provisional


def event_result_from_rsi(label, result):
    if not result["available"]:
        return {
            "event": label,
            "listedness_status": INSUFFICIENT,
            "provisional": True,
            "evidence": result["message"],
            "matches": [],
        }
    return {
        "event": label,
        "listedness_status": result["listedness_status"],
        "provisional": result["provisional"],
        "evidence": result["evidence"],
        "matches": result["matches"],
    }


def summary_evidence(source_title, event_results, provisional):
    lines = [f"Automatic check against: {source_title}."]
    if provisional:
        lines.append(
            "PROVISIONAL: a reviewer must confirm this assessment before it "
            "is relied on."
        )
    for result in event_results:
        lines.append(f"\n{result['event']}: {result['listedness_status']}")
        lines.append(result["evidence"])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Database work
# --------------------------------------------------------------------------

def _load_case(case_id):
    return query_one(
        """
        SELECT c.case_id, c.case_number, c.event_description, c.seriousness,
               c.seriousness_criteria, c.created_by, co.country_name
        FROM pv.safety_cases AS c
        LEFT JOIN pv.countries AS co ON co.country_id = c.country_id
        WHERE c.case_id = %s
        """,
        (case_id,),
    )


def _suspect_product(case_id):
    return query_one(
        """
        SELECT product_name, generic_name
        FROM pv.case_products
        WHERE case_id = %s
        ORDER BY case_product_id
        LIMIT 1
        """,
        (case_id,),
    )


# Strength, dosage-form and packaging words dropped when comparing names.
_FORM_WORDS = {
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps",
    "syrup", "suspension", "susp", "injection", "inj", "infusion", "solution",
    "oral", "cream", "ointment", "gel", "drops", "suppository", "suppositories",
    "powder", "for", "film", "coated", "fc", "dispersible", "chewable", "sr",
    "er", "xr", "mr", "forte", "plus", "iv", "im", "vial", "ampoule", "sachet",
}
_STRENGTH = re.compile(r"^\d+(\.\d+)?(mg|g|mcg|µg|ug|ml|iu|%|mg/ml|mg/5ml)?$")


def core_name(value):
    """'ABPARA 500mg Tablets' -> 'abpara'; '' if nothing is left."""
    words = re.sub(r"[^a-z0-9.%/µ]+", " ", (value or "").lower()).split()
    kept = [w for w in words if w not in _FORM_WORDS and not _STRENGTH.match(w)
            and not re.fullmatch(r"\d+(\.\d+)?", w) and w not in ("mg", "g", "ml", "mcg")]
    return " ".join(kept)


def product_matches(document, product):
    """True if a reference document is for the case's suspect product."""
    case_names = {core_name(product.get("product_name")), core_name(product.get("generic_name"))} - {""}
    if not case_names:
        return False
    doc_names = {core_name(document.get("product_name")), core_name(document.get("active_substance"))} - {""}
    if case_names & doc_names:
        return True
    # "ABPARA" on the document, "ABPARA Junior" on the case: the document's
    # whole name starts the case's name.
    for doc_name in doc_names:
        for case_name in case_names:
            if case_name.startswith(doc_name + " "):
                return True
    return False


def prefer_market(documents, country):
    """Documents for the case's country if there are any, else all.

    Returns (documents, note) where note explains a fallback.
    """
    if not documents:
        return documents, None
    country_key = (country or "").strip().casefold()
    if country_key:
        local = [d for d in documents if (d.get("market") or "").strip().casefold() == country_key]
        if local:
            return local, None
        markets = sorted({(d.get("market") or "unspecified market") for d in documents})
        return documents, (
            f"No reference document for {country}; used the one for "
            f"{', '.join(markets)}."
        )
    return documents, None


def current_documents_for_product(product, country=None):
    """Current reference documents for the suspect product (best market first).

    Returns (documents, market_note).
    """
    if not product:
        return [], None
    rows = query_all(
        """
        SELECT rsi_id, product_name, active_substance, document_type,
               document_version, effective_date, market, stored_filename,
               extraction_status
        FROM pv.reference_safety_information
        WHERE is_current = TRUE
        """
    )
    matched = [row for row in rows if product_matches(row, product)]
    return prefer_market(matched, country)


AUTO_EXTRACT_SKIP = {"Extraction failed", "No reaction terms found"}


def _extract_terms_automatically(document, actor_user_id):
    """Extract reaction terms from the document's file if it has none yet.

    Records the outcome on the document so a failure is not retried on
    every case. Returns True when terms were added.
    """
    if not document.get("stored_filename") or document.get("extraction_status") in AUTO_EXTRACT_SKIP:
        return False
    from app.rsi.routes import IMAGE_ONLY_MESSAGE, extract_terms_for_document

    count, error = extract_terms_for_document(
        document["rsi_id"], document["stored_filename"], actor_user_id,
        source_name="the document file (automatic, when a case was linked)",
    )
    if error == IMAGE_ONLY_MESSAGE:
        status = "Extraction failed"
    elif error or not count:
        status = "No reaction terms found"
    else:
        status = "Terms extracted"
    with transaction() as cursor:
        cursor.execute(
            "UPDATE pv.reference_safety_information SET extraction_status = %s WHERE rsi_id = %s",
            (status, document["rsi_id"]),
        )
    return bool(count)


def _reaction_terms(rsi_id):
    return query_all(
        """
        SELECT reaction_term, source_excerpt, review_status
        FROM pv.rsi_reactions
        WHERE rsi_id = %s
          AND review_status IN ('Proposed', 'Verified')
        """,
        (rsi_id,),
    )


def _assess_with_online_label(product, events):
    from app.services.rsi_lookup import (
        event_matches_label,
        get_dailymed_label_text,
        search_dailymed_label,
    )

    product_name = product.get("generic_name") or product.get("product_name")
    try:
        spl = search_dailymed_label(product_name, timeout=ONLINE_TIMEOUT_SECONDS)
        if not spl:
            return None, None
        set_id = spl.get("setid") or spl.get("set_id")
        label_text = get_dailymed_label_text(set_id, timeout=ONLINE_TIMEOUT_SECONDS)
    except Exception:
        current_app.logger.warning(
            "Online label lookup failed for %s", product_name, exc_info=True
        )
        return None, None

    title = f"DailyMed (US) label: {spl.get('title', product_name)}"
    results = []
    for label, text in events:
        matches = event_matches_label(text, label_text)
        results.append(
            {
                "event": label,
                "listedness_status": LISTED if matches else NOT_LISTED,
                "provisional": True,
                "evidence": (
                    "\n".join(f"Label text: {m}" for m in matches)
                    if matches
                    else "No matching text found in the label's adverse "
                    "reactions section."
                ),
                "matches": [],
            }
        )
    return title, results


def assess_case(case_id, allow_online=True):
    """Work out the automatic result for a case without saving it."""
    from app.services.event_coding import code_case_events, get_case_event_terms

    case = _load_case(case_id)
    if not case:
        return None
    product = _suspect_product(case_id) or {}

    try:
        code_case_events(case_id, case.get("event_description"))
    except Exception:
        current_app.logger.warning(
            "Event coding before listedness failed for case %s", case_id,
            exc_info=True,
        )
    events = events_to_assess(case, get_case_event_terms(case_id))
    document = None

    if not events:
        source_title = "no reported event recorded"
        event_results = []
    else:
        documents, market_note = current_documents_for_product(product, case.get("country_name"))
        document = choose_rsi_document(documents)
        if document:
            source_title = rsi_document_title(document)
            if document.get("market"):
                source_title += f" ({document['market']})"
            if market_note:
                source_title += f". {market_note}"
            terms = _reaction_terms(document["rsi_id"])
            if not terms:
                try:
                    if _extract_terms_automatically(document, case.get("created_by")):
                        terms = _reaction_terms(document["rsi_id"])
                except Exception:
                    current_app.logger.warning(
                        "Automatic term extraction failed for RSI %s",
                        document["rsi_id"], exc_info=True,
                    )
                    try:
                        get_db().rollback()
                    except Exception:
                        pass
            event_results = [
                event_result_from_rsi(
                    label, assess_event_against_rsi(document, terms, text)
                )
                for label, text in events
            ]
        else:
            source_title, event_results = (None, None)
            if allow_online and product and current_app.config.get("RSI_ONLINE_FALLBACK"):
                source_title, event_results = _assess_with_online_label(
                    product, events
                )
            if not event_results:
                product_label = (
                    product.get("product_name") or "the suspect product"
                )
                source_title = "no reference document available"
                event_results = [
                    {
                        "event": label,
                        "listedness_status": INSUFFICIENT,
                        "provisional": True,
                        "evidence": (
                            f"No current reference safety information is "
                            f"in the library for {product_label}. Add the "
                            "reference document under Reference Safety "
                            "Information; this case will then be re-checked "
                            "automatically."
                        ),
                        "matches": [],
                    }
                    for label, _ in events
                ]

    listedness, expectedness, provisional = combine_event_results(event_results)
    all_matches = [m for r in event_results for m in r.get("matches", [])]
    return {
        "case": case,
        "rsi_id": document["rsi_id"] if document else None,
        "source_title": source_title,
        "events": event_results,
        "listedness_status": listedness,
        "expectedness_status": expectedness,
        "provisional": provisional,
        "evidence": summary_evidence(source_title, event_results, provisional),
        "matches": all_matches,
    }


def existing_assessment(case_id):
    return query_one(
        "SELECT * FROM pv.case_safety_assessments WHERE case_id = %s",
        (case_id,),
    )


def is_unchanged(current, result):
    """True when an automatic assessment already says exactly this."""
    if not current or current.get("assessment_source") != "automatic":
        return False
    return (
        current.get("listedness_status") == result["listedness_status"]
        and current.get("expectedness_status") == result["expectedness_status"]
        and bool(current.get("is_provisional")) == bool(result["provisional"])
        and current.get("rsi_evidence") == result["evidence"]
        and current.get("reference_title") == result["source_title"]
        and current.get("rsi_id") == result["rsi_id"]
    )


def save_automatic_assessment(result, actor_user_id):
    from app.rsi.routes import frequency_from_rsi_matches

    case = result["case"]
    frequency, frequency_evidence = frequency_from_rsi_matches(result["matches"])
    if result["listedness_status"] != LISTED:
        frequency, frequency_evidence = (
            "Not stated",
            "Frequency applies only to listed events.",
        )
    event_term_assessed = (
        ", ".join(r["event"] for r in result["events"])
        or (case.get("event_description") or "Not recorded")
    )[:500]
    rationale = (
        "Automatic listedness check run during case processing. "
        f"Outcome: {result['listedness_status']} / "
        f"{result['expectedness_status']}. "
        + (
            "Provisional - reviewer confirmation required."
            if result["provisional"]
            else "Reviewer confirmation required."
        )
    )

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.case_safety_assessments (
                case_id, rsi_id, event_term_assessed, listedness_status,
                expectedness_status, seriousness_assessment,
                seriousness_criteria, rsi_evidence, frequency_assessment,
                frequency_evidence, assessment_rationale, assessed_by,
                assessment_source, is_provisional, reference_title,
                assessed_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'automatic', %s, %s, NOW(), NOW()
            )
            ON CONFLICT (case_id) DO UPDATE SET
                rsi_id = EXCLUDED.rsi_id,
                event_term_assessed = EXCLUDED.event_term_assessed,
                listedness_status = EXCLUDED.listedness_status,
                expectedness_status = EXCLUDED.expectedness_status,
                seriousness_assessment = EXCLUDED.seriousness_assessment,
                seriousness_criteria = EXCLUDED.seriousness_criteria,
                rsi_evidence = EXCLUDED.rsi_evidence,
                frequency_assessment = EXCLUDED.frequency_assessment,
                frequency_evidence = EXCLUDED.frequency_evidence,
                assessment_rationale = EXCLUDED.assessment_rationale,
                assessed_by = EXCLUDED.assessed_by,
                assessment_source = 'automatic',
                is_provisional = EXCLUDED.is_provisional,
                reference_title = EXCLUDED.reference_title,
                assessed_at = NOW(),
                updated_at = NOW()
            """,
            (
                case["case_id"],
                result["rsi_id"],
                event_term_assessed,
                result["listedness_status"],
                result["expectedness_status"],
                "Serious" if case.get("seriousness") else "Non-serious",
                case.get("seriousness_criteria"),
                result["evidence"],
                frequency,
                frequency_evidence,
                rationale,
                actor_user_id,
                result["provisional"],
                result["source_title"],
            ),
        )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                case["case_id"],
                "Automatic listedness check",
                (
                    f"{result['listedness_status']} / "
                    f"{result['expectedness_status']} using "
                    f"{result['source_title']}"
                    + ("; provisional." if result["provisional"] else ".")
                ),
                actor_user_id,
            ),
        )


def run_automatic_listedness(case_id, actor_user_id=None, allow_online=True, force=False):
    """Check and save listedness for a case. Never raises.

    Returns a short outcome dict for messages, or None if nothing ran.
    A reviewer-confirmed assessment is kept unless ``force`` is set.
    """
    try:
        current = existing_assessment(case_id)
        if (
            current
            and not force
            and current.get("assessment_source", "reviewer") != "automatic"
        ):
            return {"kept_reviewer": True}

        result = assess_case(case_id, allow_online=allow_online)
        if result is None:
            return None
        unchanged = is_unchanged(current, result)
        if not unchanged:
            save_automatic_assessment(result, actor_user_id)
        return {
            "kept_reviewer": False,
            "unchanged": unchanged,
            "listedness_status": result["listedness_status"],
            "expectedness_status": result["expectedness_status"],
            "provisional": result["provisional"],
            "source_title": result["source_title"],
        }
    except Exception:
        current_app.logger.exception(
            "Automatic listedness check failed for case %s", case_id
        )
        try:
            get_db().rollback()
        except Exception:
            pass
        return {"failed": True}


def listedness_message(outcome):
    """(message, category) for a flash, or None."""
    if not outcome or outcome.get("kept_reviewer"):
        return None
    if outcome.get("unchanged"):
        return None
    if outcome.get("failed"):
        return (
            "The automatic listedness check could not run. Open the "
            "Listedness panel on the case to retry.",
            "warning",
        )
    message = (
        f"Listedness checked automatically: {outcome['listedness_status']} / "
        f"{outcome['expectedness_status']} ({outcome['source_title']})."
    )
    if outcome["provisional"]:
        message += " Provisional - a reviewer must confirm it."
    return message, ("warning" if outcome["provisional"] else "success")


def reassess_product_cases(product_names, actor_user_id=None):
    """Re-run automatic (not reviewer-confirmed) checks for a product's cases
    after its reference information changes. Uses uploaded documents only."""
    names = [n for n in product_names if n]
    if not names:
        return 0
    document = {"product_name": names[0], "active_substance": names[1] if len(names) > 1 else None}
    try:
        rows = query_all(
            """
            SELECT DISTINCT ON (case_products.case_id)
                   case_products.case_id, case_products.product_name, case_products.generic_name
            FROM pv.case_products AS case_products
            LEFT JOIN pv.case_safety_assessments AS assessments
                ON assessments.case_id = case_products.case_id
            WHERE assessments.case_id IS NULL
               OR assessments.assessment_source = 'automatic'
            ORDER BY case_products.case_id, case_products.case_product_id
            """
        )
        # Same matching as the case check: strength and form are ignored.
        cases = [row for row in rows if product_matches(document, row)]
    except Exception:
        current_app.logger.exception("Could not find cases to re-check")
        try:
            get_db().rollback()
        except Exception:
            pass
        return 0

    count = 0
    for row in cases:
        outcome = run_automatic_listedness(
            row["case_id"], actor_user_id, allow_online=False
        )
        if (
            outcome
            and not outcome.get("failed")
            and not outcome.get("kept_reviewer")
            and not outcome.get("unchanged")
        ):
            count += 1
    return count
