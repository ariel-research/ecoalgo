from flask_security.forms import RegisterForm
from wtforms import StringField, HiddenField, PasswordField
from wtforms.validators import Length, Optional
from models import User


class ExtendedRegisterForm(RegisterForm):
    """Extended registration form - username auto-generated from email, no password confirm."""
    username = HiddenField('Username', validators=[Optional()])
    password_confirm = HiddenField('Retype Password', validators=[Optional()])
