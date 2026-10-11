"""Company product catalogue: list, add, edit (versioned), bulk import."""

from flask import (
    Blueprint, Response, abort, current_app, flash, jsonify, redirect,
    render_template, request, session, url_for,
)

from app.audit import write_audit_log
from app.db import query_all
from app.security import login_required, roles_required
from app.services import product_catalogue as catalogue
from app.services.tabular_import import ImportFileError

bp = Blueprint("products", __name__, url_prefix="/products")

MANAGE = catalogue.MANAGE_ROLES
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _can_manage():
    return session.get("role") in MANAGE


def _rsi_choices():
    try:
        return query_all(
            """
            SELECT rsi_id, product_name, document_type, document_version, market
            FROM pv.reference_safety_information
            WHERE is_current
            ORDER BY product_name, document_type
            """
        )
    except Exception:
        return []


@bp.get("/")
@login_required
def product_list():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    market = request.args.get("market", "").strip()
    return render_template(
        "products/product_list.html",
        products=catalogue.list_products(q, status, market),
        markets=catalogue.markets(),
        statuses=catalogue.STATUSES,
        q=q, status=status, market=market,
        can_manage=_can_manage(),
        pending_imports=query_all(
            "SELECT batch_id, original_filename, uploaded_at FROM pv.product_import_batches WHERE status = 'Preview' ORDER BY batch_id DESC LIMIT 5"
        ) if _can_manage() else [],
    )


@bp.route("/new", methods=["GET", "POST"])
@roles_required(*MANAGE)
def new_product():
    return _edit(None)


@bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@roles_required(*MANAGE)
def edit_product(product_id):
    return _edit(product_id)


def _edit(product_id):
    product = catalogue.get_product(product_id) if product_id else {}
    if product_id and not product:
        abort(404)
    if request.method == "POST":
        values = {k: request.form.get(k, "") for k, _ in catalogue.FIELDS}
        values["rsi_id"] = request.form.get("rsi_id", "")
        cleaned, problems = catalogue.clean_product(values)
        duplicates = [] if problems else catalogue.find_duplicates(cleaned, exclude_id=product_id)
        same_code = [d for d in duplicates if "same product identifier" in d["reasons"]]
        if same_code:
            problems.append(f"The identifier {cleaned['product_code']} is already used by {same_code[0]['brand_name']}.")
        if duplicates and not same_code and request.form.get("confirm_duplicate") != "1":
            flash("This looks like a product already in the catalogue. Check the match below, then save again to confirm it is different.", "warning")
            return render_template("products/product_form.html", product={**product, **values}, editing=bool(product_id),
                                   duplicates=duplicates, statuses=catalogue.STATUSES, rsi_choices=_rsi_choices(), fields=catalogue.FIELDS)
        if problems:
            for problem in problems:
                flash(f"Nothing was saved. {problem}", "error")
            return render_template("products/product_form.html", product={**product, **values}, editing=bool(product_id),
                                   duplicates=duplicates, statuses=catalogue.STATUSES, rsi_choices=_rsi_choices(), fields=catalogue.FIELDS)
        new_id, version, changes = catalogue.save_product(values, session["user_id"], product_id=product_id,
                                                          change_note=request.form.get("change_note") or None)
        write_audit_log(
            record_type="product", record_id=new_id,
            action="Catalogue product updated" if product_id else "Catalogue product added",
            details=f"{cleaned['product_code']} {cleaned['brand_name']} (version {version}): " + ("; ".join(changes) or "no changes"),
            actor_user_id=session["user_id"],
        )
        if product_id and not changes:
            flash("Nothing had changed, so no new version was saved.", "info")
        else:
            flash(f"{cleaned['brand_name']} saved as version {version}. Existing records keep the version they used.", "success")
        return redirect(url_for("products.product_detail", product_id=new_id))
    return render_template("products/product_form.html", product=product or {"status": "Active"}, editing=bool(product_id),
                           duplicates=[], statuses=catalogue.STATUSES, rsi_choices=_rsi_choices(), fields=catalogue.FIELDS)


