from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length


class UserCreateForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(min=3, max=100),
        ],
    )

    full_name = StringField(
        "Full name",
        validators=[
            DataRequired(),
            Length(max=200),
        ],
    )

    email = StringField(
        "Email address",
        validators=[
            DataRequired(),
            Email(),
            Length(max=255),
        ],
    )

    role_id = SelectField(
        "System role",
        coerce=int,
        validators=[DataRequired()],
    )

    password = PasswordField(
        "Temporary password",
        validators=[
            DataRequired(),
            Length(min=10, max=128),
        ],
    )

    submit = SubmitField("Create user account")