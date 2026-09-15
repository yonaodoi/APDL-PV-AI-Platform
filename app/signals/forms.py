from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class SafetySignalForm(FlaskForm):
    signal_number = StringField(
        "Signal ID",
        validators=[DataRequired(), Length(max=100)],
    )
    date_detected = DateField(
        "Date detected",
        format="%Y-%m-%d",
        default=date.today,
        validators=[DataRequired()],
    )
    product_name = StringField(
        "Product name",
        validators=[DataRequired(), Length(max=200)],
    )
    event_term = StringField(
        "Event / safety concern",
        validators=[DataRequired(), Length(max=300)],
    )
    signal_source = SelectField(
        "Signal source",
        choices=[
            ("ICSR review", "ICSR review"),
            ("Literature", "Literature"),
            ("Product complaint", "Product complaint"),
            ("Regulatory authority", "Regulatory authority"),
            ("Other", "Other"),
        ],
        validators=[DataRequired()],
    )
    priority = SelectField(
        "Priority",
        choices=[
            ("Low", "Low"),
            ("Medium", "Medium"),
            ("High", "High"),
            ("Critical", "Critical"),
        ],
        validators=[DataRequired()],
    )
    signal_description = TextAreaField(
        "Signal description",
        validators=[DataRequired(), Length(max=5000)],
    )
    owner_name = StringField(
        "Signal owner",
        validators=[Optional(), Length(max=200)],
    )
    submit = SubmitField("Save safety signal")

class SignalEvaluationForm(FlaskForm):
    status = SelectField(
        "Evaluation status",
        choices=[
            ("New", "New"),
            ("Under evaluation", "Under evaluation"),
            ("Validated", "Validated"),
            ("Closed", "Closed"),
        ],
        validators=[DataRequired()],
    )
    assessment_summary = TextAreaField(
        "Assessment summary",
        validators=[Optional(), Length(max=5000)],
    )
    decision_summary = TextAreaField(
        "Decision and action",
        validators=[Optional(), Length(max=5000)],
    )
    owner_name = StringField(
        "Signal owner",
        validators=[Optional(), Length(max=200)],
    )
    submit = SubmitField("Save signal evaluation")