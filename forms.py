from flask_security.forms import RegisterForm
from wtforms import HiddenField
from wtforms.validators import Optional
from models import User


class ExtendedRegisterForm(RegisterForm):
    """Extended registration form - username auto-generated from email, no password confirm."""
    username = HiddenField('Username', validators=[Optional()])
    password_confirm = HiddenField('Retype Password', validators=[Optional()])

    def validate(self, **kwargs):
        # Auto-fill hidden fields so Flask-Security's validation passes unchanged
        self.username.data = self.email.data or ''
        self.password_confirm.data = self.password.data or ''
        return super().validate(**kwargs)
