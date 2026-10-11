"""Coding of the reported items on a case.

Each reported item (reaction, drug, indication, medical history, test, past
drug) keeps its reported wording; the coding is stored beside it in
pv.term_codings with the dictionary, code, term, PT/SOC (MedDRA), version,
method, status and the candidates that were considered.

Order of work for each item:
  1. exact match in the official dictionary (MedDRA LLT / WHODrug name),
  2. wording a reviewer confirmed before ("remember this wording"),
  3. dictionary search (ranked candidates),
  4. optional AI suggestions – only names that exist in the imported
     dictionary are offered, so an AI answer can never become an official
     code by itself,
  5. when the official dictionary is not available or nothing suitable is
     found: the local terminology (LOCAL-E / LOCAL-P identifiers), proposing
     a new local term for review if needed.

Automatic acceptance follows the rules in Administration › Coding rules;
everything else waits for a person in the coding step.
"""

import json
import re

from app.db import query_all, query_one, transaction
from app.services import terminology

RULES_KEY = "coding_rules"
DEFAULT_RULES = {
    "auto_accept_exact": True,       # exact official match → Coded
    "auto_accept_synonym": True,     # wording a reviewer confirmed before → Coded
    "auto_accept_local": True,       # exact approved local term → Coded (local)
    "ai_suggestions": False,         # ask the AI for candidate names (verified against the dictionary)
    "propose_local": True,           # propose a local term when nothing suitable is found
}

MESSAGES = {
    "meddra_off": "MedDRA coding unavailable: dictionary access is not configured.",
    "whodrug_off": "WHODrug coding unavailable: check the licence or connection.",
    "no_match": "No suitable official match found. Local terminology proposed for review.",
}

# What can be coded, where it lives, and how it is coded.
ENTITY_SPECS = {
    "reaction": {"label": "Reaction/event", "kind": "event", "section": "D. Reactions", "required": True},
    "product": {"label": "Drug", "kind": "drug", "section": "C. Drugs", "required": True},
    "product_indication": {"label": "Indication", "kind": "event", "section": "C. Drugs", "required": False},
    "history": {"label": "Medical history", "kind": "event", "section": "E. Medical history", "required": False},
    "test": {"label": "Test name", "kind": "event", "section": "F. Tests", "required": False},
    "past_drug": {"label": "Past drug", "kind": "drug", "section": "G. Past drugs", "required": False},
    "past_drug_indication": {"label": "Past drug indication", "kind": "event", "section": "G. Past drugs", "required": False},
    "past_drug_reaction": {"label": "Past drug reaction", "kind": "event", "section": "G. Past drugs", "required": False},
}


def load_rules():
    try:
        row = query_one("SELECT value FROM pv.app_settings WHERE setting_key = %s", (RULES_KEY,))
    except Exception:
        terminology._rollback()
        row = None
    value = row["value"] if row else {}
    if isinstance(value, str):
        value = json.loads(value)
    return {**DEFAULT_RULES, **{k: bool(v) for k, v in (value or {}).items() if k in DEFAULT_RULES}}


