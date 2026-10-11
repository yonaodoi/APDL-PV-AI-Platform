"""The case processing pathway shown at the top of every safety case:

Intake → Validation → ICSR completion → Coding → Assessment → Narrative →
Review → Reporting.

Each stage is worked out from what is recorded on the case, so the pathway
never disagrees with the data. The first stage that is not finished is the
current stage; its outstanding issues and the next action are shown.

Minimum case validity (identifiable patient, identifiable reporter, suspect
product, suspected reaction) is assessed separately from full completeness.
"""

from datetime import date

from flask import url_for

STAGES = [
    ("intake", "Intake"),
    ("validation", "Validation"),
    ("icsr", "ICSR completion"),
    ("coding", "Coding"),
    ("assessment", "Assessment"),
    ("narrative", "Narrative"),
    ("review", "Review"),
    ("reporting", "Reporting"),
]
STAGE_LABELS = dict(STAGES)

VALIDITY_CRITERIA = [
    ("validity_patient", "Identifiable patient"),
    ("validity_reporter", "Identifiable reporter"),
    ("validity_product", "Suspect medicinal product"),
    ("validity_event", "Suspected reaction/event"),
]
VALIDITY_CHOICES = ["Present", "Absent", "Uncertain"]

# Words that do not identify anyone on their own.
_VAGUE = {"patient", "a patient", "the patient", "unknown", "unk", "n/a", "na", "not known", "-", "?", "none", "nil", "x"}


def _filled(value):
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in _VAGUE


def suggest_validity(case, products, reactions):
    """Rule-based suggestion for the four minimum criteria.

    Returns {criterion: (suggestion, reason)}. 'Uncertain' means a person must
    look; nothing is decided automatically.
    """
    out = {}
    patient_bits = [
        ("initials", case.get("patient_initials")), ("date of birth", case.get("patient_dob_text") or case.get("patient_date_of_birth")),
        ("age", case.get("patient_age_value") or case.get("patient_age_years")), ("sex", case.get("patient_sex") if case.get("patient_sex") not in (None, "", "Unknown") else None),
        ("age group", case.get("patient_age_group")),
    ]
    present = [label for label, value in patient_bits if _filled(value)]
    if len(present) >= 1:
        out["validity_patient"] = ("Present", "Recorded: " + ", ".join(present) + ".")
    elif _filled(case.get("patient_address")) or _filled(case.get("patient_phone")):
        out["validity_patient"] = ("Uncertain", "Only contact details are recorded; confirm the patient is a specific person.")
    else:
        out["validity_patient"] = ("Absent", "No initials, date of birth, age, sex or age group recorded.")

    reporter_bits = [
        ("name", case.get("reporter_name") or case.get("reporter_family_name") or case.get("reporter_given_name")),
        ("qualification", case.get("reporter_qualification") or case.get("reporter_profession")),
        ("email", case.get("reporter_email")), ("telephone", case.get("reporter_phone")),
        ("organisation", case.get("reporter_organisation")),
    ]
    present = [label for label, value in reporter_bits if _filled(value)]
    if any(label in present for label in ("name", "email", "telephone")):
        out["validity_reporter"] = ("Present", "Recorded: " + ", ".join(present) + ".")
    elif present:
        out["validity_reporter"] = ("Uncertain", "Only " + ", ".join(present) + " recorded; confirm the reporter can be contacted or identified.")
    else:
        out["validity_reporter"] = ("Absent", "No reporter name, qualification or contact recorded.")

    suspects = [p for p in products if (p.get("product_role") or "Suspect") in ("Suspect", "Interacting")]
    named = [p for p in suspects if _filled(p.get("product_name"))]
    if named:
        out["validity_product"] = ("Present", "Suspect drug: " + ", ".join(p["product_name"] for p in named[:3]) + ".")
    elif products:
        out["validity_product"] = ("Uncertain", "Drugs are recorded but none is marked as suspect.")
    else:
        out["validity_product"] = ("Absent", "No drug recorded.")

    terms = [r.get("reported_term") for r in reactions if _filled(r.get("reported_term"))]
    if not terms and _filled(case.get("event_description")):
        terms = [case["event_description"]]
    if terms and all(len(t.strip()) >= 3 for t in terms):
        out["validity_event"] = ("Present", "Reported: " + "; ".join(t[:60] for t in terms[:3]) + ".")
    elif terms:
        out["validity_event"] = ("Uncertain", "The reported wording is very short; confirm it describes a reaction.")
    else:
        out["validity_event"] = ("Absent", "No reaction/event recorded.")
    return out


def validity_outcome(case):
    """'Valid', 'Not valid' or 'Not assessed' from the four recorded criteria."""
    values = [case.get(key) for key, _ in VALIDITY_CRITERIA]
    if not all(values):
        return "Not assessed"
    if all(v == "Present" for v in values):
        return "Valid"
    return "Not valid"


# --------------------------------------------------------------------------
# The pathway
# --------------------------------------------------------------------------

