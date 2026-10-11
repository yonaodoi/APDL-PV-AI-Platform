"""Case intake from uploaded documents (one or several patient reports)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (
    abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for,
)
from werkzeug.utils import secure_filename

from app.audit import write_audit_log
from app.cases.routes import bp
from app.db import query_one
from app.security import login_required
from app.services import document_intake as intake
from app.services.case_document_extraction import MAX_DOCUMENT_BYTES, SUPPORTED_EXTENSIONS


def _document_or_404(document_id):
    document = intake.get_document(document_id)
    if not document:
        abort(404)
    return document


def _proposal_or_404(proposal_id):
    proposal = intake.get_proposal(proposal_id)
    if not proposal:
        abort(404)
    return proposal


def _stuck(document):
    """Still 'working' long after upload: the server was probably restarted."""
    uploaded = document.get("uploaded_at")
    if document["status"] in ("Ready", "Failed") or not isinstance(uploaded, datetime):
        return False
    if uploaded.tzinfo is None:
        uploaded = uploaded.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - uploaded > timedelta(minutes=20)


def _review(document_id, anchor=""):
    return redirect(url_for("cases.intake_document", document_id=document_id) + anchor)


@bp.get("/intake/")
@login_required
def intake_home():
    try:
        recent = intake.recent_documents()
    except Exception:
        current_app.logger.exception("Intake list unavailable (run the migrations?)")
        intake._rollback()
        recent = []
    return render_template("cases/extract_case_document.html", recent=recent)


@bp.post("/intake/upload")
@login_required
def intake_upload():
    uploaded = request.files.get("case_document")
    if not uploaded or not uploaded.filename:
        flash("Choose a case document to upload.", "error")
        return redirect(url_for("cases.intake_home"))
    filename = secure_filename(uploaded.filename)
    if not filename or Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        flash("Upload a PDF, DOCX, or plain text case document.", "error")
        return redirect(url_for("cases.intake_home"))
    data = uploaded.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        flash("The document exceeds the 20 MB upload limit.", "error")
        return redirect(url_for("cases.intake_home"))
    if not data:
        flash("The file is empty.", "error")
        return redirect(url_for("cases.intake_home"))

    document, is_new = intake.store_upload(filename, uploaded.content_type, data, session["user_id"])
    if is_new:
        write_audit_log("intake", document["document_id"], "Document uploaded", session["user_id"],
                        f"{filename} ({len(data):,} bytes) uploaded for case intake.")
        intake.start_processing(current_app._get_current_object(), document["document_id"])
        return redirect(url_for("cases.intake_document", document_id=document["document_id"], auto=1))
    if document["status"] == "Failed":
        intake.start_processing(current_app._get_current_object(), document["document_id"])
        flash("This document was uploaded before but could not be read then; it is being read again.", "info")
        return redirect(url_for("cases.intake_document", document_id=document["document_id"]))
    flash(
        f"This exact document was already uploaded ({document['original_filename']}, "
        f"{document['uploaded_at']:%d %b %Y %H:%M}). Showing what was found then; nothing new was created.",
        "warning",
    )
    return redirect(url_for("cases.intake_document", document_id=document["document_id"]))


@bp.get("/intake/<int:document_id>")
@login_required
def intake_document(document_id):
    document = _document_or_404(document_id)
    rows = intake.proposals(document_id)
    working = document["status"] not in ("Ready", "Failed")
    if not working and request.args.get("auto"):
        visible = [p for p in rows if p["status"] != "Merged"]
        single = visible[0] if len(visible) == 1 else None
        if (single and single["kind"] == "New patient" and single["status"] == "Proposed"
                and single["extraction_status"] == "Done" and not single["duplicate_cases"]):
            flash("One patient report was found. Check the draft against the document, then press Create safety case.", "info")
            return redirect(url_for("cases.intake_create_case", proposal_id=single["proposal_id"]))
    return render_template(
        "cases/intake_review.html",
        document=document,
        proposals=rows,
        working=working,
        kinds=intake.KINDS,
        stuck=_stuck(document),
        can_retry=document["status"] == "Failed" or (
            document["status"] == "Ready" and not any(p["decided_by"] for p in rows)),
    )


@bp.get("/intake/<int:document_id>/status.json")
@login_required
def intake_status(document_id):
    document = _document_or_404(document_id)
    return jsonify({"status": document["status"], "progress": document.get("progress"), "error": document.get("error"),
                    "done": document["status"] in ("Ready", "Failed")})


@bp.get("/intake/<int:document_id>/file")
@login_required
def intake_file(document_id):
    document = _document_or_404(document_id)
    path = intake.document_path(document)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=request.args.get("download") == "1",
                     download_name=document["original_filename"], mimetype=document.get("content_type") or None)


@bp.post("/intake/<int:document_id>/retry")
@login_required
def intake_retry(document_id):
    document = _document_or_404(document_id)
    rows = intake.proposals(document_id)
    if any(p["decided_by"] for p in rows):
        flash("The reports have already been reviewed, so the document is not read again (that could duplicate work). "
              "Split, merge or reject the reports instead.", "error")
        return _review(document_id)
    if document["status"] == "Ready" or _stuck(document):
        intake._set_document(document_id, status="Failed", error="Read again on request.")
    intake.start_processing(current_app._get_current_object(), document_id)
    flash("Reading the document again.", "info")
    return redirect(url_for("cases.intake_document", document_id=document_id))


def _act(proposal_id, action):
    proposal = _proposal_or_404(proposal_id)
    try:
        message = action(proposal)
    except intake.IntakeError as error:
        flash(f"Not changed: {error}", "error")
    else:
        if message:
            flash(message, "success")
    return _review(proposal["document_id"], f"#report-{proposal_id}")


@bp.post("/intake/proposal/<int:proposal_id>/kind")
@login_required
def intake_kind(proposal_id):
    def run(p):
        intake.set_kind(proposal_id, request.form.get("kind"), session["user_id"])
        return f"Report {p['seq']} is now treated as: {request.form.get('kind')}."
    return _act(proposal_id, run)


@bp.post("/intake/proposal/<int:proposal_id>/reject")
@login_required
def intake_reject(proposal_id):
    def run(p):
        intake.reject(proposal_id, (request.form.get("reason") or "").strip(), session["user_id"])
        write_audit_log("intake", p["document_id"], "Report rejected", session["user_id"],
                        f"Report {p['seq']}: {request.form.get('reason') or 'no reason given'}")
        return f"Report {p['seq']} rejected. No case will be created from it."
    return _act(proposal_id, run)


@bp.post("/intake/proposal/<int:proposal_id>/restore")
@login_required
def intake_restore(proposal_id):
    def run(p):
        intake.restore(proposal_id, session["user_id"])
        return f"Report {p['seq']} restored."
    return _act(proposal_id, run)


@bp.post("/intake/proposal/<int:proposal_id>/split")
@login_required
def intake_split(proposal_id):
    proposal = _proposal_or_404(proposal_id)
    try:
        ids = intake.split(proposal_id, request.form.get("marker"), session["user_id"])
    except intake.IntakeError as error:
        flash(f"Not split: {error}", "error")
        return _review(proposal["document_id"], f"#report-{proposal_id}")
    write_audit_log("intake", proposal["document_id"], "Report split", session["user_id"], f"Report {proposal['seq']} split in two.")
    intake.reextract_in_background(current_app._get_current_object(), proposal["document_id"], ids)
    flash(f"Report {proposal['seq']} was split in two. The facts are being extracted again for each part.", "success")
    return _review(proposal["document_id"])


@bp.post("/intake/<int:document_id>/merge")
@login_required
def intake_merge(document_id):
    _document_or_404(document_id)
    selected = request.form.getlist("proposal_ids")
    try:
        keep = intake.merge(selected, session["user_id"])
    except (intake.IntakeError, ValueError) as error:
        flash(f"Not merged: {error}", "error")
        return _review(document_id)
    write_audit_log("intake", document_id, "Reports merged", session["user_id"], f"Proposals {', '.join(selected)} merged.")
    intake.reextract_in_background(current_app._get_current_object(), document_id, [keep])
    flash("The reports were merged into one. The facts are being extracted again from the combined text.", "success")
    return _review(document_id)


@bp.post("/intake/proposal/<int:proposal_id>/follow-up")
@login_required
def intake_follow_up(proposal_id):
    proposal = _proposal_or_404(proposal_id)
    number = (request.form.get("case_number") or "").strip()
    case = query_one("SELECT case_id, case_number FROM pv.safety_cases WHERE UPPER(case_number) = UPPER(%s)", (number,)) if number else None
    if not case:
        flash(f"Not linked: no case with the number “{number}” was found.", "error")
        return _review(proposal["document_id"], f"#report-{proposal_id}")
    try:
        attachment_id, count = intake.link_follow_up(proposal_id, case["case_id"], session["user_id"])
    except intake.IntakeError as error:
        flash(f"Not linked: {error}", "error")
        return _review(proposal["document_id"], f"#report-{proposal_id}")
    write_audit_log("case", case["case_id"], "Follow-up linked from intake", session["user_id"],
                    f"Report {proposal['seq']} of intake document {proposal['document_id']}.")
    if count:
        flash(f"Report {proposal['seq']} was added to {case['case_number']} as follow-up with {count} suggested update(s). "
              "Review them before they are applied.", "success")
        return redirect(url_for("cases.review_document_suggestions", case_id=case["case_id"], attachment_id=attachment_id))
    flash(f"Report {proposal['seq']} was added to {case['case_number']} as follow-up. It adds nothing that differs from the case.", "success")
    return _review(proposal["document_id"], f"#report-{proposal_id}")


@bp.get("/intake/proposal/<int:proposal_id>/create")
@login_required
def intake_create_case(proposal_id):
    """The case form, filled from this report only."""
    from app.cases.forms import SafetyCaseForm
    from app.cases.routes import _populate_extracted_case_form, _set_country_choices

    proposal = _proposal_or_404(proposal_id)
    if proposal["status"] == "Created" and proposal["created_case_id"]:
        flash("A case was already created from this report.", "info")
        return redirect(url_for("cases.case_detail", case_id=proposal["created_case_id"]))
    if proposal["status"] != "Proposed":
        flash(f"This report is {proposal['status'].lower()}; it cannot become a case.", "error")
        return _review(proposal["document_id"], f"#report-{proposal_id}")
    if proposal["extraction_status"] != "Done":
        flash("The facts for this report are not extracted yet (or extraction failed). Wait, or read the document again.", "warning")
        return _review(proposal["document_id"], f"#report-{proposal_id}")
    document = intake.get_document(proposal["document_id"])
    form = SafetyCaseForm()
    countries = _set_country_choices(form)
    uncertain = _populate_extracted_case_form(form, intake.form_values(proposal), countries)
    if not form.icsr_case_id.data:
        form.icsr_case_id.data = intake.suggest_case_number()
    return render_template(
        "cases/create_case.html",
        form=form,
        editing=False,
        extraction_filename=document["original_filename"],
        uncertain_fields=uncertain,
        intake_proposal=proposal,
        intake_document=document,
    )
