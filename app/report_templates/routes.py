"""Report templates: upload, check, map, test-print and activate."""

from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from app.audit import write_audit_log
from app.security import roles_required
from app.services.report_fields import REPORT_TYPES, sample_context

bp = Blueprint("report_templates", __name__, url_prefix="/report-templates")

TEMPLATE_ROLES = ("System Administrator", "QPPV", "Deputy QPPV", "Group Head RA & Quality")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _template_or_404(template_id):
    from app.services.report_templates import get_template

    row = get_template(template_id)
    if not row:
        abort(404)
    return row


def _audit(template, action, details):
    write_audit_log(
        record_type="report_template",
        record_id=template["template_id"],
        action=action,
        details=f"{REPORT_TYPES[template['report_type']]['label']} – “{template['name']}”"
                + (f" v{template['version']}" if template.get("version") else "") + f": {details}",
        actor_user_id=session["user_id"],
    )


@bp.get("/")
@roles_required(*TEMPLATE_ROLES)
def template_list():
    from app.services.report_templates import list_templates

    rows = list_templates()
    by_type = {key: {"active": None, "others": []} for key in REPORT_TYPES}
    for row in rows:
        group = by_type.get(row["report_type"])
        if group is None:
            continue
        if row["status"] == "Active":
            group["active"] = row
        else:
            group["others"].append(row)
    return render_template("report_templates/template_list.html", report_types=REPORT_TYPES, by_type=by_type)


@bp.post("/upload")
@roles_required(*TEMPLATE_ROLES)
def upload_template():
    from werkzeug.utils import secure_filename

    from app.services.report_templates import MAX_TEMPLATE_BYTES, save_template

    report_type = request.form.get("report_type", "")
    back = url_for("report_templates.template_list") + f"#type-{report_type}"
    if report_type not in REPORT_TYPES:
        flash("Choose which report the template is for.", "error")
        return redirect(url_for("report_templates.template_list"))
    upload = request.files.get("template_file")
    filename = secure_filename(upload.filename) if upload and upload.filename else ""
    if not filename or Path(filename).suffix.lower() != ".docx":
        flash("Upload a Word document (.docx). Older .doc files: open in Word and “Save as” .docx first.", "error")
        return redirect(back)
    data = upload.read(MAX_TEMPLATE_BYTES + 1)
    if len(data) > MAX_TEMPLATE_BYTES:
        flash("The file is larger than 15 MB.", "error")
        return redirect(back)
    name = " ".join((request.form.get("name") or Path(filename).stem).split())[:200]
    version = " ".join((request.form.get("version") or "").split())[:40]
    mode = request.form.get("mode", "markers")

    if mode == "ai":
        from app.services.template_mapping import propose_mapping

        try:
            placements, _ = propose_mapping(report_type, data)
        except Exception as error:
            flash(f"The AI could not map this form ({error}). You can add markers yourself using the marker guide.", "error")
            return redirect(back)
        if not placements:
            flash("The AI found no labels it could match to report fields. Add markers yourself using the marker guide.", "error")
            return redirect(back)
        from app.services.report_templates import store_file

        source_name = store_file(data)
        template_id = save_template(
            report_type, name, version, filename, data, session["user_id"],
            source="ai_mapped", mapping=placements, source_filename=source_name,
        )
        template = _template_or_404(template_id)
        _audit(template, "Report template uploaded", f"AI proposed {len(placements)} field placement(s) for review.")
        flash("The AI has suggested where each field goes. Check the list, correct anything, then confirm.", "info")
        return redirect(url_for("report_templates.review_mapping", template_id=template_id))

    template_id = save_template(report_type, name, version, filename, data, session["user_id"])
    template = _template_or_404(template_id)
    _audit(template, "Report template uploaded",
           "passed the test fill" if template["check_passed"] else "has problems to fix")
    if template["check_passed"]:
        flash("Template uploaded and test-filled without problems. Download the test output to check it, then make it active.", "success")
    else:
        flash("Template uploaded, but it has problems listed below. Fix them in Word and upload it again.", "error")
    return redirect(url_for("report_templates.template_detail", template_id=template_id))


@bp.get("/<int:template_id>")
@roles_required(*TEMPLATE_ROLES)
def template_detail(template_id):
    template = _template_or_404(template_id)
    if template["source"] == "ai_mapped" and not template["mapping_applied"]:
        return redirect(url_for("report_templates.review_mapping", template_id=template_id))
    return render_template(
        "report_templates/template_detail.html",
        template=template,
        spec=REPORT_TYPES[template["report_type"]],
    )


