from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.security import login_required


bp = Blueprint("attachments", __name__, url_prefix="/records")


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
}


RECORD_CONFIG = {
    "case": {
        "label": "Safety case",
        "table": "pv.safety_cases",
        "id_column": "case_id",
        "endpoint": "cases.case_detail",
        "endpoint_id": "case_id",
    },
    "complaint": {
        "label": "Product complaint",
        "table": "pv.product_complaints",
        "id_column": "complaint_id",
        "endpoint": "complaints.complaint_detail",
        "endpoint_id": "complaint_id",
    },
    "signal": {
        "label": "Safety signal",
        "table": "pv.safety_signals",
        "id_column": "signal_id",
        "endpoint": "signals.signal_detail",
        "endpoint_id": "signal_id",
    },
    "psur": {
        "label": "PSUR",
        "table": "pv.psur_reports",
        "id_column": "psur_id",
        "endpoint": "psur.psur_detail",
        "endpoint_id": "psur_id",
    },
}


def get_record_config(record_type):
    config = RECORD_CONFIG.get(record_type)

    if not config:
        abort(404)

    return config


def record_exists(config, record_id):
    record = query_one(
        f"""
        SELECT {config["id_column"]}
        FROM {config["table"]}
        WHERE {config["id_column"]} = %s
        """,
        (record_id,),
    )

    if not record:
        abort(404)


def record_detail_url(record_type, record_id):
    config = get_record_config(record_type)

    return url_for(
        config["endpoint"],
        **{config["endpoint_id"]: record_id},
    )


@bp.route(
    "/<string:record_type>/<int:record_id>/attachments",
    methods=["GET", "POST"],
)
@login_required
def manage_attachments(record_type, record_id):
    config = get_record_config(record_type)
    record_exists(config, record_id)

    if request.method == "POST":
        uploaded_file = request.files.get("attachment")

        if not uploaded_file or not uploaded_file.filename:
            flash("Choose a file before uploading.", "error")
            return redirect(
                url_for(
                    "attachments.manage_attachments",
                    record_type=record_type,
                    record_id=record_id,
                )
            )

        original_filename = secure_filename(uploaded_file.filename)

        if not original_filename:
            flash("The file name is not valid.", "error")
            return redirect(
                url_for(
                    "attachments.manage_attachments",
                    record_type=record_type,
                    record_id=record_id,
                )
            )

        extension = Path(original_filename).suffix.lower()

        if extension not in ALLOWED_EXTENSIONS:
            flash(
                "Unsupported file type. Use PDF, Word, Excel, CSV, text or image files.",
                "error",
            )
            return redirect(
                url_for(
                    "attachments.manage_attachments",
                    record_type=record_type,
                    record_id=record_id,
                )
            )

        stored_filename = f"{uuid4().hex}{extension}"

        destination = (
            current_app.config["UPLOAD_ROOT"]
            / "attachments"
            / record_type
            / str(record_id)
        )
        destination.mkdir(parents=True, exist_ok=True)

        output_path = destination / stored_filename
        uploaded_file.save(output_path)

        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.record_attachments (
                    record_type,
                    record_id,
                    original_filename,
                    stored_filename,
                    content_type,
                    file_size_bytes,
                    uploaded_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_type,
                    record_id,
                    original_filename,
                    stored_filename,
                    uploaded_file.content_type,
                    output_path.stat().st_size,
                    session["user_id"],
                ),
            )

        write_audit_log(
            record_type=record_type,
            record_id=record_id,
            action="Attachment uploaded",
            details=f"Uploaded file: {original_filename}",
            actor_user_id=session["user_id"],
        )

        flash("Attachment uploaded successfully.", "success")

        return redirect(
            url_for(
                "attachments.manage_attachments",
                record_type=record_type,
                record_id=record_id,
            )
        )

    attachments = query_all(
        """
        SELECT
            a.*,
            u.full_name AS uploaded_by_name
        FROM pv.record_attachments AS a
        LEFT JOIN pv.users AS u ON u.user_id = a.uploaded_by
        WHERE a.record_type = %s
          AND a.record_id = %s
        ORDER BY a.uploaded_at DESC
        """,
        (record_type, record_id),
    )

    return render_template(
        "attachments/manage_attachments.html",
        attachments=attachments,
        record_type=record_type,
        record_id=record_id,
        record_label=config["label"],
        back_url=record_detail_url(record_type, record_id),
    )


@bp.get("/attachments/<int:attachment_id>/download")
@login_required
def download_attachment(attachment_id):
    attachment = query_one(
        """
        SELECT
            record_type,
            record_id,
            original_filename,
            stored_filename
        FROM pv.record_attachments
        WHERE attachment_id = %s
        """,
        (attachment_id,),
    )

    if not attachment:
        abort(404)

    file_path = (
        current_app.config["UPLOAD_ROOT"]
        / "attachments"
        / attachment["record_type"]
        / str(attachment["record_id"])
        / attachment["stored_filename"]
    )

    if not file_path.is_file():
        abort(404)

    return send_file(
        file_path,
        as_attachment=True,
        download_name=attachment["original_filename"],
    )