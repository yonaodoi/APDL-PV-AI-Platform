"""Case › E2B XML export (E2B(R3) and E2B(R2)). Files are generated and
downloaded here; nothing is transmitted to any regulator or partner."""

from flask import Response, abort, flash, redirect, render_template, request, session, url_for

from app.cases.routes import bp
from app.db import query_one, transaction
from app.security import login_required
from app.services import e2b


def _case(case_id):
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    if not case:
        abort(404)
    return case


def issue_link(case_id, issue):
    field = issue.get("field") or ""
    if field.startswith("coding:"):
        return url_for("cases.case_coding", case_id=case_id) + "#item-" + field.split(":", 1)[1]
    if field == "coding":
        return url_for("cases.case_coding", case_id=case_id)
    if field == "settings":
        return url_for("e2b_admin.settings") if session.get("role") == "System Administrator" else None
    if field == "validity":
        return url_for("cases.case_validity", case_id=case_id)
    if field == "narrative":
        return url_for("cases.case_narrative", case_id=case_id)
    if field == "approval":
        return url_for("cases.case_detail", case_id=case_id) + "#approval"
    if field == "file" or not field:
        return None
    return url_for("cases.icsr_form", case_id=case_id, focus=field, why=issue.get("message"))


@bp.get("/<int:case_id>/e2b")
@login_required
def case_e2b(case_id):
    case = _case(case_id)
    settings = e2b.load_settings()
    version = request.args.get("version") or "R3"
    if version not in e2b.VERSIONS:
        version = "R3"
    recipient_key = request.args.get("recipient") or (settings["recipients"][0]["key"] if settings["recipients"] else "")
    issues, _, cfg = e2b.preview(case_id, version, recipient_key)
    for issue in issues:
        issue["link"] = issue_link(case_id, issue)
    return render_template(
        "cases/case_e2b.html",
        case=case,
        settings=settings,
        version=version,
        recipient_key=recipient_key,
        recipient_cfg=cfg,
        issues=issues,
        errors=[i for i in issues if i["level"] == "error"],
        warnings=[i for i in issues if i["level"] == "warning"],
        exports=e2b.exports(case_id),
        schemas=e2b.schema_files(),
    )


@bp.post("/<int:case_id>/e2b/generate")
@login_required
def generate_e2b(case_id):
    _case(case_id)
    version = request.form.get("version") or "R3"
    recipient_key = request.form.get("recipient") or ""
    try:
        export = e2b.create_export(case_id, version, recipient_key, session["user_id"])
    except e2b.ExportError as error:
        flash(f"No file was made: {error}", "error")
        return redirect(url_for("cases.case_e2b", case_id=case_id, version=version, recipient=recipient_key))
    errors = len([i for i in export["issues"] if i["level"] == "error"])
    if export["status"] == "Draft":
        flash(f"E2B({version}) version {export['export_version']} saved as a DRAFT: {errors} problem(s) must be corrected "
              "before it can be used for submission. Each is listed below with a link to the field.", "warning")
    elif export["status"] == "Validated":
        flash(f"E2B({version}) version {export['export_version']} passed all checks, including the official schema "
              f"({export['schema_used']}). It is ready to submit through your usual gateway; nothing has been sent.", "success")
    else:
        flash(f"E2B({version}) version {export['export_version']} passed the built-in checks. Install the official ICH "
              "schema to also validate the file structure (see E2B settings). Nothing has been sent.", "success")
    return redirect(url_for("cases.case_e2b", case_id=case_id, version=version, recipient=recipient_key) + "#exports")


@bp.get("/<int:case_id>/e2b/<int:export_id>/download")
@login_required
def download_e2b(case_id, export_id):
    case = _case(case_id)
    export = e2b.get_export(export_id)
    if not export or export["case_id"] != case_id:
        abort(404)
    with transaction() as cursor:
        cursor.execute("UPDATE pv.e2b_exports SET downloaded_count = downloaded_count + 1 WHERE export_id = %s", (export_id,))
        cursor.execute(
            "INSERT INTO pv.case_audit_log (case_id, action, details, performed_by) VALUES (%s, %s, %s, %s)",
            (case_id, "E2B file downloaded",
             f"E2B({export['e2b_version']}) export version {export['export_version']} ({export['status']}) downloaded.",
             session["user_id"]),
        )
    name = e2b.file_name(export, case["case_number"])
    return Response(export["xml_text"], mimetype="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
