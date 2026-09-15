from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    IntegerField,
    SelectField,
    StringField,
    TextAreaField,
    TimeField,
)
from wtforms.validators import DataRequired, Email, NumberRange, Optional


class SafetyCaseForm(FlaskForm):
    icsr_case_id = StringField("ICSR Case ID", validators=[DataRequired()])

    received_date = DateField("Date received", validators=[DataRequired()])
    country_id = SelectField(
        "Country",
        coerce=int,
        validators=[DataRequired()],
    )
    source = SelectField(
        "Source of report",
        choices=[
            ("Healthcare professional", "Healthcare professional"),
            ("Patient or consumer", "Patient or consumer"),
            ("Distributor", "Distributor"),
            ("Literature", "Literature"),
            ("Regulatory authority", "Regulatory authority"),
            ("Other", "Other"),
        ],
        validators=[DataRequired()],
    )
    report_type = SelectField(
        "Report type",
        choices=[
            ("Initial", "Initial"),
            ("Follow-up", "Follow-up"),
        ],
        validators=[DataRequired()],
    )

    reporter_name = StringField("Reporter name", validators=[Optional()])
    reporter_profession = StringField("Reporter profession", validators=[Optional()])
    reporter_organisation = StringField("Reporter organisation", validators=[Optional()])
    reporter_phone = StringField("Reporter telephone", validators=[Optional()])
    reporter_email = StringField(
        "Reporter email",
        validators=[Optional(), Email()],
    )

    patient_initials = StringField("Patient initials", validators=[Optional()])
    patient_date_of_birth = DateField("Patient date of birth", validators=[Optional()])
    patient_age_years = IntegerField(
        "Patient age",
        validators=[Optional(), NumberRange(min=0, max=150)],
    )
    patient_sex = SelectField(
        "Patient sex",
        choices=[
            ("", "Select"),
            ("Female", "Female"),
            ("Male", "Male"),
            ("Unknown", "Unknown"),
        ],
        validators=[Optional()],
    )
    patient_weight_kg = StringField("Patient weight (kg)", validators=[Optional()])
    patient_pregnancy_status = SelectField(
        "Pregnancy status",
        choices=[
            ("", "Select"),
            ("Yes", "Yes"),
            ("No", "No"),
            ("Not applicable", "Not applicable"),
            ("Unknown", "Unknown"),
        ],
        validators=[Optional()],
    )
    patient_address = TextAreaField("Patient address", validators=[Optional()])
    patient_phone = StringField("Patient telephone", validators=[Optional()])
    medical_history = TextAreaField("Relevant medical history", validators=[Optional()])
    concomitant_medicines = TextAreaField(
        "Concomitant medicines",
        validators=[Optional()],
    )

    product_name = StringField("Suspected product name", validators=[DataRequired()])
    generic_name = StringField("Generic name", validators=[Optional()])
    strength = StringField("Strength", validators=[Optional()])
    dosage_form = StringField("Dosage form", validators=[Optional()])
    batch_number = StringField("Batch number", validators=[Optional()])
    expiry_date = DateField("Expiry date", validators=[Optional()])
    dose = StringField("Dose", validators=[Optional()])
    route = StringField("Route of administration", validators=[Optional()])
    frequency = StringField("Frequency", validators=[Optional()])
    indication = StringField("Indication", validators=[Optional()])
    therapy_start_date = DateField("Therapy start date", validators=[Optional()])
    therapy_end_date = DateField("Therapy end date", validators=[Optional()])
    action_taken = SelectField(
        "Action taken with suspected medicine",
        choices=[
            ("", "Select"),
            ("Drug withdrawn", "Drug withdrawn"),
            ("Dose increased", "Dose increased"),
            ("Dose reduced", "Dose reduced"),
            ("Dose not changed", "Dose not changed"),
            ("Unknown", "Unknown"),
        ],
        validators=[Optional()],
    )

    event_description = TextAreaField(
        "Adverse event description",
        validators=[DataRequired()],
    )
    treatment_given = TextAreaField("Treatment given", validators=[Optional()])
    event_onset_date = DateField("Event onset date", validators=[Optional()])
    event_onset_time = TimeField(
        "Event onset time",
        format="%H:%M",
        validators=[Optional()],
    )
    event_end_date = DateField("Event end date", validators=[Optional()])
    laboratory_results = TextAreaField(
        "Relevant laboratory test results",
        validators=[Optional()],
    )
    event_outcome = SelectField(
        "Outcome",
        choices=[
            ("", "Select"),
            ("Recovered/resolved", "Recovered/resolved"),
            ("Recovering/resolving", "Recovering/resolving"),
            ("Not recovered/not resolved", "Not recovered/not resolved"),
            ("Recovered with sequelae", "Recovered with sequelae"),
            ("Fatal", "Fatal"),
            ("Unknown", "Unknown"),
        ],
        validators=[Optional()],
    )
    seriousness = BooleanField("This is a serious adverse event")
    seriousness_criteria = TextAreaField(
        "Reason for seriousness",
        validators=[Optional()],
    )
    causality_assessment = SelectField(
        "Causality assessment",
        choices=[
            ("", "Select"),
            ("Certain", "Certain"),
            ("Probable / Likely", "Probable / Likely"),
            ("Possible", "Possible"),
            ("Unlikely", "Unlikely"),
            ("Unassessable / Unclassifiable", "Unassessable / Unclassifiable"),
        ],
        validators=[Optional()],
    )
    case_narrative = TextAreaField("Case narrative", validators=[Optional()])
    follow_up_required = BooleanField("Follow-up is required")
    follow_up_due_date = DateField("Follow-up due date", validators=[Optional()])
    report_title = StringField("Report title", validators=[Optional()])
    form_id = StringField("Form ID number", validators=[Optional()])