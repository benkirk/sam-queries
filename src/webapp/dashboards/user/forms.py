"""
WTForms form definitions for user dashboard.

Uses plain WTForms (already available as a transitive dependency via flask-admin).
Flask-WTF can be added later for CSRF protection.
"""

from wtforms import Form, HiddenField, DateField
from wtforms.validators import DataRequired, Optional as OptionalValidator, ValidationError


class AddMemberForm(Form):
    """
    Server-side form for adding a member to a project.

    Replaces manual request.get_json() parsing with declarative validation.
    The hidden username field is populated client-side via the search/select flow.
    """
    username = HiddenField('Username', validators=[
        DataRequired(message='Please select a user first')
    ])
    start_date = DateField('Start Date', validators=[OptionalValidator()])
    end_date = DateField('End Date', validators=[OptionalValidator()])

    def validate_end_date(self, field):
        """End date must be after start date if both are provided."""
        if field.data and self.start_date.data and field.data <= self.start_date.data:
            raise ValidationError('End date must be after start date')
