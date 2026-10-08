import re
from pathlib import Path
from uuid import uuid4

from docx import Document
from pypdf import PdfReader
from app.services.rsi_lookup import automatic_dailymed_assessment
from app.services.ai_case_assessment import (
    generate_rsi_assessment_explanation,
)
from app.services.rsi_extraction import (
    extract_reference_document_text,
)

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    send_file,
    session,
    url_for,
    request,
)
from werkzeug.utils import secure_filename

from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.rsi.forms import (
    CaseSafetyAssessmentForm,
    ReferenceSafetyInformationForm,
)
from app.security import login_required, roles_required
from app.services.case_listedness import (
    listedness_message,
    reassess_product_cases,
    run_automatic_listedness,
)
from app.services.rsi_assessment import (
    assess_event_against_rsi,
    choose_rsi_document,
    rsi_document_title,
)
from app.services.rsi_versions import (
    duplicate_current_groups,
    make_current,
    rsi_group_label,
)
from app.services.signal_detection import (
    screen_case_for_signals,
    summarise_screening,
)


bp = Blueprint("rsi", __name__, url_prefix="/reference-safety")


REVIEWER_ROLES = (
    "System Administrator",
    "QPPV",
    "Deputy QPPV",
    "PV Officer",
    "Medical Reviewer",
    "Quality Reviewer",
    "Regulatory Affairs Officer",
)

ALLOWED_RSI_EXTENSIONS = {".pdf", ".doc", ".docx"}


def rsi_file_path(stored_filename):
    return (
        current_app.config["UPLOAD_ROOT"]
        / "reference_safety_information"
        / stored_filename
    )


@bp.get("/")
@roles_required(*REVIEWER_ROLES)
def rsi_list():
    documents = query_all(
        """
        SELECT
            rsi.*,
            users.full_name AS uploaded_by_name
        FROM pv.reference_safety_information AS rsi
        LEFT JOIN pv.users AS users
            ON users.user_id = rsi.uploaded_by
        ORDER BY
            rsi.is_current DESC,
            rsi.product_name,
            rsi.created_at DESC
        """
    )

    return render_template(
        "rsi/rsi_list.html",
        documents=documents,
        duplicate_groups=duplicate_current_groups(documents),
    )


def _load_rsi_or_404(rsi_id):
    document = query_one(
        """
        SELECT rsi_id, product_name, document_type, market,
               document_version, is_current
        FROM pv.reference_safety_information
        WHERE rsi_id = %s
        """,
        (rsi_id,),
    )
    if not document:
        abort(404)
    return document


def _describe_rsi(document):
    version = document.get("document_version")
    return rsi_group_label(document) + (f" (version {version})" if version else "")


@bp.post("/<int:rsi_id>/make-current")
@roles_required(*REVIEWER_ROLES)
def make_rsi_current(rsi_id):
    document = _load_rsi_or_404(rsi_id)
    with transaction() as cursor:
        superseded = make_current(cursor, document)

    write_audit_log(
        record_type="reference_safety_information",
        record_id=rsi_id,
        action="Marked as current reference",
        details=(
            f"{_describe_rsi(document)}. "
            f"{superseded} other document(s) marked historic."
        ),
        actor_user_id=session["user_id"],
    )
    _flash_rechecked(_recheck_cases_for_document(rsi_id))
    flash(
        "Document marked as the current reference"
        + (
            f"; {superseded} other document(s) marked historic."
            if superseded
            else "."
        ),
        "success",
    )
    return redirect(url_for("rsi.rsi_list"))


