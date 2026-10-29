"""HTMX form schemas for the requests a lead files from My Requests.

The shape of the body only: whether the user may file, the date floor, the limits and the
preflight are the handler's, against SAM and XRAS. Every form carries the agreement
(``XrasAgreementMixin``); the handler checks the posted ``eula_sha`` against the text it shows.
Design: ``docs/plans/XRAS_SUBMISSION_SPRINT1.md`` § 2.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load

from . import HtmxFormSchema
from .xras_remediation import _LONGTEXT_MAX, _clean

__all__ = ['XrasAgreementMixin', 'XrasExtensionRequestForm', 'XrasSupplementRequestForm',
           'XrasAddActionAgreementForm', 'SUBMISSION_STAGES']

#: ``review`` renders the summary and the agreement; ``submit`` files; ``edit`` goes back.
SUBMISSION_STAGES = ('edit', 'review', 'submit')

_COMMENTS_MIN = 10


class XrasAgreementMixin(HtmxFormSchema):
    """The stage, and the agreement: its checkbox and the sha of the text shown.

    Not refused here: a submit without the tick goes back to the review, which the
    handler renders from the rest of the body (``clean``).
    """

    stage = f.Str(load_default='review', validate=v.OneOf(SUBMISSION_STAGES))
    eula_sha = f.Str(load_default=None, validate=v.Length(equal=40))
    agree = f.Bool(load_default=False)


class XrasExtensionRequestForm(XrasAgreementMixin):
    """An Extension: the new end date and why (XRAS's rule book requires both)."""

    end_date = f.Date(required=True, error_messages={'required': 'Choose the new end date.'})
    user_comments = f.Str(required=True, validate=v.Length(max=_LONGTEXT_MAX),
                          error_messages={'required': 'Say why the project needs more time.'})

    @post_load
    def _comments(self, data, **kwargs):
        data['user_comments'] = _clean(data.get('user_comments'))
        if len(data['user_comments']) < _COMMENTS_MIN:
            raise ValidationError({'user_comments': [
                'Say why the project needs more time, in a sentence or two.']})
        return data


class XrasSupplementRequestForm(XrasAgreementMixin):
    """A Supplement: ``amounts`` maps an XRAS resource id to its typed amount (blank asks none).

    The handler folds the ``amount_<id>`` inputs into ``amounts`` (``form_input``): blank list
    entries would be dropped by the base, and the ids would no longer line up.
    """

    amounts = f.Dict(keys=f.Int(), values=f.Str(), load_default=dict)
    user_comments = f.Str(required=True, validate=v.Length(max=_LONGTEXT_MAX),
                          error_messages={'required': 'Say what the extra resources are for.'})

    @post_load
    def _amounts(self, data, **kwargs):
        data['user_comments'] = _clean(data.get('user_comments'))
        if len(data['user_comments']) < _COMMENTS_MIN:
            raise ValidationError({'user_comments': [
                'Say what the extra resources are for, in a sentence or two.']})
        amounts, errors = {}, {}
        for rid, text in data['amounts'].items():
            text = (text or '').replace(',', '').strip()
            if not text:
                continue
            try:
                value = float(text)
            except ValueError:
                errors[f'amount_{rid}'] = ['Enter a number.']
                continue
            if value < 0:
                errors[f'amount_{rid}'] = ['Enter zero or more.']
            elif value > 0:
                amounts[rid] = value
        if errors:
            raise ValidationError(errors)
        if not amounts:
            raise ValidationError('Ask for an amount on at least one resource.')
        data['amounts'] = amounts
        return data


class XrasAddActionAgreementForm(HtmxFormSchema):
    """The operator Add-action modal's agreement, when the action is filed through the flow."""

    eula_sha = f.Str(load_default=None, validate=v.Length(equal=40))
    agree = f.Bool(load_default=False)
