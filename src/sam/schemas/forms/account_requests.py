"""Form schemas for the HPC account-request queue and invitation surfaces."""

from marshmallow import fields as f, post_load, validate as v

from . import HtmxFormSchema


class AccountRequestReasonForm(HtmxFormSchema):
    """Dismiss and reject both need a reason: it is what the next operator,
    or the requester, is told."""

    reason = f.Str(required=True, validate=v.Length(min=1, max=255),
                   error_messages={'required': 'A reason is required.'})

    @post_load
    def _strip(self, data, **kwargs):
        data['reason'] = data['reason'].strip()
        if not data['reason']:
            from marshmallow import ValidationError
            raise ValidationError({'reason': ['A reason is required.']})
        return data