@bp.post("/<int:rsi_id>/make-historic")
@roles_required(*REVIEWER_ROLES)
def make_rsi_historic(rsi_id):
    document = _load_rsi_or_404(rsi_id)
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.reference_safety_information
            SET is_current = FALSE
            WHERE rsi_id = %s
            """,
            (rsi_id,),
        )

    write_audit_log(
        record_type="reference_safety_information",
        record_id=rsi_id,
        action="Marked as historic reference",
        details=f"{_describe_rsi(document)}.",
        actor_user_id=session["user_id"],
    )
    flash(
        "Document marked historic. It will no longer be used for "
        "listedness review.",
        "success",
    )
    _flash_rechecked(_recheck_cases_for_document(rsi_id))
    return redirect(url_for("rsi.rsi_list"))


@bp.route("/new", methods=["GET", "POST"])
@roles_required(*REVIEWER_ROLES)
def create_rsi():
    form = ReferenceSafetyInformationForm()
    return_to = request.args.get(
        "return_to",
        "",
    ).strip()

    if return_to and not return_to.startswith("/"):
        return_to = ""

    if form.validate_on_submit():
        source_file = form.source_file.data
        original_filename = None
        stored_filename = None
        content_type = None
        file_size_bytes = None

        if source_file and source_file.filename:
            original_filename = secure_filename(
                source_file.filename
            )
            extension = Path(original_filename).suffix.lower()

            if extension not in ALLOWED_RSI_EXTENSIONS:
                form.source_file.errors.append(
                    "Upload a PDF, DOC or DOCX reference document."
                )
                return render_template(
                    "rsi/create_rsi.html",
                    form=form,
                )

            stored_filename = f"{uuid4().hex}{extension}"
            output_path = rsi_file_path(stored_filename)
            output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            source_file.save(output_path)

            content_type = source_file.content_type
            file_size_bytes = output_path.stat().st_size

        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.reference_safety_information (
                    product_name,
                    active_substance,
                    reference_product_name,
                    market,
                    document_type,
                    document_version,
                    effective_date,
                    source_url,
                    original_filename,
                    stored_filename,
                    content_type,
                    file_size_bytes,
                    uploaded_by
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                RETURNING rsi_id
                """,
                (
                    form.product_name.data.strip(),
                    form.active_substance.data.strip() or None,
                    form.reference_product_name.data.strip() or None,
                    form.market.data.strip() or None,
                    form.document_type.data,
                    form.document_version.data.strip() or None,
                    form.effective_date.data,
                    form.source_url.data.strip() or None,
                    original_filename,
                    stored_filename,
                    content_type,
                    file_size_bytes,
                    session["user_id"],
                ),
            )
            rsi_id = cursor.fetchone()["rsi_id"]
            superseded = make_current(
                cursor,
                {
                    "rsi_id": rsi_id,
                    "product_name": form.product_name.data.strip(),
                    "document_type": form.document_type.data,
                    "market": form.market.data.strip() or None,
                },
            )
        if superseded:
            flash(
                f"{superseded} older document(s) for the same product, "
                "document type and market were marked historic. This "
                "document is now the current reference.",
                "success",
            )
        if stored_filename:
            try:
                extracted_text = extract_reference_document_text(
                    rsi_file_path(stored_filename)
                )

                with transaction() as cursor:
                    cursor.execute(
                        """
                        UPDATE pv.reference_safety_information
                        SET
                            extracted_text = %s,
                            extraction_status = 'Extracted',
                            extracted_at = CURRENT_TIMESTAMP
                        WHERE rsi_id = %s
                        """,
                        (extracted_text, rsi_id),
                    )

            except Exception:
                current_app.logger.exception(
                    "Reference document text extraction failed for RSI %s",
                    rsi_id,
                )
                with transaction() as cursor:
                    cursor.execute(
                        """
                        UPDATE pv.reference_safety_information
                        SET extraction_status = 'Extraction failed'
                        WHERE rsi_id = %s
                        """,
                        (rsi_id,),
                    )

                flash(
                    "Reference document was saved, but its text "
                    "could not be extracted automatically.",
                    "warning",
                )

        if stored_filename:
            term_count, term_error = extract_terms_for_document(
                rsi_id, stored_filename, session["user_id"], original_filename
            )
            if term_error:
                flash(
                    "Reaction terms could not be extracted automatically: "
                    f"{term_error}",
                    "warning",
                )
            else:
                flash(
                    f"{term_count} reaction term(s) extracted automatically. "
                    "Verify them under Verify terms.",
                    "success",
                )

        write_audit_log(
            record_type="reference_safety_information",
            record_id=rsi_id,
            action="Reference safety information created",
            details=(
                f"Product: {form.product_name.data.strip()}; "
                f"document type: {form.document_type.data}."
            ),
            actor_user_id=session["user_id"],
        )

        flash(
            "Reference safety information saved successfully.",
            "success",
        )
        _flash_rechecked(_recheck_cases_for_document(rsi_id))
        return redirect(
            return_to or url_for("rsi.rsi_list")
        )
    return render_template("rsi/create_rsi.html", form=form)

