"""Administration › Licences & integrations (System Administrator only)."""

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from app.audit import write_audit_log
from app.security import roles_required
from app.services import integrations as svc
from app.services.secrets_store import SecretError

bp = Blueprint("integrations", __name__, url_prefix="/administration/integrations")
ADMIN = ("System Administrator",)


@bp.get("/")
@roles_required(*ADMIN)
def integration_list():
    from app.services.llm import effective_config
    from app.services.terminology import dictionary_status

    rows = [svc.public_view(r) for r in svc.list_integrations()]
    spent, calls = svc.month_spend()
    current = effective_config()
    return render_template(
        "administration/integrations.html",
        rows=rows,
        kinds=svc.KINDS,
        usage=svc.usage_summary(),
        spent=spent,
        calls=calls,
        ai_now={"provider": current["provider"], "model": current["model"], "source": current["source"],
                "limit": current.get("monthly_limit"), "currency": current.get("currency")},
        dictionaries={k: dictionary_status(k) for k in ("MedDRA", "WHODrug")},
    )


@bp.route("/new/<kind>", methods=["GET", "POST"])
@roles_required(*ADMIN)
def new_integration(kind):
    if kind not in svc.KINDS:
        abort(404)
    return _form(kind, None)


@bp.route("/<int:integration_id>", methods=["GET", "POST"])
@roles_required(*ADMIN)
def edit_integration(integration_id):
    row = svc.get_integration(integration_id)
    if not row:
        abort(404)
    return _form(row["kind"], row)


def _form(kind, row):
    if request.method == "POST":
        values = {k: request.form.get(k, "") for k in svc.FIELDS}
        settings = {}
        if kind == "AI provider":
            values["provider"] = request.form.get("provider", "anthropic")
            settings = {
                "timeout_seconds": _int(request.form.get("timeout_seconds"), 180, 10, 1800),
                "max_retries": _int(request.form.get("max_retries"), 2, 0, 5),
                "monthly_limit": request.form.get("monthly_limit") or None,
                "currency": (request.form.get("currency") or "USD").upper()[:3],
                "price_input_per_million": request.form.get("price_input_per_million") or None,
                "price_output_per_million": request.form.get("price_output_per_million") or None,
                "use_for_documents": request.form.get("use_for_documents") == "on",
            }
        try:
            new_id, changes = svc.save_integration(
                kind, values, settings, (request.form.get("credential") or "").strip(),
                request.form.get("clear_credential") == "on", session["user_id"],
                integration_id=row["integration_id"] if row else None,
            )
        except (ValueError, SecretError) as error:
            flash(f"Nothing was saved. {error}", "error")
            return render_template("administration/integration_form.html", kind=kind,
                                   row={**(svc.public_view(row) if row else {}), **values, "settings": settings},
                                   providers=svc.AI_PROVIDERS, defaults=svc.DEFAULT_AI_SETTINGS)
        write_audit_log(
            record_type="settings", record_id=new_id,
            action=f"{kind} integration {'changed' if row else 'added'}",
            details=f"{values.get('name')}: " + ("; ".join(changes) or "no changes"),   # never the credential
            actor_user_id=session["user_id"],
        )
        flash(f"{values.get('name')} saved. Run the connection test to check it works.", "success")
        return redirect(url_for("integrations.integration_list") + f"#integration-{new_id}")
    return render_template("administration/integration_form.html", kind=kind,
                           row=svc.public_view(row) if row else {"settings": dict(svc.DEFAULT_AI_SETTINGS)},
                           providers=svc.AI_PROVIDERS, defaults=svc.DEFAULT_AI_SETTINGS)


def _int(value, default, low, high):
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


@bp.post("/<int:integration_id>/test")
@roles_required(*ADMIN)
def test_integration(integration_id):
    try:
        ok, message = svc.test_connection(integration_id)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("integrations.integration_list"))
    write_audit_log(record_type="settings", record_id=integration_id, action="Integration connection tested",
                    details=("Working: " if ok else "Failed: ") + message, actor_user_id=session["user_id"])
    flash(("Connection test passed. " if ok else "Connection test failed. ") + message, "success" if ok else "error")
    return redirect(url_for("integrations.integration_list") + f"#integration-{integration_id}")


@bp.post("/<int:integration_id>/enable")
@roles_required(*ADMIN)
def toggle_integration(integration_id):
    enable = request.form.get("enable") == "1"
    try:
        svc.set_enabled(integration_id, enable, session["user_id"])
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("integrations.integration_list") + f"#integration-{integration_id}")
    write_audit_log(record_type="settings", record_id=integration_id,
                    action="Integration switched on" if enable else "Integration switched off",
                    details=None, actor_user_id=session["user_id"])
    flash("Switched on." if enable else "Switched off.", "success")
    return redirect(url_for("integrations.integration_list") + f"#integration-{integration_id}")
