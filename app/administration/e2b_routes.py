"""Administration › E2B settings: sender, recipients and their rules."""

import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.audit import write_audit_log
from app.security import roles_required
from app.services import e2b

bp = Blueprint("e2b_admin", __name__, url_prefix="/administration/e2b")

SENDER_TYPES = [("1", "Pharmaceutical company"), ("2", "Regulatory authority"), ("3", "Health professional"),
                ("4", "Regional pharmacovigilance centre"), ("5", "WHO collaborating centre for international drug monitoring"),
                ("6", "Other"), ("7", "Patient / consumer")]
RULES = [
    ("require_all_reactions_meddra", "Every reaction must be coded to MedDRA"),
    ("require_most_recent_date", "Date of most recent information is required"),
    ("require_batch_for_suspects", "Batch number required for suspect drugs"),
    ("require_study_registration", "Remind about study registration for study reports"),
]


@bp.route("/", methods=["GET", "POST"])
@roles_required("System Administrator")
def settings():
    current = e2b.load_settings()
    if request.method == "POST":
        values = {
            "sender_identifier": (request.form.get("sender_identifier") or "").strip()[:60],
            "sender_type": request.form.get("sender_type") if request.form.get("sender_type") in dict(SENDER_TYPES) else "1",
            "sender_organisation": (request.form.get("sender_organisation") or "").strip()[:100],
            "sender_department": (request.form.get("sender_department") or "").strip()[:60],
            "sender_country": (request.form.get("sender_country") or "").strip().upper()[:2],
        }
        problems = []
        if values["sender_country"] and not re.fullmatch(r"[A-Z]{2}", values["sender_country"]):
            problems.append("The sender country must be a two-letter ISO code, e.g. UG.")
        recipients = []
        for index in range(int(request.form.get("recipient_count") or 0) + 1):
            name = (request.form.get(f"r{index}-name") or "").strip()
            if not name or request.form.get(f"r{index}-remove"):
                continue
            key = (request.form.get(f"r{index}-key") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:40]
            versions = [v for v in e2b.VERSIONS if request.form.get(f"r{index}-{v}")]
            if not versions:
                problems.append(f"{name}: choose E2B(R3), E2B(R2) or both.")
            recipient = {"key": key, "name": name[:100], "identifier": (request.form.get(f"r{index}-identifier") or "").strip()[:60],
                         "versions": versions}
            for rule, _ in RULES:
                recipient[rule] = bool(request.form.get(f"r{index}-{rule}"))
            recipients.append(recipient)
        if not recipients:
            problems.append("Keep at least one recipient.")
        if len({r["key"] for r in recipients}) != len(recipients):
            problems.append("Two recipients have the same name; give each a different name.")
        if problems:
            for problem in problems:
                flash(f"Nothing was saved. {problem}", "error")
            return render_template("administration/e2b_settings.html", settings={**current, **values, "recipients": recipients or current["recipients"]},
                                   sender_types=SENDER_TYPES, rules=RULES, schemas=e2b.schema_files(), schema_dir=e2b.schema_dir())
        values["recipients"] = recipients
        e2b.save_settings(values, session["user_id"])
        write_audit_log("settings", 0, "E2B settings changed", session["user_id"],
                        f"Sender {values['sender_identifier'] or '(not set)'}; recipients: " + ", ".join(r["name"] for r in recipients))
        flash("E2B settings saved.", "success")
        return redirect(url_for("administration.user_list"))
    return render_template("administration/e2b_settings.html", settings=current, sender_types=SENDER_TYPES, rules=RULES,
                           schemas=e2b.schema_files(), schema_dir=e2b.schema_dir())
