from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter


REASON_POSITIONS = {
    "minimum_patient": (48, 436),
    "identifiable_reporter": (308, 436),
    "reported_event": (48, 407),
    "seriousness_basis": (308, 407),
    "event_outcome": (48, 378),
    "event_onset_date": (308, 378),
    "event_end_date": (48, 349),
    "medical_history": (308, 349),
    "concomitant_medicines": (48, 320),
    "suspected_product": (308, 320),
}


def _display_date(value):
    return value.strftime("%d/%m/%Y") if value else ""


def _draw_wrapped(canvas_instance, text, x, y, width, line_height=12):
    words = str(text or "").split()
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if canvas_instance.stringWidth(candidate, "Helvetica", 9) > width:
            canvas_instance.drawString(x, y, line)
            y -= line_height
            line = word
        else:
            line = candidate
    if line:
        canvas_instance.drawString(x, y, line)


def build_abacus_follow_up_pdf(template_path, case, product, task, checks):
    template = Path(template_path)
    if not template.is_file():
        raise FileNotFoundError(
            f"Abacus follow-up template was not found: {template}"
        )

    overlay_stream = BytesIO()
    overlay = canvas.Canvas(overlay_stream, pagesize=letter)
    overlay.setFont("Helvetica", 9)

    identification_values = (
        (205, 628, case.get("case_number")),
        (205, 610, _display_date(case.get("received_date"))),
        (205, 592, case.get("patient_initials")),
        (205, 574, product.get("product_name")),
    )
    for x, y, value in identification_values:
        if value:
            overlay.drawString(x, y, str(value))

    if case.get("event_description"):
        _draw_wrapped(
            overlay,
            case["event_description"],
            205,
            556,
            280,
        )

    overlay.drawString(205, 520, "1")

    for check in checks:
        if check["code"] in REASON_POSITIONS:
            x, y = REASON_POSITIONS[check["code"]]
            overlay.setFont("Helvetica-Bold", 12)
            overlay.drawString(x, y, "X")
            overlay.setFont("Helvetica", 9)

    overlay.showPage()
    overlay.setFont("Helvetica", 9)
    overlay.drawString(72, 540, task["task_description"])
    overlay.setFont("Helvetica", 9)
    overlay.drawString(72, 505, f"Case: {case['case_number']}")
    overlay.drawString(72, 488, f"Follow-up due: {_display_date(task['due_date'])}")
    overlay.save()
    overlay_stream.seek(0)

    source = PdfReader(str(template))
    overlay_reader = PdfReader(overlay_stream)
    writer = PdfWriter()

    for index, page in enumerate(source.pages):
        if index < len(overlay_reader.pages):
            page.merge_page(overlay_reader.pages[index])
        writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output
