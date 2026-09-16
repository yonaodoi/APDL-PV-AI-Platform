from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import (
    DateField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, Optional, URL


class ReferenceSafetyInformationForm(FlaskForm):
    product_name = StringField(
        "APDL product name",
        validators=[DataRequired(), Length(max=255)],
    )

    active_substance = StringField(
        "Active substance",
        validators=[Optional(), Length(max=255)],
    )

    reference_product_name = StringField(
        "Innovator / reference product",
        validators=[Optional(), Length(max=255)],
    )

    market = StringField(
        "Reference market",
        validators=[Optional(), Length(max=150)],
    )

    document_type = SelectField(
        "Reference document type",
        choices=[
            (
                "APDL Product Information",
                "APDL Product Information",
            ),
            (
                "Innovator Reference Safety Information",
                "Innovator Reference Safety Information",
            ),
            ("SmPC", "SmPC"),
            (
                "US Prescribing Information",
                "US Prescribing Information",
            ),
            (
                "Patient Information Leaflet",
                "Patient Information Leaflet",
            ),
            (
                "Company Core Data Sheet",
                "Company Core Data Sheet",
            ),
            ("Local product label", "Local product label"),
            ("Other approved RSI", "Other approved RSI"),
        ],
        validators=[DataRequired()],
    )

    document_version = StringField(
        "Document version / revision",
        validators=[Optional(), Length(max=100)],
    )

    effective_date = DateField(
        "Effective date",
        validators=[Optional()],
    )

    source_url = StringField(
        "Official online reference URL",
        validators=[Optional(), URL(), Length(max=2000)],
    )

    source_file = FileField(
        "Upload controlled reference document",
        validators=[Optional()],
    )

    submit = SubmitField("Save reference safety information")

    def validate(self, extra_validators=None):
        is_valid = super().validate(extra_validators=extra_validators)

        if not self.source_url.data and not self.source_file.data:
            self.source_url.errors.append(
                "Provide an official URL or upload the controlled RSI document."
            )
            return False

        return is_valid


class CaseSafetyAssessmentForm(FlaskForm):
    rsi_id = SelectField(
        "Reference safety information",
        coerce=int,
        validators=[DataRequired()],
    )

    event_term_assessed = StringField(
        "Event term assessed",
        validators=[DataRequired(), Length(max=500)],
    )

    listedness_status = SelectField(
        "Listedness in RSI",
        choices=[
            ("Listed", "Listed"),
            ("Not listed", "Not listed"),
            ("Insufficient information", "Insufficient information"),
        ],
        validators=[DataRequired()],
    )

    expectedness_status = SelectField(
        "Expectedness",
        choices=[
            ("Expected", "Expected"),
            ("Unexpected", "Unexpected"),
            ("Not assessable", "Not assessable"),
        ],
        validators=[DataRequired()],
    )

    seriousness_assessment = SelectField(
        "Seriousness assessment",
        choices=[
            ("Serious", "Serious"),
            ("Non-serious", "Non-serious"),
            ("Not assessable", "Not assessable"),
        ],
        validators=[DataRequired()],
    )

    seriousness_criteria = TextAreaField(
        "Seriousness criteria",
        validators=[Optional()],
    )

    rsi_evidence = TextAreaField(
        "RSI evidence",
        validators=[Optional()],
    )

    frequency_assessment = SelectField(
        "Frequency in reference information",
        choices=[
            ("Very common", "Very common"),
            ("Common", "Common"),
            ("Uncommon", "Uncommon"),
            ("Rare", "Rare"),
            ("Very rare", "Very rare"),
            ("Frequency not known", "Frequency not known"),
            ("Not stated", "Not stated"),
            ("Not assessable", "Not assessable"),
        ],
        validators=[DataRequired()],
    )

    frequency_evidence = TextAreaField(
        "Frequency evidence",
        validators=[Optional()],
    )

    assessment_rationale = TextAreaField(
        "Assessment rationale",
        validators=[Optional()],
    )

    submit = SubmitField("Save safety assessment")