def build_pathway(case, products, reactions, checks, assessment, causality, follow_up, today=None, coding=None,
                  narrative_state=None):
    """Stages with done/current/todo, outstanding issues and the next action.

    ``checks``: completeness checks; ``assessment``: listedness assessment row
    or None; ``causality``: rows from pv.case_causality; ``follow_up``: dict
    from :func:`follow_up_summary`; ``coding``: the overview from
    :func:`app.services.coding.coding_overview` (None → older event coding check).
    """
    today = today or date.today()
    case_id = case["case_id"]
    status = case.get("workflow_status")
    issues = {key: [] for key, _ in STAGES}
    done = {}

    done["intake"] = True

    decision = case.get("validity_decision")
    done["validation"] = decision == "Valid"
    if decision == "Awaiting information":
        issues["validation"].append({
            "text": "Not a valid case yet: waiting for the missing minimum information"
                    + (f" (due {follow_up['next_due']:%d %b %Y})" if follow_up.get("next_due") else "") + ".",
            "href": url_for("cases.case_validity", case_id=case_id),
        })
    elif not decision:
        issues["validation"].append({
            "text": "Confirm the four minimum criteria (patient, reporter, suspect drug, reaction).",
            "href": url_for("cases.case_validity", case_id=case_id),
        })

    icsr_checks = [c for c in checks or [] if c.get("status") != "Pass" and c.get("code") != "event_coding"]
    for check in icsr_checks:
        issues["icsr"].append({
            "text": f"{check.get('label')}: {check.get('message')}",
            "href": url_for("cases.icsr_form", case_id=case_id, focus=check.get("code"), why=check.get("message")),
            "code": check.get("code"),
        })
    if not reactions:
        issues["icsr"].append({"text": "No reaction/event is recorded.", "href": url_for("cases.icsr_form", case_id=case_id) + "#section-reactions"})
    for reaction in reactions:
        if not reaction.get("outcome"):
            issues["icsr"].append({
                "text": f"Outcome missing for “{(reaction.get('reported_term') or '')[:50]}”.",
                "href": url_for("cases.icsr_form", case_id=case_id, focus=f"reactions-r{reaction['reaction_id']}-outcome",
                                why="Every reaction needs an outcome (or ‘Unknown’)."),
            })
    done["icsr"] = done["validation"] and not issues["icsr"]

    if coding is not None:
        page = url_for("cases.case_coding", case_id=case_id)
        for item in coding.get("open_required", []):
            cd = item.get("coding")
            state = cd["status"].lower() if cd else "not coded yet"
            issues["coding"].append({
                "text": f"{item['label']} “{item['verbatim'][:50]}”: {state}" + (f" – {cd['message']}" if cd and cd.get("message") else "."),
                "href": page + f"#item-{item['entity']}-{item['entity_id']}",
            })
    else:
        old_check = [c for c in checks or [] if c.get("code") == "event_coding"]
        if old_check and old_check[0].get("status") != "Pass":
            issues["coding"].append({"text": old_check[0].get("message"), "href": url_for("cases.case_detail", case_id=case_id) + "#event-coding"})
    done["coding"] = done["icsr"] and not issues["coding"]

    if not assessment:
        issues["assessment"].append({"text": "Listedness/expectedness has not been assessed.", "href": url_for("cases.case_detail", case_id=case_id) + "#listedness"})
    elif assessment.get("assessment_source", "reviewer") == "automatic":
        issues["assessment"].append({"text": "Listedness is an automatic result; a reviewer must confirm it.", "href": url_for("cases.case_detail", case_id=case_id) + "#listedness"})
    elif assessment.get("listedness_status") == "Insufficient information":
        issues["assessment"].append({"text": "Listedness could not be assessed (insufficient information).", "href": url_for("cases.case_detail", case_id=case_id) + "#listedness"})
    suspects = [p for p in products if (p.get("product_role") or "Suspect") in ("Suspect", "Interacting")]
    reviewed = [c for c in causality or [] if not c.get("is_ai_draft") and c.get("result")]
    missing_pairs = []
    for product in suspects:
        for reaction in reactions:
            if not any(c.get("case_product_id") == product["case_product_id"] and c.get("reaction_id") == reaction["reaction_id"] for c in reviewed):
                missing_pairs.append(f"{product.get('product_name')} → {(reaction.get('reported_term') or '')[:40]}")
    if missing_pairs:
        issues["assessment"].append({
            "text": f"Causality not yet assessed for {len(missing_pairs)} drug–reaction pair(s): " + "; ".join(missing_pairs[:3]) + ("…" if len(missing_pairs) > 3 else ""),
            "href": url_for("cases.icsr_form", case_id=case_id) + "#section-assessments",
        })
    if any(c.get("is_ai_draft") for c in causality or []):
        issues["assessment"].append({
            "text": "An AI-assisted causality draft is recorded; it does not count until a person records their own assessment.",
            "href": url_for("cases.icsr_form", case_id=case_id) + "#section-assessments",
        })
    done["assessment"] = done["coding"] and not issues["assessment"]

    narrative_page = url_for("cases.case_narrative", case_id=case_id)
    if not (case.get("case_narrative") or "").strip():
        issues["narrative"].append({"text": "No case narrative yet.", "href": narrative_page})
    elif narrative_state == "unreviewed":
        issues["narrative"].append({"text": "The narrative has not been reviewed by a second person yet.", "href": narrative_page})
    done["narrative"] = done["assessment"] and not issues["narrative"]

    stage = case.get("approval_stage")
    done["review"] = stage == "Approved" or status in ("Approved", "Submitted", "Closed")
    if not done["review"]:
        if stage in ("Pending review", "Pending approval"):
            issues["review"].append({"text": f"Waiting for sign-off ({stage.lower()}).", "href": url_for("cases.case_detail", case_id=case_id) + "#approval"})
        elif stage == "Returned":
            issues["review"].append({"text": "Returned by the reviewer for changes.", "href": url_for("cases.case_detail", case_id=case_id) + "#approval"})
        else:
            issues["review"].append({"text": "Not yet sent for review and approval.", "href": url_for("cases.case_detail", case_id=case_id) + "#approval"})

    done["reporting"] = bool(case.get("regulatory_submitted_date")) or status in ("Submitted", "Closed")
    if not done["reporting"]:
        issues["reporting"].append({"text": "Not yet reported to the authority.", "href": url_for("cases.case_detail", case_id=case_id) + "#approval"})

    # The first stage that is not finished is the current one (a case can be
    # reviewed or reported before every earlier box is ticked; those stages
    # still show their open issues).
    current = next((key for key, _ in STAGES if not done[key]), None)
    stages = []
    for key, label in STAGES:
        state = "done" if done[key] else ("current" if key == current else "todo")
        if state == "todo" and issues[key] and done.get("review") and key not in ("reporting",):
            state = "open"
        stages.append({"key": key, "label": label, "state": state, "issues": issues[key]})

    return {
        "stages": stages,
        "current": current,
        "current_label": STAGE_LABELS.get(current, "Complete"),
        "completed": [s["label"] for s in stages if s["state"] == "done"],
        "issues": issues.get(current, []) if current else [],
        "all_issues": [dict(i, stage=STAGE_LABELS[k]) for k, _ in STAGES for i in issues[k]],
        "next_action": next_action(case, current, issues),
        "follow_up": follow_up,
    }