@bp.get("/<int:product_id>")
@login_required
def product_detail(product_id):
    product = catalogue.get_product(product_id)
    if not product:
        abort(404)
    usage = {}
    for label, sql in (
        ("Safety cases", "SELECT c.case_id AS id, c.case_number AS number, p.catalogue_version AS version FROM pv.case_products p JOIN pv.safety_cases c ON c.case_id = p.case_id WHERE p.catalogue_product_id = %s ORDER BY c.case_id DESC LIMIT 50"),
        ("Product complaints", "SELECT complaint_id AS id, complaint_number AS number, catalogue_version AS version FROM pv.product_complaints WHERE catalogue_product_id = %s ORDER BY complaint_id DESC LIMIT 50"),
        ("Safety signals", "SELECT signal_id AS id, signal_number AS number, catalogue_version AS version FROM pv.safety_signals WHERE catalogue_product_id = %s ORDER BY signal_id DESC LIMIT 50"),
    ):
        try:
            usage[label] = query_all(sql, (product_id,))
        except Exception:
            usage[label] = []
    return render_template(
        "products/product_detail.html",
        product=product,
        versions=catalogue.product_versions(product_id),
        usage=usage,
        can_manage=_can_manage(),
        rsi=[r for r in _rsi_choices() if r["rsi_id"] == product.get("rsi_id")],
    )


@bp.get("/options.json")
@login_required
def product_options():
    """Products for the pickers on case, complaint and signal forms."""
    return jsonify(catalogue.picker_options(include_inactive=request.args.get("all") == "1"))


# --------------------------------------------------------------------------
# Bulk import
# --------------------------------------------------------------------------

@bp.get("/import/template.csv")
@login_required
def import_template():
    return Response(catalogue.TEMPLATE_CSV, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=product_catalogue_template.csv"})


@bp.post("/import")
@roles_required(*MANAGE)
def upload_import():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose a CSV or Excel (.xlsx) file to import.", "error")
        return redirect(url_for("products.product_list"))
    data = upload.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        flash("The file is larger than 10 MB.", "error")
        return redirect(url_for("products.product_list"))
    try:
        batch_id, existing = catalogue.start_import(upload.filename, data, session["user_id"])
    except ImportFileError as error:
        flash(f"The file could not be read: {error}", "error")
        return redirect(url_for("products.product_list"))
    if existing:
        flash("This file was already uploaded and is waiting for review; the existing preview is shown.", "info")
    return redirect(url_for("products.import_preview", batch_id=batch_id))


@bp.route("/import/<int:batch_id>", methods=["GET", "POST"])
@roles_required(*MANAGE)
def import_preview(batch_id):
    batch = catalogue.get_batch(batch_id)
    if not batch:
        abort(404)
    if request.method == "POST":
        if request.form.get("action") == "discard":
            catalogue.discard_import(batch_id)
            flash("The import was discarded; nothing was changed.", "info")
            return redirect(url_for("products.product_list"))
        decisions = {str(entry["line"]): request.form.get(f"decision-{entry['line']}", "skip") for entry in batch["rows"]}
        try:
            created, updated, skipped = catalogue.apply_import(batch_id, decisions, session["user_id"])
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("products.import_preview", batch_id=batch_id))
        write_audit_log(
            record_type="product", record_id=None, action="Catalogue import applied",
            details=f"{batch['original_filename']} (import {batch_id}): {created} added, {updated} updated, {skipped} skipped.",
            actor_user_id=session["user_id"],
        )
        flash(f"Import applied: {created} added, {updated} updated, {skipped} skipped. Each change is a new version.", "success")
        return redirect(url_for("products.product_list"))
    products = {p["product_id"]: p for p in query_all("SELECT product_id, product_code, brand_name, strength, market FROM pv.product_catalogue")}
    return render_template("products/import_preview.html", batch=batch, products=products)
