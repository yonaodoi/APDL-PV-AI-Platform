"""Administration › Terminology: dictionary imports, coding rules and the local terminology."""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from app.audit import write_audit_log
from app.security import roles_required
from app.services import coding, terminology

bp = Blueprint("terminology", __name__, url_prefix="/administration/terminology")
ADMIN = ("System Administrator",)
REVIEWERS = ("System Administrator", "QPPV", "Deputy QPPV", "Group Head RA & Quality")


def _proposed_counts():
    try:
        return {
            "event": len(terminology.local_terms("event", "Proposed")),
            "product": len(terminology.local_terms("product", "Proposed")),
        }
    except Exception:
        terminology._rollback()
        return {"event": 0, "product": 0}


@bp.get("/")
@roles_required(*REVIEWERS)
def overview():
    return render_template(
        "administration/terminology.html",
        dictionaries={k: terminology.dictionary_status(k) for k in ("MedDRA", "WHODrug")},
        imports=terminology.import_history(),
        rules=coding.load_rules(),
        proposed=_proposed_counts(),
        messages=coding.MESSAGES,
        is_admin=session.get("role") in ADMIN,
        max_mb=(current_app.config.get("MAX_CONTENT_LENGTH") or 0) // (1024 * 1024),
    )


@bp.post("/meddra")
@roles_required(*ADMIN)
def import_meddra():
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Nothing imported: choose the MedDRA release zip or the files llt.asc, pt.asc, soc.asc and mdhier.asc.", "error")
        return redirect(url_for("terminology.overview"))
    uploads = [(f.filename, f.read()) for f in files]
    try:
        version, llts, pts = terminology.import_meddra(
            uploads, request.form.get("version"), session["user_id"],
            filenames=", ".join(name for name, _ in uploads), language=(request.form.get("language") or "English")[:20],
        )
    except terminology.DictionaryImportError as error:
        flash(f"MedDRA not imported: {error}", "error")
        return redirect(url_for("terminology.overview"))
    except Exception:
        current_app.logger.exception("MedDRA import failed")
        terminology._rollback()
        flash("MedDRA not imported: the files could not be loaded. Nothing was changed.", "error")
        return redirect(url_for("terminology.overview"))
    write_audit_log("terminology", 0, "MedDRA imported", session["user_id"], f"Version {version}: {llts} LLTs, {pts} PTs.")
    flash(f"MedDRA {version} imported: {llts:,} lowest level terms and {pts:,} preferred terms. "
          "New coding uses this version; existing codings keep the version they were coded with.", "success")
    return redirect(url_for("terminology.overview"))


@bp.post("/whodrug")
@roles_required(*ADMIN)
def import_whodrug():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Nothing imported: choose the WHODrug extract (CSV, TSV or Excel).", "error")
        return redirect(url_for("terminology.overview"))
    try:
        version, count = terminology.import_whodrug(upload.filename, upload.read(), request.form.get("version"), session["user_id"])
    except (terminology.DictionaryImportError, ValueError) as error:
        flash(f"WHODrug not imported: {error}", "error")
        return redirect(url_for("terminology.overview"))
    except Exception:
        current_app.logger.exception("WHODrug import failed")
        terminology._rollback()
        flash("WHODrug not imported: the file could not be loaded. Nothing was changed.", "error")
        return redirect(url_for("terminology.overview"))
    write_audit_log("terminology", 0, "WHODrug imported", session["user_id"], f"Release {version}: {count} records.")
    flash(f"WHODrug {version} imported: {count:,} drug names.", "success")
    return redirect(url_for("terminology.overview"))


@bp.post("/rules")
@roles_required(*ADMIN)
def save_rules():
    rules = coding.save_rules(request.form, session["user_id"])
    write_audit_log("terminology", 0, "Coding rules changed", session["user_id"],
                    ", ".join(f"{k}={'on' if v else 'off'}" for k, v in rules.items()))
    flash("Coding rules saved. They apply the next time coding runs.", "success")
    return redirect(url_for("terminology.overview") + "#rules")


@bp.get("/local/<kind>")
@roles_required(*REVIEWERS)
def local_terms(kind):
    if kind not in ("event", "product"):
        abort(404)
    status = request.args.get("status") or ""
    if status not in ("", "Proposed", "Approved", "Rejected"):
        status = ""
    rows = terminology.local_terms(kind, status or None)
    return render_template(
        "administration/local_terms.html",
        kind=kind,
        rows=rows,
        status=status,
        proposed=_proposed_counts(),
        dictionary="MedDRA" if kind == "event" else "WHODrug",
        dictionary_on=bool(terminology.active_version("MedDRA" if kind == "event" else "WHODrug")),
    )


@bp.post("/local/<kind>/<int:local_id>")
@roles_required(*REVIEWERS)
def review_local(kind, local_id):
    if kind not in ("event", "product"):
        abort(404)
    decision = request.form.get("decision") or ""
    if decision not in ("Approved", "Rejected", ""):
        abort(400)
    mapping = None
    code = (request.form.get("map_code") or "").strip()
    if code:
        found = terminology.meddra_by_code(code) if kind == "event" else terminology.whodrug_by_code(code)
        if not found:
            flash(f"Not saved: {code} is not in the imported {'MedDRA' if kind == 'event' else 'WHODrug'} dictionary.", "error")
            return redirect(url_for("terminology.local_terms", kind=kind) + f"#term-{local_id}")
        mapping = {"dictionary": found["dictionary"], "code": found["code"], "term": found["term"], "version": found["version"]}
    if not decision and not mapping:
        flash("Nothing to save: approve, reject or enter an official code to map to.", "info")
        return redirect(url_for("terminology.local_terms", kind=kind) + f"#term-{local_id}")
    row = terminology.review_local_term(kind, local_id, decision or None, session["user_id"], mapping)
    if not row:
        abort(404)
    name = row.get("preferred_term") or row.get("name")
    details = (f"{decision} " if decision else "") + (f"mapped to {mapping['dictionary']} {mapping['code']} {mapping['term']}" if mapping else "")
    write_audit_log("terminology", local_id, f"Local {kind} term reviewed", session["user_id"], f"{row['local_code']} {name}: {details.strip()}")
    flash(f"{row['local_code']} “{name}”: {details.strip()}." + (
        " Case codings that use it now count as coded." if decision == "Approved" else
        " Case codings that used it are back to Needs review." if decision == "Rejected" else ""), "success")
    return redirect(url_for("terminology.local_terms", kind=kind, status=request.args.get("status") or None) + f"#term-{local_id}")
