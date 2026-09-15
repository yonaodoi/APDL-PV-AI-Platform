from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class ProductComplaintForm(FlaskForm):
    complaint_number = StringField(
        "Complaint ID",
        validators=[DataRequired(), Length(max=100)],
    )
    date_received = DateField(
        "Date received",
        format="%Y-%m-%d",
        default=date.today,
        validators=[DataRequired()],
    )
    country_id = SelectField(
        "Country",
        coerce=int,
        validators=[DataRequired()],
    )
    reporter_name = StringField(
        "Reporter name",
        validators=[Optional(), Length(max=200)],
    )
    reporter_contact = StringField(
        "Reporter contact",
        validators=[Optional(), Length(max=200)],
    )

    product_name = StringField(
        "Product name",
        validators=[DataRequired(), Length(max=200)],
    )
    batch_number = StringField(
        "Batch number",
        validators=[Optional(), Length(max=100)],
    )
    manufacturing_date = DateField(
        "Manufacturing date",
        format="%Y-%m-%d",
        validators=[Optional()],
    )
    expiry_date = DateField(
        "Expiry date",
        format="%Y-%m-%d",
        validators=[Optional()],
    )

    complaint_category = SelectField(
        "Complaint category",
        choices=[
            ("Product quality", "Product quality"),
            ("Packaging", "Packaging"),
            ("Labelling", "Labelling"),
            ("Adverse event", "Adverse event"),
            ("Other", "Other"),
        ],
        validators=[DataRequired()],
    )
    severity = SelectField(
        "Severity",
        choices=[
            ("Non-serious", "Non-serious"),
            ("Serious", "Serious"),
            ("Critical", "Critical"),
        ],
        validators=[DataRequired()],
    )
    complaint_description = TextAreaField(
        "Complaint description",
        validators=[DataRequired(), Length(max=5000)],
    )

    submit = SubmitField("Save product complaint")

class ComplaintReviewForm(FlaskForm):
    status = SelectField(
        "Workflow status",
        choices=[
            ("New", "New"),
            ("Under investigation", "Under investigation"),
            ("Awaiting information", "Awaiting information"),
            ("Closed", "Closed"),
        ],
        validators=[DataRequired()],
    )
    investigation_summary = TextAreaField(
        "Investigation summary",
        validators=[Optional(), Length(max=5000)],
    )
    corrective_action = TextAreaField(
        "Corrective action / CAPA",
        validators=[Optional(), Length(max=5000)],
    )
    closure_date = DateField(
        "Closure date",
        format="%Y-%m-%d",
        validators=[Optional()],
    )
    submit = SubmitField("Save investigation update")