@bp.post("/<int:rsi_id>/extract")
@roles_required(*REVIEWER_ROLES)
def extract_rsi_text(rsi_id):
    document = query_one(
        """
        SELECT rsi_id, stored_filename
        FROM pv.reference_safety_information
        WHERE rsi_id = %s
        """,
        (rsi_id,),
    )

    if not document:
        abort(404)

    if not document["stored_filename"]:
        flash(
            "No uploaded PDF or DOCX document is available for "
            "text extraction.",
            "error",
        )
        return redirect(url_for("rsi.rsi_list"))

    try:
        extracted_text = extract_reference_document_text(
            rsi_file_path(document["stored_filename"])
        )

        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.reference_safety_information
                SET
                    extracted_text = %s,
                    extraction_status = 'Extracted',
                    extracted_at = CURRENT_TIMESTAMP
                WHERE rsi_id = %s
                """,
                (extracted_text, rsi_id),
            )

        flash(
            "Reference document text extracted successfully.",
            "success",
        )

    except Exception:
        current_app.logger.exception(
            "Reference document text extraction failed for RSI %s",
            rsi_id,
        )
        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.reference_safety_information
                SET extraction_status = 'Extraction failed'
                WHERE rsi_id = %s
                """,
                (rsi_id,),
            )

        flash(
            "The document could not be extracted. Upload a readable "
            "PDF or DOCX version where available.",
            "error",
        )

    return redirect(url_for("rsi.rsi_list"))

@bp.get("/<int:rsi_id>/download")
@roles_required(*REVIEWER_ROLES)
def download_rsi(rsi_id):
    document = query_one(
        """
        SELECT original_filename, stored_filename
        FROM pv.reference_safety_information
        WHERE rsi_id = %s
        """,
        (rsi_id,),
    )

    if not document or not document["stored_filename"]:
        abort(404)

    file_path = rsi_file_path(document["stored_filename"])

    if not file_path.is_file():
        abort(404)

    return send_file(
        file_path,
        as_attachment=True,
        download_name=document["original_filename"],
    )


def _reference_title_for(rsi_id, documents):
    for document in documents:
        if document["rsi_id"] == rsi_id:
            return rsi_document_title(document)
    return None


