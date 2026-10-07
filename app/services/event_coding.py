"""Coding of reported (verbatim) adverse event terms to preferred terms.

Each case's event description is split into individual verbatim terms, and
each one is coded against the controlled dictionary in pv.event_terms:

* Exact     - the verbatim matches a preferred term.
* Synonym   - the verbatim matches a recorded synonym.
* Suggested - a close spelling match; a reviewer must confirm it.
* Uncoded   - no match; a reviewer must code it.
* Manual    - set by a reviewer; never overwritten automatically.
"""

import re
from difflib import get_close_matches

from app.db import query_all, query_one, transaction


SUGGESTION_CUTOFF = 0.85

HARD_SPLIT_PATTERN = re.compile(r"[,;/\n+]")
SOFT_SPLIT_PATTERN = re.compile(r"\band\b|\bwith\b|&", re.I)

# Placeholder text that is not an event.
IGNORED_TERMS = {
    "n a", "na", "nil", "none", "unknown", "not applicable",
    "not known", "not stated", "not recorded", "no", "other",
}

REVIEW_METHODS = {"Suggested", "Uncoded"}


def normalise_term(value):
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def split_verbatim_terms(event_description, known_terms=()):
    """Split free text into the reporter's individual terms, as written.

    Text is split on punctuation, then on "and"/"with" unless the whole
    phrase is a known dictionary term (e.g. "pins and needles").
    """
    pieces = []
    for part in HARD_SPLIT_PATTERN.split(event_description or ""):
        if normalise_term(part) in known_terms:
            pieces.append(part)
        else:
            pieces.extend(SOFT_SPLIT_PATTERN.split(part))

    verbatims = []
    seen = set()
    for piece in pieces:
        verbatim = re.sub(r"\s+", " ", piece).strip(" .-:")
        key = normalise_term(verbatim)
        if len(key) < 3 or key in IGNORED_TERMS or key in seen:
            continue
        seen.add(key)
        verbatims.append(verbatim)
    return verbatims


def known_terms(dictionary):
    return set(dictionary["preferred"]) | set(dictionary["synonyms"])


def build_dictionary(terms, synonyms):
    """Index dictionary rows for coding.

    ``terms``: rows with term_id, preferred_term, system_organ_class.
    ``synonyms``: rows with synonym, term_id.
    """
    by_id = {term["term_id"]: dict(term) for term in terms}
    preferred = {
        normalise_term(term["preferred_term"]): term for term in by_id.values()
    }
    synonym_index = {
        normalise_term(row["synonym"]): by_id[row["term_id"]]
        for row in synonyms
        if row["term_id"] in by_id
    }
    return {"by_id": by_id, "preferred": preferred, "synonyms": synonym_index}


def code_verbatim(verbatim, dictionary):
    """Return (term or None, coding method) for one verbatim term."""
    key = normalise_term(verbatim)
    preferred = dictionary["preferred"]
    synonyms = dictionary["synonyms"]

    if key in preferred:
        return preferred[key], "Exact"
    if key in synonyms:
        return synonyms[key], "Synonym"
    if key.endswith("s") and key[:-1] in preferred:
        return preferred[key[:-1]], "Synonym"
    if key.endswith("s") and key[:-1] in synonyms:
        return synonyms[key[:-1]], "Synonym"

    candidates = list(preferred) + list(synonyms)
    match = get_close_matches(key, candidates, n=1, cutoff=SUGGESTION_CUTOFF)
    if match:
        term = preferred.get(match[0]) or synonyms[match[0]]
        return term, "Suggested"
    return None, "Uncoded"


def load_dictionary():
    terms = query_all(
        """
        SELECT term_id, preferred_term, system_organ_class
        FROM pv.event_terms
        WHERE is_active = TRUE
        ORDER BY system_organ_class, preferred_term
        """
    )
    synonyms = query_all(
        "SELECT synonym, term_id FROM pv.event_term_synonyms"
    )
    return build_dictionary(terms, synonyms)


