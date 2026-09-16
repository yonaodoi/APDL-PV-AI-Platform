from datetime import date
from pathlib import Path
import re

from docx import Document
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from flask import current_app

from app.db import query_all


SECTION_HEADINGS = {
    "executive_summary": "Executive summary",
    "introduction": "Introduction",
    "marketing_authorisation_status": (
        "3.0 Worldwide marketing authorisation status"
    ),
    "safety_actions": (
        "4.0 Action taken in the reporting interval for safety reasons"
    ),
    "reference_safety_information": (
        "5.0 Changes to reference safety information"
    ),
    "exposure": "6.0 Estimated Exposure and Use Patterns",
    "signals": "17. Signal and risk evaluation",
    "benefit_risk": (
        "19. Integrated benefit-risk analysis for authorised indications"
    ),
    "conclusion": "20.0 Conclusion and actions",
}


def _text(value, fallback="Not recorded"):
    if value is None:
        return fallback

    value = str(value).strip()
    return value or fallback


def _date(value):
    if not value:
        return "Not recorded"

    if isinstance(value, date):
        return value.strftime("%d %B %Y")

    return str(value)


def _replace_in_paragraph(paragraph, old_text, new_text):
    for run in paragraph.runs:
        if old_text in run.text:
            run.text = run.text.replace(old_text, new_text)
            return True

    if old_text in paragraph.text:
        paragraph.text = paragraph.text.replace(old_text, new_text)
        return True

    return False


def _replace_paragraph_start(paragraph, label, value):
    if paragraph.text.strip().startswith(label):
        _replace_in_paragraph(
            paragraph,
            paragraph.text,
            f"{label} {value}",
        )
        return True

    return False


def _insert_paragraph_after(paragraph, text):
    new_paragraph_xml = OxmlElement("w:p")
    paragraph._p.addnext(new_paragraph_xml)

    new_paragraph = Paragraph(
        new_paragraph_xml,
        paragraph._parent,
    )
    new_paragraph.add_run(text)

    return new_paragraph


def _safe_filename(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def generate_psur_report(report):
    template_path = Path(current_app.config["PSUR_TEMPLATE_PATH"])

    if not template_path.exists():
        raise FileNotFoundError(
            f"PSUR template was not found: {template_path}"
        )

    document = Document(template_path)

    replacements = {
        "ACTIVE SUBSTANCE(S): WRITE": (
            "ACTIVE SUBSTANCE(S): "
            + _text(report["active_substances"])
        ),
        "ATC CODE(S): WRITE": (
            "ATC CODE(S): "
            + _text(report["atc_codes"])
        ),
        "MARKETING AUTHORISATION PROCEDURE in the EU: WRITE": (
            "MARKETING AUTHORISATION PROCEDURE in the EU: "
            + _text(report["marketing_authorisation_procedure"])
        ),
        "INTERNATIONAL BIRTH DATE (IBD): WRITE": (
            "INTERNATIONAL BIRTH DATE (IBD): "
            + _date(report["international_birth_date"])
        ),
        "EUROPEAN UNION REFERENCE DATE (EURD): WRITE": (
            "EUROPEAN UNION REFERENCE DATE (EURD): "
            + _date(report["eurd"])
        ),
        "NAME: WRITE": (
            "NAME: " + _text(report["qppv_name"])
        ),
        "Phone: WRITE": (
            "Phone: " + _text(report["qppv_phone"])
        ),
        "Email: write": (
            "Email: " + _text(report["qppv_email"])
        ),
        "QPPV: WRITE NAME": (
            "QPPV: " + _text(report["qppv_name"])
        ),
    }

    for paragraph in document.paragraphs:
        for old_text, new_text in replacements.items():
            _replace_in_paragraph(paragraph, old_text, new_text)

        _replace_paragraph_start(
            paragraph,
            "MEDICINAL PRODUCTS COVERED:",
            _text(report["product_name"]),
        )
        _replace_paragraph_start(
            paragraph,
            "Therapeutic Indication:",
            _text(report["therapeutic_indication"]),
        )
        _replace_paragraph_start(
            paragraph,
            "Mechanism of action:",
            _text(report["mechanism_of_action"]),
        )

    if len(document.tables) >= 1:
        authorisation_table = document.tables[0]

        if len(authorisation_table.rows) > 1:
            first_country = _text(
                report["countries_covered"]
            ).split(",")[0].strip()

            authorisation_table.cell(1, 0).text = _text(
                report["product_name"]
            )
            authorisation_table.cell(1, 1).text = _text(
                report["marketing_authorisation_number"]
            )
            authorisation_table.cell(1, 2).text = first_country
            authorisation_table.cell(1, 3).text = _date(
                report["marketing_authorisation_date"]
            )
            authorisation_table.cell(1, 4).text = _text(
                report["marketing_authorisation_holder_name"]
            )

    if len(document.tables) >= 2:
        period_table = document.tables[1]
        period_text = (
            "PERIOD COVERED BY THIS REPORT "
            f"From {_date(report['reporting_period_start'])} "
            f"to {_date(report['reporting_period_end'])}  "
            f"DATE OF THIS REPORT {_date(date.today())}"
        )
        period_table.cell(0, 0).text = period_text

    if len(document.tables) >= 3:
        serial_table = document.tables[2]

        if len(serial_table.rows) > 1:
            serial_table.cell(1, 0).text = _text(
                report["serial_number"],
                report["report_number"],
            )
            serial_table.cell(1, 1).text = (
                f"{_date(report['reporting_period_start'])} "
                f"to {_date(report['reporting_period_end'])}"
            )

    if len(document.tables) >= 4:
        country_table = document.tables[3]
        countries = _text(report["countries_covered"]).split(",")

        for index, country in enumerate(countries[:2], start=1):
            if index < len(country_table.rows):
                country_table.cell(index, 0).text = _text(
                    report["active_substances"]
                )
                country_table.cell(index, 1).text = country.strip()
                country_table.cell(index, 2).text = _date(
                    report["marketing_authorisation_date"]
                )

    saved_sections = query_all(
        """
        SELECT section_key, content
        FROM pv.psur_section_entries
        WHERE psur_id = %s
        """,
        (report["psur_id"],),
    )

    content_by_key = {
        section["section_key"]: section["content"]
        for section in saved_sections
    }

    for paragraph in list(document.paragraphs):
        heading = paragraph.text.strip()

        for section_key, template_heading in SECTION_HEADINGS.items():
            content = content_by_key.get(section_key)

            if content and heading == template_heading:
                _insert_paragraph_after(paragraph, content)
                break

    output_folder = (
        Path(current_app.config["UPLOAD_ROOT"]).resolve()
        / "reports"
    )
    output_folder.mkdir(parents=True, exist_ok=True)

    filename = (
        f"PSUR_{_safe_filename(report['report_number'])}.docx"
    )
    output_path = output_folder / filename

    document.save(output_path)

    return output_path