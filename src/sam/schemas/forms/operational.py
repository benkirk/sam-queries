"""
Marshmallow form validation schemas for operational-data routes.

Covers: Wallclock Exemptions (admin dashboard).
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load

from . import HtmxFormSchema


class CreateWallclockExemptionForm(HtmxFormSchema):
    """Create a wallclock exemption for a route-resolved user.

    Queue existence and the user lookup require DB access and stay in the
    route/handler per CLAUDE.md §9.
    """
    queue_id = f.Int(
        required=True,
        error_messages={'required': 'Queue is required.'})
    start_date = f.Date(
        '%Y-%m-%d', required=True,
        error_messages={'required': 'Start date is required.',
                        'invalid': 'Invalid start date format.'})
    end_date = f.Str(   # 23:59:59 convention applied in post_load
        required=True,
        error_messages={'required': 'End date is required.'})
    time_limit_hours = f.Float(
        required=True,
        validate=v.Range(min=0, min_inclusive=False,
                         error='Time limit must be a positive number.'),
        error_messages={'required': 'Time limit (hours) is required.',
                        'invalid': 'Time limit must be a number.'})
    comment = f.Str(load_default=None)

    @post_load
    def coerce_and_validate_dates(self, data, **kwargs):
        try:
            data['end_date'] = self.normalize_end_date(data['end_date'])
        except ValueError:
            raise ValidationError({'end_date': ['Invalid end date format.']})
        self.assert_date_range(data.get('start_date'), data['end_date'])
        return data


class AdminCreateWallclockExemptionForm(CreateWallclockExemptionForm):
    """Create-exemption variant for the admin "New" button — the user
    arrives from an FK picker instead of the route path."""
    user_id = f.Int(
        required=True,
        error_messages={'required': 'User is required.'})


class EditWallclockExemptionForm(HtmxFormSchema):
    """Edit a wallclock exemption (end date / limit / comment).

    The end-vs-start check needs the stored start_date, so it lives in
    the handler's clean() (requires the loaded ORM object).
    """
    end_date = f.Str(   # 23:59:59 convention applied in post_load
        required=True,
        error_messages={'required': 'End date is required.'})
    time_limit_hours = f.Float(
        required=True,
        validate=v.Range(min=0, min_inclusive=False,
                         error='Time limit must be a positive number.'),
        error_messages={'required': 'Time limit (hours) is required.',
                        'invalid': 'Time limit must be a number.'})
    comment = f.Str(load_default=None)

    @post_load
    def coerce_end_date(self, data, **kwargs):
        try:
            data['end_date'] = self.normalize_end_date(data['end_date'])
        except ValueError:
            raise ValidationError({'end_date': ['Invalid end date format.']})
        return data