@bp.route("/cases/<int:case_id>/assessment", methods=["GET", "POST"])
@roles_required(*REVIEWER_ROLES)
def assess_case(case_id):
    case = query_one(
        """
        SELECT
            safety_cases.case_id,
            safety_cases.case_number,
            safety_cases.event_description,
            case_products.product_name
        FROM pv.safety_cases AS safety_cases
        LEFT JOIN pv.case_products AS case_products
            ON case_products.case_id = safety_cases.case_id
        WHERE safety_cases.case_id = %s
        """,
        (case_id,),
    )

    if not case:
        abort(404)

    rsi_documents = query_all(
        """
        SELECT
            rsi_id,
            product_name,
            reference_product_name,
            document_type,
            document_version,
            effective_date,
            market
        FROM pv.reference_safety_information
        WHERE is_current = TRUE
        ORDER BY product_name, created_at DESC
        """
    )

    form = CaseSafetyAssessmentForm()
    form.rsi_id.choices = [(0, "Select reference safety information")] + [
        (
            document["rsi_id"],
            " | ".join(
                value
                for value in (
                    document["product_name"],
                    document["reference_product_name"],
                    document["document_type"],
                    document["market"],
                )
                if value
            ),
        )
        for document in rsi_documents
    ]

    existing_assessment = query_one(
        """
        SELECT *
        FROM pv.case_safety_assessments
        WHERE case_id = %s
        """,
        (case_id,),
    )

    if form.validate_on_submit():
        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.case_safety_assessments (
                    case_id,
                    rsi_id,
                    event_term_assessed,
                    listedness_status,
                    expectedness_status,
                    seriousness_assessment,
                    seriousness_criteria,
                    rsi_evidence,
                    frequency_assessment,
                    frequency_evidence,
                    assessment_rationale,
                    assessed_by,
                    assessment_source,
                    is_provisional,
                    reference_title,
                    assessed_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, 'reviewer', FALSE, %s,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (case_id)
                DO UPDATE SET
                    rsi_id = EXCLUDED.rsi_id,
                    event_term_assessed = EXCLUDED.event_term_assessed,
                    listedness_status = EXCLUDED.listedness_status,
                    expectedness_status = EXCLUDED.expectedness_status,
                    seriousness_assessment = EXCLUDED.seriousness_assessment,
                    seriousness_criteria = EXCLUDED.seriousness_criteria,
                rsi_evidence = EXCLUDED.rsi_evidence,
                frequency_assessment = EXCLUDED.frequency_assessment,
                frequency_evidence = EXCLUDED.frequency_evidence,
                assessment_rationale = EXCLUDED.assessment_rationale,
                    assessed_by = EXCLUDED.assessed_by,
                    assessment_source = 'reviewer',
                    is_provisional = FALSE,
                    reference_title = EXCLUDED.reference_title,
                    assessed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    case_id,
                    form.rsi_id.data,
                    form.event_term_assessed.data.strip(),
                    form.listedness_status.data,
                    form.expectedness_status.data,
                    form.seriousness_assessment.data,
                    form.seriousness_criteria.data.strip() or None,
                    form.rsi_evidence.data.strip() or None,
                    form.frequency_assessment.data,
                    form.frequency_evidence.data.strip() or None,
                    form.assessment_rationale.data.strip() or None,
                    session["user_id"],
                    _reference_title_for(form.rsi_id.data, rsi_documents),
                ),
            )

            cursor.execute(
                """
                INSERT INTO pv.case_audit_log (
                    case_id,
                    action,
                    details,
                    performed_by
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    case_id,
                    "RSI safety assessment saved",
                    (
                        f"Listedness: {form.listedness_status.data}; "
                        f"Expectedness: {form.expectedness_status.data}; "
                        f"Seriousness: {form.seriousness_assessment.data}."
                    ),
                    session["user_id"],
                ),
            )

        write_audit_log(
            record_type="case",
            record_id=case_id,
            action="RSI safety assessment saved",
            details=(
                f"Listedness: {form.listedness_status.data}; "
                f"Expectedness: {form.expectedness_status.data}; "
                f"Seriousness: {form.seriousness_assessment.data}."
            ),
            actor_user_id=session["user_id"],
        )

        flash(
            "Safety assessment saved and confirmed by you. The automatic "
            "check will not change it.",
            "success",
        )
        _flash_signal_screening(case_id)
        return redirect(url_for("cases.case_detail", case_id=case_id))

    if existing_assessment:
        form.rsi_id.data = existing_assessment["rsi_id"]
        form.event_term_assessed.data = existing_assessment["event_term_assessed"]
        form.listedness_status.data = existing_assessment["listedness_status"]
        form.expectedness_status.data = existing_assessment["expectedness_status"]
        form.seriousness_assessment.data = (
            existing_assessment["seriousness_assessment"]
        )
        form.seriousness_criteria.data = (
            existing_assessment["seriousness_criteria"]
        )
        form.rsi_evidence.data = existing_assessment["rsi_evidence"]
        form.frequency_assessment.data = (
            existing_assessment["frequency_assessment"]
            or "Not stated"
        )
        form.frequency_evidence.data = (
            existing_assessment["frequency_evidence"]
        )

        form.assessment_rationale.data = (
            existing_assessment["assessment_rationale"]
        )
    else:
        form.event_term_assessed.data = case["event_description"]

    return render_template(
        "rsi/case_assessment.html",
        form=form,
        case=case,
        existing_assessment=existing_assessment,
    )

REACTION_SECTION_HEADINGS = (
    "adverse reactions",
    "undesirable effects",
    "adverse events",
    "side effects",
)


def extract_document_text(file_path):
    extension = file_path.suffix.lower()

    if extension == ".docx":
        document = Document(file_path)
        parts = [paragraph.text for paragraph in document.paragraphs]

        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        parts.append(cell.text)

        return "\n".join(parts)

    if extension == ".pdf":
        reader = PdfReader(str(file_path))
        return "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    raise ValueError("Only DOCX and text-based PDF documents can be extracted.")


def find_reaction_section(document_text):
    lowered_text = document_text.lower()

    positions = [
        lowered_text.find(heading)
        for heading in REACTION_SECTION_HEADINGS
        if lowered_text.find(heading) != -1
    ]

    if not positions:
        return None

    start_position = min(positions)
    return document_text[start_position:start_position + 12000]


def reaction_candidates(section_text):
    candidates = []

    for raw_line in re.split(r"[\n;•]+", section_text):
        line = re.sub(r"\s+", " ", raw_line).strip()

        if ":" in line:
            label, remainder = line.split(":", 1)

            if any(
                frequency in label.lower()
                for frequency in (
                    "very common",
                    "common",
                    "uncommon",
                    "rare",
                    "very rare",
                    "not known",
                    "frequency",
                )
            ):
                line = remainder.strip()

        for item in re.split(r",(?=\s*[A-Za-z])", line):
            reaction = item.strip(" -–—•:;.")

            if len(reaction) < 3 or len(reaction) > 120:
                continue

            lowered_reaction = reaction.lower()

            if any(
                heading in lowered_reaction
                for heading in REACTION_SECTION_HEADINGS
            ):
                continue

            if any(
                excluded in lowered_reaction
                for excluded in (
                    "frequency cannot",
                    "consult your",
                    "medical advice",
                    "section",
                    "table",
                )
            ):
                continue

            if reaction not in candidates:
                candidates.append(reaction)

    return candidates[:250]


def extract_terms_for_document(rsi_id, stored_filename, actor_user_id, source_name=None):
    """Extract proposed reaction terms from an uploaded document.

    Returns (inserted_count, error_message). Never raises.
    """
    try:
        file_path = rsi_file_path(stored_filename)
        if not file_path.is_file():
            return 0, "The uploaded RSI file could not be found."
        document_text = extract_document_text(file_path)
        section_text = find_reaction_section(document_text)
    except Exception as error:
        current_app.logger.warning(
            "Reaction term extraction failed for RSI %s", rsi_id, exc_info=True
        )
        return 0, f"Reaction extraction could not be completed: {error}"

    if not section_text:
        return 0, (
            "No adverse-reactions or undesirable-effects heading was found. "
            "Review the document manually."
        )

    candidates = reaction_candidates(section_text)
    if not candidates:
        return 0, (
            "A likely reactions section was found, but no reaction terms "
            "could be extracted."
        )

    inserted_count = 0
    with transaction() as cursor:
        for candidate in candidates:
            cursor.execute(
                """
                INSERT INTO pv.rsi_reactions (
                    rsi_id,
                    reaction_term,
                    source_excerpt
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (rsi_id, reaction_term) DO NOTHING
                """,
                (rsi_id, candidate, section_text[:1500]),
            )
            inserted_count += cursor.rowcount

    write_audit_log(
        record_type="reference_safety_information",
        record_id=rsi_id,
        action="RSI reaction terms extracted",
        details=(
            f"Created {inserted_count} proposed reaction term(s)"
            + (f" from {source_name}." if source_name else ".")
        ),
        actor_user_id=actor_user_id,
    )
    return inserted_count, None


def _recheck_cases_for_document(rsi_id):
    """Re-run automatic listedness for cases of this document's product."""
    document = query_one(
        """
        SELECT product_name, active_substance
        FROM pv.reference_safety_information
        WHERE rsi_id = %s
        """,
        (rsi_id,),
    )
    if not document:
        return 0
    return reassess_product_cases(
        [document["product_name"], document.get("active_substance")],
        actor_user_id=session.get("user_id"),
    )


def _flash_rechecked(count):
    if count:
        flash(
            f"Listedness was re-checked automatically on {count} case(s) "
            "for this product.",
            "info",
        )


@bp.post("/<int:rsi_id>/extract-reactions")
@roles_required(*REVIEWER_ROLES)
def extract_reactions(rsi_id):
    document = query_one(
        """
        SELECT
            rsi_id,
            product_name,
            original_filename,
            stored_filename
        FROM pv.reference_safety_information
        WHERE rsi_id = %s
        """,
        (rsi_id,),
    )

    if not document:
        abort(404)

    if not document["stored_filename"]:
        flash(
            "Upload a DOCX or text-based PDF document before extracting reactions.",
            "error",
        )
        return redirect(url_for("rsi.rsi_list"))

    inserted_count, error = extract_terms_for_document(
        rsi_id,
        document["stored_filename"],
        session["user_id"],
        document["original_filename"],
    )
    if error:
        flash(error, "error")
        return redirect(url_for("rsi.rsi_list"))

    flash(
        f"{inserted_count} proposed reaction term(s) extracted. "
        "Verify each term; case listedness results stay provisional until "
        "all terms are reviewed.",
        "success",
    )
    _flash_rechecked(_recheck_cases_for_document(rsi_id))

    return redirect(url_for("rsi.review_reactions", rsi_id=rsi_id))


@bp.get("/<int:rsi_id>/reactions")
@roles_required(*REVIEWER_ROLES)
def review_reactions(rsi_id):
    document = query_one(
        """
        SELECT
            rsi_id,
            product_name,
            reference_product_name,
            document_type,
            document_version
        FROM pv.reference_safety_information
        WHERE rsi_id = %s
        """,
        (rsi_id,),
    )

    if not document:
        abort(404)

    reactions = query_all(
        """
        SELECT *
        FROM pv.rsi_reactions
        WHERE rsi_id = %s
        ORDER BY
            CASE review_status
                WHEN 'Proposed' THEN 1
                WHEN 'Verified' THEN 2
                ELSE 3
            END,
            reaction_term
        """,
        (rsi_id,),
    )

    return render_template(
        "rsi/review_reactions.html",
        document=document,
        reactions=reactions,
    )


def _recheck_when_terms_reviewed(rsi_id):
    remaining = query_one(
        """
        SELECT COUNT(*) AS count
        FROM pv.rsi_reactions
        WHERE rsi_id = %s
          AND review_status = 'Proposed'
        """,
        (rsi_id,),
    )
    if remaining and remaining["count"] == 0:
        flash(
            "All reaction terms for this document have been reviewed.",
            "success",
        )
        _flash_rechecked(_recheck_cases_for_document(rsi_id))


@bp.post("/reactions/<int:reaction_id>/verify")
@roles_required(*REVIEWER_ROLES)
def verify_reaction(reaction_id):
    reaction = query_one(
        """
        SELECT rsi_id
        FROM pv.rsi_reactions
        WHERE reaction_id = %s
        """,
        (reaction_id,),
    )

    if not reaction:
        abort(404)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.rsi_reactions
            SET
                review_status = 'Verified',
                reviewed_by = %s,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE reaction_id = %s
            """,
            (session["user_id"], reaction_id),
        )

    _recheck_when_terms_reviewed(reaction["rsi_id"])

    return redirect(
        url_for(
            "rsi.review_reactions",
            rsi_id=reaction["rsi_id"],
        )
    )


@bp.post("/reactions/<int:reaction_id>/exclude")
@roles_required(*REVIEWER_ROLES)
def exclude_reaction(reaction_id):
    reaction = query_one(
        """
        SELECT rsi_id
        FROM pv.rsi_reactions
        WHERE reaction_id = %s
        """,
        (reaction_id,),
    )

    if not reaction:
        abort(404)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.rsi_reactions
            SET
                review_status = 'Excluded',
                reviewed_by = %s,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE reaction_id = %s
            """,
            (session["user_id"], reaction_id),
        )

    _recheck_when_terms_reviewed(reaction["rsi_id"])

    return redirect(
        url_for(
            "rsi.review_reactions",
            rsi_id=reaction["rsi_id"],
        )
    )

@bp.post("/cases/<int:case_id>/automatic-assessment")
@login_required
def automatic_case_assessment(case_id):
    if not query_one(
        "SELECT case_id FROM pv.safety_cases WHERE case_id = %s", (case_id,)
    ):
        abort(404)

    outcome = run_automatic_listedness(
        case_id, actor_user_id=session["user_id"], force=True
    )
    if outcome and outcome.get("unchanged"):
        flash(
            f"Automatic check re-run: no change ({outcome['listedness_status']} "
            f"/ {outcome['expectedness_status']}).",
            "info",
        )
    else:
        message = listedness_message(outcome)
        if message:
            flash(*message)
    _flash_signal_screening(case_id)
    return redirect(url_for("cases.case_detail", case_id=case_id) + "#listedness")


def _flash_signal_screening(case_id):
    """Re-screen a case for signals after its listedness changes."""
    results, _ = screen_case_for_signals(
        case_id, actor_user_id=session["user_id"]
    )
    message = summarise_screening(results)
    if message:
        flash(message, "warning")

def normalise_rsi_text(value):
    return " ".join(
        re.findall(r"[a-z0-9]+", (value or "").lower())
    )


def automatic_uploaded_rsi_assessment(product_name, event_term):
    """
    Assess an event against the current uploaded RSI for the product.

    Returns None when no current document exists for the product (the
    caller may then try the online label). Returns an "available": False
    result when the chosen document has no extracted reaction terms, rather
    than wrongly concluding the event is not listed.
    """
    documents = query_all(
        """
        SELECT
            rsi_id,
            product_name,
            active_substance,
            document_type,
            document_version,
            effective_date
        FROM pv.reference_safety_information
        WHERE is_current = TRUE
          AND (
              LOWER(product_name) = LOWER(%s)
              OR LOWER(COALESCE(active_substance, '')) = LOWER(%s)
          )
        """,
        (product_name, product_name),
    )

    document = choose_rsi_document(documents)
    if not document:
        return None

    reactions = query_all(
        """
        SELECT
            reaction_term,
            source_excerpt,
            review_status
        FROM pv.rsi_reactions
        WHERE rsi_id = %s
          AND review_status IN ('Proposed', 'Verified')
        ORDER BY
            CASE WHEN review_status = 'Verified' THEN 0 ELSE 1 END,
            reaction_term
        """,
        (document["rsi_id"],),
    )

    result = assess_event_against_rsi(document, reactions, event_term)
    if result["available"]:
        frequency_assessment, frequency_evidence = (
            frequency_from_rsi_matches(result["matches"])
        )
        result["frequency_assessment"] = frequency_assessment
        result["frequency_evidence"] = frequency_evidence
    return result

def frequency_from_rsi_matches(matches):
    """Extract a standard frequency category from matched RSI excerpts."""
    frequency_patterns = (
        ("Very common", r"\bvery common\b"),
        ("Common", r"\bcommon\b"),
        ("Uncommon", r"\buncommon\b"),
        ("Very rare", r"\bvery rare\b"),
        ("Rare", r"\brare\b"),
        (
            "Frequency not known",
            r"\bfrequency (is )?not known\b|\bnot known\b",
        ),
    )

    for match in matches:
        excerpt = match.get("source_excerpt") or ""
        excerpt_lower = excerpt.lower()

        for frequency, pattern in frequency_patterns:
            if re.search(pattern, excerpt_lower):
                return frequency, excerpt

    if matches:
        return (
            "Not stated",
            "A matching RSI reaction was found, but its frequency was not "
            "stated in the extracted source excerpt.",
        )

    return (
        "Not assessable",
        "No matching RSI reaction was identified; frequency cannot be "
        "assessed from the available reference information.",
    )