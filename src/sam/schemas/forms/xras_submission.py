"""HTMX form schemas for the requests a lead files from My Requests.

The shape of the body only: whether the user may file, the date floor, the limits and the
preflight are the handler's, against SAM and XRAS. Every form carries the agreement
(``XrasAgreementMixin``); the handler checks the posted ``eula_sha`` against the text it shows.
Design: ``docs/plans/XRAS_SUBMISSION_SPRINT1.md`` § 2.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load

from sam.text import strip_or_none

from . import HtmxFormSchema
from .xras_remediation import XRAS_LONGTEXT_MAX, XRAS_TITLE_MAX, clean_text

__all__ = ['XrasAgreementMixin', 'XrasExtensionRequestForm', 'XrasSupplementRequestForm',
           'XrasRenewalRequestForm', 'XrasNewRequestForm', 'XrasGrantMixin',
           'XrasAddActionAgreementForm', 'SUBMISSION_STAGES', 'NEW_REQUEST_STAGES', 'GRANT_INPUTS']

#: ``review`` renders the summary and the agreement; ``submit`` files; ``edit`` goes back.
SUBMISSION_STAGES = ('edit', 'review', 'submit')

#: A New: ``describe`` and ``request`` are its two pages, then ``review`` and ``submit``.
NEW_REQUEST_STAGES = ('describe', 'request', 'review', 'submit')

#: Form input -> the wire name ``add_grant`` sends.
GRANT_INPUTS = {'grant_number': 'grantNumber', 'grant_title': 'title',
                'grant_pi_name': 'piName', 'grant_begin_date': 'beginDate',
                'grant_end_date': 'endDate', 'grant_officer_name': 'programOfficerName',
                'grant_officer_email': 'programOfficerEmail'}

_COMMENTS_MIN = 10


class XrasAgreementFields(HtmxFormSchema):
    """The agreement: its checkbox and the sha of the text shown.

    Not refused here: the handler decides (a submit without the tick goes back to the
    review; the operator's Add-action refuses).
    """

    eula_sha = f.Str(load_default=None, validate=v.Length(equal=40))
    agree = f.Bool(load_default=False)


class XrasAgreementMixin(XrasAgreementFields):
    """The agreement and the stage of a request form."""

    stage = f.Str(load_default='review', validate=v.OneOf(SUBMISSION_STAGES))


class XrasExtensionRequestForm(XrasAgreementMixin):
    """An Extension: the new end date and why (XRAS's rule book requires both)."""

    end_date = f.Date(required=True, error_messages={'required': 'Choose the new end date.'})
    user_comments = f.Str(required=True, validate=v.Length(max=XRAS_LONGTEXT_MAX),
                          error_messages={'required': 'Say why the project needs more time.'})

    @post_load
    def _comments(self, data, **kwargs):
        data['user_comments'] = clean_text(data.get('user_comments'))
        if len(data['user_comments']) < _COMMENTS_MIN:
            raise ValidationError({'user_comments': [
                'Say why the project needs more time, in a sentence or two.']})
        return data


def _parse_amounts(raw: dict) -> dict:
    """``{resource id: amount}`` from the typed amounts; blank and zero ask for none."""
    amounts, errors = {}, {}
    for rid, text in raw.items():
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
        raise ValidationError('Add at least one resource.')
    return amounts


class XrasSupplementRequestForm(XrasAgreementMixin):
    """A Supplement: ``amounts`` maps an XRAS resource id to its typed amount (blank asks none).

    The handler folds the ``amount_<id>`` inputs into ``amounts`` (``form_input``): blank list
    entries would be dropped by the base, and the ids would no longer line up.
    """

    amounts = f.Dict(keys=f.Int(), values=f.Str(), load_default=dict)
    user_comments = f.Str(required=True, validate=v.Length(max=XRAS_LONGTEXT_MAX),
                          error_messages={'required': 'Say what the extra resources are for.'})

    @post_load
    def _amounts(self, data, **kwargs):
        data['user_comments'] = clean_text(data.get('user_comments'))
        if len(data['user_comments']) < _COMMENTS_MIN:
            raise ValidationError({'user_comments': [
                'Say what the extra resources are for, in a sentence or two.']})
        data['amounts'] = _parse_amounts(data['amounts'])
        return data


class XrasGrantMixin(HtmxFormSchema):
    """One supporting grant, typed or filled by the award lookup; ``grant`` is its wire dict.

    Which fields the type requires is the rule book's, so the handler checks them.
    """

    grant_number = f.Str(load_default=None, validate=v.Length(max=64))
    grant_title = f.Str(load_default=None, validate=v.Length(max=XRAS_TITLE_MAX))
    grant_pi_name = f.Str(load_default=None, validate=v.Length(max=255))
    grant_begin_date = f.Date(load_default=None)
    grant_end_date = f.Date(load_default=None)
    grant_officer_name = f.Str(load_default=None, validate=v.Length(max=255))
    grant_officer_email = f.Email(load_default=None, validate=v.Length(max=255))

    @post_load
    def _grant(self, data, **kwargs):
        for name in ('grant_number', 'grant_title', 'grant_pi_name', 'grant_officer_name'):
            data[name] = strip_or_none(data.get(name))
        begin, end = data.get('grant_begin_date'), data.get('grant_end_date')
        if begin and end and end < begin:
            raise ValidationError({'grant_end_date': ['The grant ends before it begins.']})
        data['grant'] = ({wire: data[name] for name, wire in GRANT_INPUTS.items()
                          if data.get(name) is not None} if data['grant_number'] else None)
        return data


class XrasRenewalRequestForm(XrasGrantMixin, XrasAgreementMixin):
    """A Renewal: the abstract (``/renew`` does not copy it), the window, an amount per resource."""

    abstract = f.Str(required=True, validate=v.Length(max=XRAS_LONGTEXT_MAX),
                     error_messages={'required': 'The renewal needs an abstract.'})
    begin_date = f.Date(required=True, error_messages={'required': 'Choose the start date.'})
    end_date = f.Date(required=True, error_messages={'required': 'Choose the end date.'})
    amounts = f.Dict(keys=f.Int(), values=f.Str(), load_default=dict)

    @post_load
    def _renewal(self, data, **kwargs):
        data['abstract'] = clean_text(data.get('abstract'))
        if len(data['abstract']) < _COMMENTS_MIN:
            raise ValidationError({'abstract': ['Describe the work for the coming period.']})
        if data['end_date'] <= data['begin_date']:
            raise ValidationError({'end_date': ['The end date must follow the start date.']})
        data['amounts'] = _parse_amounts(data['amounts'])
        return data


class XrasNewRequestForm(XrasGrantMixin, XrasAgreementMixin):
    """A New: the description (page one), then the window and amounts (page two, required
    from ``review``). The opportunity's resources, limits and rule book are the handler's."""

    stage = f.Str(load_default='request', validate=v.OneOf(NEW_REQUEST_STAGES))
    opportunity_id = f.Int(required=True, error_messages={'required': 'Choose an opportunity.'})
    title = f.Str(required=True, validate=v.Length(max=XRAS_TITLE_MAX),
                  error_messages={'required': 'The project needs a title.'})
    abstract = f.Str(required=True, validate=v.Length(max=XRAS_LONGTEXT_MAX),
                     error_messages={'required': 'The project needs an abstract.'})
    keywords = f.Str(load_default=None, validate=v.Length(max=500))
    fos_type_id = f.Int(required=True,
                        error_messages={'required': 'Choose the field of science.'})
    admin_user_id = f.Int(load_default=None)
    mnemonic_code_id = f.Int(load_default=None)
    begin_date = f.Date(load_default=None)
    end_date = f.Date(load_default=None)
    amounts = f.Dict(keys=f.Int(), values=f.Str(), load_default=dict)

    @post_load
    def _new(self, data, **kwargs):
        data['title'] = clean_text(data.get('title'))
        data['abstract'] = clean_text(data.get('abstract'))
        data['keywords'] = strip_or_none(data.get('keywords'))
        errors = {}
        if not data['title']:
            errors['title'] = ['The project needs a title.']
        if len(data['abstract']) < _COMMENTS_MIN:
            errors['abstract'] = ['Describe the work in a few sentences.']
        if errors:
            raise ValidationError(errors)
        if data['stage'] in ('describe', 'request'):
            data['amounts'] = {}
            return data
        if data['begin_date'] is None:
            errors['begin_date'] = ['Choose the start date.']
        if data['end_date'] is None:
            errors['end_date'] = ['Choose the end date.']
        elif data['begin_date'] and data['end_date'] <= data['begin_date']:
            errors['end_date'] = ['The end date must follow the start date.']
        if errors:
            raise ValidationError(errors)
        data['amounts'] = _parse_amounts(data['amounts'])
        return data


class XrasAddActionAgreementForm(XrasAgreementFields):
    """The operator Add-action modal's agreement, when the action is filed through the flow."""