def next_action(case, current, issues):
    case_id = case["case_id"]
    if current is None:
        return {"label": "Case complete", "href": url_for("cases.case_detail", case_id=case_id), "hint": "All pathway stages are finished."}
    if current == "validation":
        if case.get("validity_decision") == "Awaiting information":
            return {"label": "Check the follow-up and revalidate", "href": url_for("cases.case_validity", case_id=case_id),
                    "hint": "When the missing minimum information arrives, add it to this case and confirm validity."}
        return {"label": "Confirm case validity", "href": url_for("cases.case_validity", case_id=case_id),
                "hint": "Check the four minimum criteria. Valid cases move on to ICSR completion; others wait for information."}
    if current == "icsr":
        return {"label": "Complete the ICSR form", "href": url_for("cases.icsr_form", case_id=case_id),
                "hint": f"{len(issues['icsr'])} item(s) to complete."}
    if current == "coding":
        return {"label": "Confirm the coding", "href": url_for("cases.case_coding", case_id=case_id),
                "hint": "Choose MedDRA terms for the reactions and WHODrug (or local) terms for the drugs."}
    if current == "assessment":
        first = issues["assessment"][0] if issues["assessment"] else None
        return {"label": "Complete the assessment", "href": first["href"] if first else url_for("cases.icsr_form", case_id=case_id) + "#section-assessments",
                "hint": "Listedness against the reference safety information and causality for each drug–reaction pair."}
    if current == "narrative":
        return {"label": "Write and review the narrative", "href": url_for("cases.case_narrative", case_id=case_id),
                "hint": "Generate a draft from the recorded facts, check it, then a second person reviews it."}
    if current == "review":
        return {"label": "Send for review and approval", "href": url_for("cases.case_detail", case_id=case_id) + "#approval",
                "hint": "The reviewer and approver sign off before reporting."}
    return {"label": "Make the E2B file and record the submission", "href": url_for("cases.case_e2b", case_id=case_id),
            "hint": "Download the report or E2B file, submit it, then record the submission date."}


def follow_up_summary(tasks, requests, today=None):
    """Open follow-up items, next due date, overdue count and last request."""
    today = today or date.today()
    open_tasks = [t for t in tasks or [] if t.get("status") in ("Open", "In progress")]
    dues = sorted(t["due_date"] for t in open_tasks if t.get("due_date"))
    sent = [r for r in requests or [] if r.get("status") == "Sent"]
    last = max((r["sent_at"] for r in sent), default=None)
    return {
        "open": len(open_tasks),
        "next_due": dues[0] if dues else None,
        "overdue": sum(1 for d in dues if d < today),
        "last_request": last,
        "requests_sent": len(sent),
        "owners": sorted({t.get("assigned_to_name") for t in open_tasks if t.get("assigned_to_name")}),
    }