def code_case_events(case_id, event_description, dictionary=None, actor_user_id=None):
    """Bring a case's coded terms in line with its event description.

    Reviewer (Manual) codings are kept. Rows for wording that is no longer in
    the description are removed.
    """
    dictionary = dictionary or load_dictionary()
    verbatims = split_verbatim_terms(event_description, known_terms(dictionary))

    with transaction() as cursor:
        cursor.execute(
            """
            SELECT case_event_term_id, verbatim_term, term_id, coding_method
            FROM pv.case_event_terms
            WHERE case_id = %s
            """,
            (case_id,),
        )
        existing = {row["verbatim_term"]: row for row in cursor.fetchall()}

        for verbatim in verbatims:
            term, method = code_verbatim(verbatim, dictionary)
            term_id = term["term_id"] if term else None
            row = existing.get(verbatim)

            if row is None:
                cursor.execute(
                    """
                    INSERT INTO pv.case_event_terms (
                        case_id, verbatim_term, term_id, coding_method, coded_by
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (case_id, verbatim, term_id, method, actor_user_id),
                )
            elif row["coding_method"] != "Manual" and (
                row["term_id"] != term_id or row["coding_method"] != method
            ):
                cursor.execute(
                    """
                    UPDATE pv.case_event_terms
                    SET term_id = %s,
                        coding_method = %s,
                        coded_by = %s,
                        coded_at = CURRENT_TIMESTAMP
                    WHERE case_event_term_id = %s
                    """,
                    (term_id, method, actor_user_id, row["case_event_term_id"]),
                )

        removed = [
            row["case_event_term_id"]
            for verbatim, row in existing.items()
            if verbatim not in verbatims
        ]
        if removed:
            cursor.execute(
                "DELETE FROM pv.case_event_terms WHERE case_event_term_id = ANY(%s)",
                (removed,),
            )


def get_case_event_terms(case_id):
    return query_all(
        """
        SELECT
            case_event_terms.case_event_term_id,
            case_event_terms.verbatim_term,
            case_event_terms.term_id,
            case_event_terms.coding_method,
            case_event_terms.coded_at,
            event_terms.preferred_term,
            event_terms.system_organ_class,
            users.full_name AS coded_by_name
        FROM pv.case_event_terms AS case_event_terms
        LEFT JOIN pv.event_terms AS event_terms
            ON event_terms.term_id = case_event_terms.term_id
        LEFT JOIN pv.users AS users
            ON users.user_id = case_event_terms.coded_by
        WHERE case_event_terms.case_id = %s
        ORDER BY case_event_terms.case_event_term_id
        """,
        (case_id,),
    )


def get_active_terms():
    return query_all(
        """
        SELECT term_id, preferred_term, system_organ_class
        FROM pv.event_terms
        WHERE is_active = TRUE
        ORDER BY system_organ_class, preferred_term
        """
    )


def preferred_terms_by_case(case_ids):
    """Map case_id -> list of coded preferred terms."""
    if not case_ids:
        return {}
    rows = query_all(
        """
        SELECT case_event_terms.case_id, event_terms.preferred_term
        FROM pv.case_event_terms AS case_event_terms
        INNER JOIN pv.event_terms AS event_terms
            ON event_terms.term_id = case_event_terms.term_id
        WHERE case_event_terms.case_id = ANY(%s)
        ORDER BY case_event_terms.case_event_term_id
        """,
        (list(case_ids),),
    )
    result = {}
    for row in rows:
        terms = result.setdefault(row["case_id"], [])
        if row["preferred_term"] not in terms:
            terms.append(row["preferred_term"])
    return result


def ensure_cases_coded(cases, dictionary=None):
    """Code any case that has no coded-term rows yet (older cases)."""
    if not cases:
        return
    coded_ids = {
        row["case_id"]
        for row in query_all(
            "SELECT DISTINCT case_id FROM pv.case_event_terms WHERE case_id = ANY(%s)",
            ([case["case_id"] for case in cases],),
        )
    }
    pending = [case for case in cases if case["case_id"] not in coded_ids]
    if not pending:
        return
    dictionary = dictionary or load_dictionary()
    for case in pending:
        code_case_events(case["case_id"], case.get("event_description"), dictionary)


def set_manual_coding(case_id, case_event_term_id, term_id, actor_user_id, save_synonym=False):
    """Record a reviewer's coding. Returns (row, synonym_saved) or (None, False)."""
    row = query_one(
        """
        SELECT case_event_term_id, verbatim_term
        FROM pv.case_event_terms
        WHERE case_event_term_id = %s AND case_id = %s
        """,
        (case_event_term_id, case_id),
    )
    term = query_one(
        "SELECT term_id, preferred_term FROM pv.event_terms WHERE term_id = %s",
        (term_id,),
    )
    if row is None or term is None:
        return None, False

    synonym_saved = False
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.case_event_terms
            SET term_id = %s,
                coding_method = 'Manual',
                coded_by = %s,
                coded_at = CURRENT_TIMESTAMP
            WHERE case_event_term_id = %s
            """,
            (term_id, actor_user_id, case_event_term_id),
        )
        synonym = normalise_term(row["verbatim_term"])
        if (
            save_synonym
            and synonym
            and synonym != normalise_term(term["preferred_term"])
        ):
            cursor.execute(
                """
                INSERT INTO pv.event_term_synonyms (synonym, term_id, added_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (synonym) DO NOTHING
                """,
                (synonym, term_id, actor_user_id),
            )
            synonym_saved = cursor.rowcount == 1
    return {**row, "preferred_term": term["preferred_term"]}, synonym_saved