@bp.route("/<int:template_id>/mapping", methods=["GET", "POST"])
@roles_required(*TEMPLATE_ROLES)
def review_mapping(template_id):
    from app.services.report_templates import apply_mapping

    template = _template_or_404(template_id)
    spec = REPORT_TYPES[template["report_type"]]
    placements = template.get("mapping") or []
    if request.method == "POST":
        chosen = []
        for index, item in enumerate(placements):
            if request.form.get(f"keep_{index}") != "on":
                continue
            field = request.form.get(f"field_{index}") or item["field"]
            how = request.form.get(f"how_{index}") or item.get("how", "after")
            chosen.append({**item, "field": field, "how": how})
        if not chosen:
            flash("Keep at least one field, or delete this draft.", "error")
            return redirect(url_for("report_templates.review_mapping", template_id=template_id))
        result = apply_mapping(template_id, chosen, session["user_id"])
        _audit(template, "Report template mapping confirmed",
               f"{len(chosen)} field(s) placed; " + ("passed the test fill." if result["passed"] else "has problems."))
        flash("Mapping confirmed and test-filled. Download the test output to check it, then make it active."
              if result["passed"] else "Mapping saved, but the test fill found problems listed below.",
              "success" if result["passed"] else "error")
        return redirect(url_for("report_templates.template_detail", template_id=template_id))
    field_choices = list(spec["fields"].items()) + [
        (f"{list_name}.{sub}", f"{info['label']} – {label}")
        for list_name, info in spec["lists"].items() for sub, label in info["fields"].items()
    ]
    return render_template(
        "report_templates/review_mapping.html",
        template=template, spec=spec, placements=placements, field_choices=field_choices,
    )


@bp.get("/<int:template_id>/test.docx")
@roles_required(*TEMPLATE_ROLES)
def test_output(template_id):
    from app.services.docx_fill import fill_template
    from app.services.report_templates import read_template

    template = _template_or_404(template_id)
    try:
        data = fill_template(read_template(template), sample_context(template["report_type"]))
    except Exception as error:
        flash(f"The test fill failed: {error}", "error")
        return redirect(url_for("report_templates.template_detail", template_id=template_id))
    return send_file(BytesIO(data), mimetype=DOCX_MIME, as_attachment=True,
                     download_name=f"TEST - {Path(template['original_filename']).stem}.docx")


@bp.get("/<int:template_id>/file.docx")
@roles_required(*TEMPLATE_ROLES)
def download_template(template_id):
    from app.services.report_templates import read_template

    template = _template_or_404(template_id)
    original = request.args.get("original") == "1"
    return send_file(BytesIO(read_template(template, original=original)), mimetype=DOCX_MIME, as_attachment=True,
                     download_name=("" if original else "MARKED - ") + template["original_filename"])


@bp.post("/<int:template_id>/activate")
@roles_required(*TEMPLATE_ROLES)
def activate_template(template_id):
    from app.services.report_templates import activate

    template = _template_or_404(template_id)
    try:
        activate(template_id, session["user_id"])
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("report_templates.template_detail", template_id=template_id))
    _audit(template, "Report template made active", "now used for every new print of this report.")
    flash(f"“{template['name']}” is now used for every {REPORT_TYPES[template['report_type']]['label']}.", "success")
    return redirect(url_for("report_templates.template_list") + f"#type-{template['report_type']}")


@bp.post("/<int:template_id>/retire")
@roles_required(*TEMPLATE_ROLES)
def retire_template(template_id):
    from app.services.report_templates import retire

    template = _template_or_404(template_id)
    retire(template_id)
    _audit(template, "Report template made historic", "the built-in layout is used until another template is made active.")
    flash("Template made historic. The standard layout is used until another template is made active.", "success")
    return redirect(url_for("report_templates.template_list") + f"#type-{template['report_type']}")


@bp.post("/<int:template_id>/delete")
@roles_required(*TEMPLATE_ROLES)
def delete_template(template_id):
    from app.services.report_templates import delete_draft

    template = _template_or_404(template_id)
    if delete_draft(template_id):
        _audit(template, "Report template draft deleted", "draft removed.")
        flash("Draft deleted.", "success")
    else:
        flash("Only drafts can be deleted. Make it historic instead.", "error")
    return redirect(url_for("report_templates.template_list") + f"#type-{template['report_type']}")


@bp.get("/guide/<report_type>")
@roles_required(*TEMPLATE_ROLES)
def marker_guide(report_type):
    if report_type not in REPORT_TYPES:
        abort(404)
    return render_template("report_templates/marker_guide.html", report_type=report_type,
                           spec=REPORT_TYPES[report_type], report_types=REPORT_TYPES)
