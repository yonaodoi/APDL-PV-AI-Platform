from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from app.psur.section_definitions import PSUR_SECTION_CHOICES


class PsurReportForm(FlaskForm):
    report_number = StringField(
        "PSUR ID",
        validators=[DataRequired(), Length(max=100)],
    )
    serial_number = StringField(
        "Serial number",
        validators=[Optional(), Length(max=100)],
    )
    product_name = StringField(
        "Medicinal product covered",
        validators=[DataRequired(), Length(max=200)],
    )
    active_substances = StringField(
        "Active substance(s)",
        validators=[Optional(), Length(max=500)],
    )
    atc_codes = StringField(
        "ATC code(s)",
        validators=[Optional(), Length(max=500)],
    )
    marketing_authorisation_number = StringField(
        "Marketing authorisation number(s)",
        validators=[Optional(), Length(max=1000)],
    )
    marketing_authorisation_date = DateField(
        "Marketing authorisation date",
        format="%Y-%m-%d",
        validators=[Optional()],
    )
    marketing_authorisation_procedure = StringField(
        "Marketing authorisation procedure",
        validators=[Optional(), Length(max=500)],
    )
    international_birth_date = DateField(
        "International birth date",
        format="%Y-%m-%d",
        validators=[Optional()],
    )
    eurd = DateField(
        "European Union reference date",
        format="%Y-%m-%d",
        validators=[Optional()],
    )

    reporting_period_start = DateField(
        "Reporting period start",
        format="%Y-%m-%d",
        validators=[DataRequired()],
    )
    reporting_period_end = DateField(
        "Reporting period end",
        format="%Y-%m-%d",
        validators=[DataRequired()],
    )
    data_lock_point = DateField(
        "Data lock point",
        format="%Y-%m-%d",
        default=date.today,
        validators=[DataRequired()],
    )

    marketing_authorisation_holder_name = StringField(
        "Marketing authorisation holder",
        validators=[Optional(), Length(max=500)],
    )
    marketing_authorisation_holder_address = TextAreaField(
        "Marketing authorisation holder address",
        validators=[Optional(), Length(max=2000)],
    )
    qppv_name = StringField(
        "QPPV name",
        validators=[Optional(), Length(max=200)],
    )
    qppv_phone = StringField(
        "QPPV telephone",
        validators=[Optional(), Length(max=100)],
    )
    qppv_email = StringField(
        "QPPV email",
        validators=[Optional(), Length(max=200)],
    )
    pbrer_contact_name = StringField(
        "Contact person for the PBRER",
        validators=[Optional(), Length(max=200)],
    )
    pbrer_contact_position = StringField(
        "PBRER contact position",
        validators=[Optional(), Length(max=200)],
    )
    reviewer_a_name = StringField(
        "Reviewer name",
        validators=[Optional(), Length(max=200)],
    )
    reviewer_a_position = StringField(
        "Reviewer position",
        validators=[Optional(), Length(max=200)],
    )
    prepared_by_position = StringField(
        "Prepared by position",
        validators=[Optional(), Length(max=200)],
    )

    therapeutic_indication = TextAreaField(
        "Therapeutic indication",
        validators=[Optional(), Length(max=5000)],
    )
    mechanism_of_action = TextAreaField(
        "Mechanism of action",
        validators=[Optional(), Length(max=5000)],
    )
    countries_covered = TextAreaField(
        "Countries covered",
        validators=[Optional(), Length(max=2000)],
    )
    prepared_by = StringField(
        "Prepared by",
        validators=[Optional(), Length(max=200)],
    )
    approved_by = StringField(
        "Approved by",
        validators=[Optional(), Length(max=200)],
    )
    report_notes = TextAreaField(
        "Report notes",
        validators=[Optional(), Length(max=5000)],
    )
    submit = SubmitField("Save PSUR record")


class PsurReviewForm(FlaskForm):
    status = SelectField(
        "Report status",
        choices=[
            ("Draft", "Draft"),
            ("Under review", "Under review"),
            ("Approved", "Approved"),
            ("Finalised", "Finalised"),
        ],
        validators=[DataRequired()],
    )
    prepared_by = StringField(
        "Prepared by",
        validators=[Optional(), Length(max=200)],
    )
    approved_by = StringField(
        "Approved by",
        validators=[Optional(), Length(max=200)],
    )
    report_notes = TextAreaField(
        "Report preparation notes",
        validators=[Optional(), Length(max=5000)],
    )
    submit = SubmitField("Save PSUR update")

class PsurSectionForm(FlaskForm):
    section_key = SelectField(
        "PSUR section",
        choices=PSUR_SECTION_CHOICES,
        validators=[DataRequired()],
    )
    content = TextAreaField(
        "Section content",
        validators=[DataRequired(), Length(max=15000)],
    )
    submit = SubmitField("Save PSUR section")