def save_rules(values, user_id):
    rules = {k: bool(values.get(k)) for k in DEFAULT_RULES}
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO pv.app_settings (setting_key, value, updated_by, updated_at)
            VALUES (%s, %s::jsonb, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (setting_key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP
            """,
            (RULES_KEY, json.dumps(rules), user_id),
        )
    return rules


# --------------------------------------------------------------------------
# Items on a case
# --------------------------------------------------------------------------

def case_items(case_id):
    """Every codable reported item on the case, with where to find it in the ICSR form."""
    items = []

    def add(entity, entity_id, verbatim, focus, context=""):
        if verbatim and str(verbatim).strip():
            items.append({"entity": entity, "entity_id": entity_id, "verbatim": str(verbatim).strip(),
                          "focus": focus, "context": context, **ENTITY_SPECS[entity]})

    for r in query_all("SELECT reaction_id, reported_term FROM pv.case_reactions WHERE case_id = %s ORDER BY sort_order, reaction_id", (case_id,)):
        add("reaction", r["reaction_id"], r["reported_term"], f"reactions-r{r['reaction_id']}-reported_term")
    for p in query_all("SELECT case_product_id, product_name, product_role, indication FROM pv.case_products WHERE case_id = %s ORDER BY sort_order, case_product_id", (case_id,)):
        add("product", p["case_product_id"], p["product_name"], f"products-p{p['case_product_id']}-product_name", p.get("product_role") or "")
        add("product_indication", p["case_product_id"], p.get("indication"), f"products-p{p['case_product_id']}-indication", p["product_name"])
    for h in query_all("SELECT history_id, condition_text, for_parent FROM pv.case_medical_history WHERE case_id = %s ORDER BY for_parent, sort_order, history_id", (case_id,)):
        prefix = "parent_history" if h["for_parent"] else "history"
        add("history", h["history_id"], h["condition_text"], f"{prefix}-h{h['history_id']}-condition_text", "parent" if h["for_parent"] else "")
    for t in query_all("SELECT test_id, test_name FROM pv.case_tests WHERE case_id = %s ORDER BY sort_order, test_id", (case_id,)):
        add("test", t["test_id"], t["test_name"], f"tests-t{t['test_id']}-test_name")
    for d in query_all("SELECT past_drug_id, drug_name, indication_text, reaction_text, for_parent FROM pv.case_past_drugs WHERE case_id = %s ORDER BY for_parent, sort_order, past_drug_id", (case_id,)):
        prefix = "parent_past_drugs" if d["for_parent"] else "past_drugs"
        add("past_drug", d["past_drug_id"], d["drug_name"], f"{prefix}-d{d['past_drug_id']}-drug_name")
        add("past_drug_indication", d["past_drug_id"], d.get("indication_text"), f"{prefix}-d{d['past_drug_id']}-indication_text", d["drug_name"])
        add("past_drug_reaction", d["past_drug_id"], d.get("reaction_text"), f"{prefix}-d{d['past_drug_id']}-reaction_text", d["drug_name"])
    return items


def case_codings(case_id):
    rows = query_all("SELECT * FROM pv.term_codings WHERE case_id = %s", (case_id,))
    out = {}
    for row in rows:
        row = dict(row)
        if isinstance(row.get("candidates"), str):
            row["candidates"] = json.loads(row["candidates"])
        out[(row["entity"], row["entity_id"])] = row
    return out


def coding_overview(case_id):
    """Items joined with their coding, plus counts for the pathway."""
    codings = case_codings(case_id)
    items = case_items(case_id)
    for item in items:
        coding = codings.get((item["entity"], item["entity_id"]))
        if coding and coding["verbatim"] != item["verbatim"]:
            coding = dict(coding, status="Needs review", message="The reported wording changed after it was coded.")
        item["coding"] = coding
    open_required = [i for i in items if i["required"] and (not i["coding"] or i["coding"]["status"] != "Coded")]
    open_optional = [i for i in items if not i["required"] and (not i["coding"] or i["coding"]["status"] not in ("Coded", "Not coded"))]
    return {"items": items, "open_required": open_required, "open_optional": open_optional}


# --------------------------------------------------------------------------
# Automatic coding
# --------------------------------------------------------------------------

def _ai_candidates(text, kind):
    """Candidate names from the AI, kept only if they exist in the dictionary."""
    from app.services.llm import AIUnavailableError, PURPOSE_ASSESSMENT, generate_text

    dictionary = "MedDRA Lowest Level Term" if kind == "event" else "WHODrug drug name"
    prompt = (
        f"Suggest up to 5 {dictionary} names that could code this reported wording. "
        "Give names exactly as they appear in the dictionary; do not give codes.\n"
        f"Reported wording: {text}\n"
        'Answer as JSON: {"names": ["..."]}'
    )
    try:
        reply = generate_text(prompt, max_tokens=300, json_output=True, purpose=PURPOSE_ASSESSMENT)
        names = json.loads(re.search(r"\{.*\}", reply, re.S).group(0)).get("names", [])
    except (AIUnavailableError, AttributeError, ValueError, TypeError):
        return []
    out = []
    for name in names[:5]:
        if kind == "event":
            match = terminology.meddra_exact(str(name))
        else:
            found = terminology.whodrug_search(str(name), limit=1)
            match = found[0] if found and found[0]["method"] == "Exact" else None
        if match:
            out.append(dict(match, method="AI suggestion (verified in the dictionary)", score=0.8))
    return out


def suggest(item, rules=None, user_id=None):
    """Work out the coding for one item. Returns a dict ready to store."""
    rules = rules or load_rules()
    text, kind = item["verbatim"], item["kind"]
    dictionary = "MedDRA" if kind == "event" else "WHODrug"
    version = terminology.active_version(dictionary)
    result = {"dictionary": None, "code": None, "term": None, "pt_code": None, "pt_name": None, "soc_code": None,
              "soc_name": None, "version": None, "method": None, "status": "Needs review", "message": None, "candidates": []}

    def take(candidate, status, message=None):
        result.update({k: candidate.get(k) for k in ("dictionary", "code", "term", "pt_code", "pt_name", "soc_code", "soc_name", "version", "method")})
        result["status"] = status
        result["message"] = message
        return result

    if version:
        exact = terminology.meddra_exact(text, version) if kind == "event" else next(
            (c for c in terminology.whodrug_search(text, limit=3, version=version) if c["method"] == "Exact"), None)
        if exact:
            result["candidates"] = [exact]
            return take(exact, "Coded" if rules["auto_accept_exact"] else "Needs review",
                        None if rules["auto_accept_exact"] else "Exact match found; confirm it.")
        synonym = terminology.synonym_lookup(dictionary, text)
        if synonym:
            found = terminology.meddra_by_code(synonym["code"], version) if kind == "event" else terminology.whodrug_by_code(synonym["code"], version)
            if found:
                found = dict(found, method="Synonym (confirmed before)", score=0.99)
                result["candidates"] = [found]
                return take(found, "Coded" if rules["auto_accept_synonym"] else "Needs review",
                            None if rules["auto_accept_synonym"] else "Matched wording confirmed before; confirm it.")
        candidates = terminology.meddra_search(text, version=version) if kind == "event" else terminology.whodrug_search(text, version=version)
        if rules["ai_suggestions"]:
            known = {c["code"] for c in candidates}
            candidates += [c for c in _ai_candidates(text, kind) if c["code"] not in known]
        result["candidates"] = candidates
        if candidates and candidates[0]["score"] >= 0.6:
            result["message"] = f"{len(candidates)} {dictionary} candidate(s) found; choose the right one."
            return result
        unavailable = None
    else:
        unavailable = MESSAGES["meddra_off"] if kind == "event" else MESSAGES["whodrug_off"]

    # Local terminology fallback.
    local = terminology.local_event_search(text) if kind == "event" else terminology.local_product_search(text)
    result["candidates"] += local
    best = local[0] if local else None
    if best and best["score"] == 1.0 and best.get("status") == "Approved" and rules["auto_accept_local"]:
        return take(best, "Coded", (unavailable + " " if unavailable else "") + "Coded to the approved local term.")
    if best and best["score"] >= 0.75:
        result["message"] = (unavailable + " " if unavailable else "") + "Local terminology candidates found; choose one or propose a new local term."
        return result
    if rules["propose_local"] and user_id is not None:
        proposal = (terminology.propose_local_event(text, text, user_id) if kind == "event"
                    else terminology.propose_local_product(text, text, user_id))
        result["candidates"].append(proposal)
        return take(proposal, "Needs review", ((unavailable + " ") if unavailable else "") + MESSAGES["no_match"])
    result["message"] = (unavailable + " " if unavailable else "") + "Nothing suitable found; search or propose a local term."
    return result


def code_case(case_id, user_id, only_missing=True):
    """Code every item that is not coded yet. Returns (coded, needs_review, messages)."""
    rules = load_rules()
    overview = coding_overview(case_id)
    coded = review = 0
    messages = set()
    for item in overview["items"]:
        coding = item["coding"]
        if only_missing and coding and coding["status"] in ("Coded", "Not coded") and coding["verbatim"] == item["verbatim"]:
            continue
        if coding and coding.get("method") == "Manual" and coding["verbatim"] == item["verbatim"]:
            continue
        result = suggest(item, rules, user_id)
        store(case_id, item, result, user_id, action="Automatic coding")
        if result["status"] == "Coded":
            coded += 1
        else:
            review += 1
        if result.get("message"):
            for key in ("meddra_off", "whodrug_off"):
                if MESSAGES[key] in result["message"]:
                    messages.add(MESSAGES[key])
    return coded, review, sorted(messages)


def store(case_id, item, result, user_id, action, reviewed=False):
    candidates = json.dumps(result.get("candidates") or [], default=str)
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO pv.term_codings (case_id, entity, entity_id, verbatim, dictionary, code, term, pt_code, pt_name,
                                         soc_code, soc_name, version, method, status, message, candidates, coded_by, coded_at,
                                         reviewed_by, reviewed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW(), %s, CASE WHEN %s THEN NOW() END)
            ON CONFLICT (entity, entity_id) DO UPDATE SET
                verbatim = EXCLUDED.verbatim, dictionary = EXCLUDED.dictionary, code = EXCLUDED.code, term = EXCLUDED.term,
                pt_code = EXCLUDED.pt_code, pt_name = EXCLUDED.pt_name, soc_code = EXCLUDED.soc_code, soc_name = EXCLUDED.soc_name,
                version = EXCLUDED.version, method = EXCLUDED.method, status = EXCLUDED.status, message = EXCLUDED.message,
                candidates = EXCLUDED.candidates, coded_by = EXCLUDED.coded_by, coded_at = NOW(),
                reviewed_by = EXCLUDED.reviewed_by, reviewed_at = EXCLUDED.reviewed_at
            RETURNING coding_id
            """,
            (case_id, item["entity"], item["entity_id"], item["verbatim"], result.get("dictionary"), result.get("code"),
             result.get("term"), result.get("pt_code"), result.get("pt_name"), result.get("soc_code"), result.get("soc_name"),
             result.get("version"), result.get("method"), result.get("status"), result.get("message"), candidates,
             user_id, user_id if reviewed else None, reviewed),
        )
        coding_id = cur.fetchone()["coding_id"]
        snapshot = {k: result.get(k) for k in ("dictionary", "code", "term", "pt_code", "pt_name", "version", "method", "status", "message")}
        snapshot["verbatim"] = item["verbatim"]
        cur.execute(
            "INSERT INTO pv.term_coding_history (coding_id, case_id, action, snapshot, actor) VALUES (%s, %s, %s, %s::jsonb, %s)",
            (coding_id, case_id, action, json.dumps(snapshot, default=str), user_id),
        )
        if item["entity"] == "reaction":
            _sync_signal_grouping(cur, case_id, item["verbatim"], result)
    return coding_id


def _sync_signal_grouping(cur, case_id, verbatim, result):
    """Signal detection groups cases by the local event terms; keep it in step."""
    term_id = None
    if result.get("dictionary") == "Local" and result.get("code"):
        cur.execute("SELECT term_id FROM pv.event_terms WHERE local_code = %s", (result["code"],))
        row = cur.fetchone()
        term_id = row["term_id"] if row else None
    elif result.get("dictionary") == "MedDRA" and result.get("pt_name"):
        cur.execute(
            "SELECT term_id FROM pv.event_terms WHERE LOWER(preferred_term) = LOWER(%s) OR mapped_code IN (%s, %s) LIMIT 1",
            (result["pt_name"], result.get("pt_code"), result.get("code")),
        )
        row = cur.fetchone()
        term_id = row["term_id"] if row else None
    if term_id and result.get("status") == "Coded":
        cur.execute(
            """
            INSERT INTO pv.case_event_terms (case_id, verbatim_term, term_id, coding_method)
            VALUES (%s, %s, %s, 'Manual')
            ON CONFLICT (case_id, verbatim_term) DO UPDATE SET term_id = EXCLUDED.term_id, coding_method = 'Manual'
            """,
            (case_id, verbatim[:500], term_id),
        )


def choose(case_id, entity, entity_id, dictionary, code, user_id, remember=False):
    """A person picks the coding for an item."""
    item = next((i for i in case_items(case_id) if i["entity"] == entity and i["entity_id"] == int(entity_id)), None)
    if not item:
        raise ValueError("That item is no longer on the case.")
    if dictionary == "MedDRA":
        candidate = terminology.meddra_by_code(code)
    elif dictionary == "WHODrug":
        candidate = terminology.whodrug_by_code(code)
    else:
        candidate = _local_by_code(code, item["kind"])
    if not candidate:
        raise ValueError("That term is not in the current dictionary.")
    result = {**{k: candidate.get(k) for k in ("dictionary", "code", "term", "pt_code", "pt_name", "soc_code", "soc_name", "version")},
              "method": "Manual", "status": "Coded", "message": None, "candidates": [candidate]}
    if candidate["dictionary"] == "Local" and candidate.get("status") == "Proposed":
        result["status"] = "Needs review"
        result["message"] = "Coded to a proposed local term; it counts once the term is approved."
    store(case_id, item, result, user_id, action="Coding chosen", reviewed=True)
    if remember and dictionary in ("MedDRA", "WHODrug"):
        terminology.remember_synonym(dictionary, item["verbatim"], candidate["code"], candidate["term"], candidate["version"], user_id)
    return result


def mark_not_coded(case_id, entity, entity_id, reason, user_id):
    item = next((i for i in case_items(case_id) if i["entity"] == entity and i["entity_id"] == int(entity_id)), None)
    if not item:
        raise ValueError("That item is no longer on the case.")
    if item["required"]:
        raise ValueError("Reactions and drugs must be coded.")
    store(case_id, item, {"status": "Not coded", "method": "Manual", "message": (reason or "Not codable").strip()[:500]},
          user_id, action="Marked as not coded", reviewed=True)


def propose_local(case_id, entity, entity_id, name, user_id):
    item = next((i for i in case_items(case_id) if i["entity"] == entity and i["entity_id"] == int(entity_id)), None)
    if not item:
        raise ValueError("That item is no longer on the case.")
    candidate = (terminology.propose_local_event(name or item["verbatim"], item["verbatim"], user_id) if item["kind"] == "event"
                 else terminology.propose_local_product(name or item["verbatim"], item["verbatim"], user_id))
    return choose(case_id, entity, entity_id, "Local", candidate["code"], user_id)


def _local_by_code(code, kind):
    if kind == "event":
        row = query_one("SELECT * FROM pv.event_terms WHERE local_code = %s", (code,))
        return terminology._local_event_candidate(row, "Selected", 1.0) if row else None
    row = query_one("SELECT * FROM pv.local_product_terms WHERE local_code = %s", (code,))
    if not row:
        return None
    return {"dictionary": "Local", "code": row["local_code"], "term": row["name"], "local_id": row["local_product_id"],
            "pt_code": None, "pt_name": row.get("ingredients"), "soc_code": None, "soc_name": None,
            "version": "local", "method": "Selected", "score": 1.0, "status": row["status"]}


def search(entity_kind, text):
    """Candidates for the search box in the coding step."""
    official = terminology.meddra_search(text, limit=10) if entity_kind == "event" else terminology.whodrug_search(text, limit=10)
    local = terminology.local_event_search(text) if entity_kind == "event" else terminology.local_product_search(text)
    return official + local


def history(case_id):
    return query_all(
        """
        SELECT h.*, u.full_name AS actor_name, c.entity, c.entity_id
        FROM pv.term_coding_history AS h
        LEFT JOIN pv.term_codings AS c ON c.coding_id = h.coding_id
        LEFT JOIN pv.users AS u ON u.user_id = h.actor
        WHERE h.case_id = %s ORDER BY h.occurred_at DESC LIMIT 100
        """,
        (case_id,),
    )
