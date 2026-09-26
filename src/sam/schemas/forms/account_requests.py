"""Form schemas for the HPC account-request queue and invitation surfaces."""

from datetime import date
from typing import Optional

from marshmallow import ValidationError, fields as f, post_load, validate as v, validates

from . import HtmxFormSchema


class AccountRequestReasonForm(HtmxFormSchema):
    """Dismiss and reject both need a reason: it is what the next operator,
    or the requester, is told. ``notify`` is the reject form's checkbox
    (absent when unchecked, so ``load_default`` is the right False)."""

    reason = f.Str(required=True, validate=v.Length(min=1, max=255),
                   error_messages={'required': 'A reason is required.'})
    notify = f.Bool(load_default=False)

    @post_load
    def _strip(self, data, **kwargs):
        data['reason'] = data['reason'].strip()
        if not data['reason']:
            raise ValidationError({'reason': ['A reason is required.']})
        return data


#: HTML datetime-local wire format, as sam.schemas.forms.status uses it.
_DATETIME_LOCAL = '%Y-%m-%dT%H:%M'

#: Lead time the account team asks for; a nearer deadline saves with a warning.
DEADLINE_LEAD_DAYS = 28
PAST_DEADLINE_MSG = 'The date accounts are needed by cannot be in the past.'


def deadline_notice(needed_by: Optional[date], today: Optional[date] = None) -> Optional[str]:
    """The warning for a deadline 0 to ``DEADLINE_LEAD_DAYS`` away; None otherwise
    (a past date is refused on entry, and an old event re-saved is not news)."""
    if needed_by is None:
        return None
    days = (needed_by - (today or date.today())).days
    if not 0 <= days < DEADLINE_LEAD_DAYS:
        return None
    when = 'today' if days == 0 else f'in {days} day{"" if days == 1 else "s"}'
    return (f'Accounts are needed {when}; the account team asks for at least '
            f'{DEADLINE_LEAD_DAYS // 7} weeks.')


_EVENT_CODE_RE = r'^[A-Za-z0-9][A-Za-z0-9-]{2,31}$'
_EVENT_CODE_MSG = ('3-32 letters, digits or dashes, starting with a letter or '
                   'digit (e.g. WRF-TUTORIAL-2026-10).')


class InviteUserForm(HtmxFormSchema):
    """One person invited onto a project by a steward. The email is the one
    key that resolves later, so it is lower-cased here. ``send_invite`` is a
    checkbox the route injects explicitly (absent means unchecked)."""

    email = f.Email(required=True, validate=v.Length(max=255))
    first_name = f.Str(required=True, validate=v.Length(min=1, max=64))
    last_name = f.Str(required=True, validate=v.Length(min=1, max=64))
    organization = f.Str(load_default=None, validate=v.Length(max=128))
    note = f.Str(load_default=None, validate=v.Length(max=255))
    event_code = f.Str(load_default=None, validate=v.Length(max=32))
    send_invite = f.Bool(load_default=False)

    @post_load
    def _normalize(self, data, **kwargs):
        data['email'] = data['email'].strip().lower()
        for key in ('first_name', 'last_name', 'organization', 'note'):
            if data.get(key) is not None:
                data[key] = data[key].strip() or None
        if data.get('event_code'):
            data['event_code'] = data['event_code'].strip().upper()
        return data


class AccountRequestEventForm(HtmxFormSchema):
    """Create an event: code, name, deadline, an optional public-form window
    and an optional extra sponsor picked from the SAM user search."""

    event_code = f.Str(required=True,
                       validate=v.Regexp(_EVENT_CODE_RE, error=_EVENT_CODE_MSG))
    name = f.Str(required=True, validate=v.Length(min=1, max=128))
    instructions = f.Str(load_default=None, validate=v.Length(max=4000))
    accounts_needed_by = f.Date('%Y-%m-%d', required=True)
    opens_at = f.DateTime(_DATETIME_LOCAL, load_default=None)
    closes_at = f.DateTime(_DATETIME_LOCAL, load_default=None)
    extra_sponsor_user_id = f.Int(load_default=None)
    listed = f.Bool(load_default=False)
    invite_only = f.Bool(load_default=False)

    @validates('accounts_needed_by')
    def _not_past(self, value, **kwargs):
        if value < date.today():
            raise ValidationError(PAST_DEADLINE_MSG)

    @post_load
    def _normalize(self, data, **kwargs):
        data['event_code'] = data['event_code'].strip().upper()
        data['name'] = data['name'].strip()
        self.assert_date_range(data.get('opens_at'), data.get('closes_at'),
                               field='closes_at',
                               message='The window must close after it opens.')
        return data


class AccountRequestEventAdminForm(AccountRequestEventForm):
    """Admin -> Events create: the project comes from a picker, not the URL."""
    project_id = f.Int(required=True, error_messages={'required': 'Pick a project.'})


class AccountRequestEventEditForm(HtmxFormSchema):
    """Edit an event. The code is not editable -- it has been handed out.
    Loaded ``partial=True``; the handler gates updates on the keys present in
    the original form, since ``load_default`` fills absent fields with None."""

    name = f.Str(load_default=None, validate=v.Length(min=1, max=128))
    instructions = f.Str(load_default=None, validate=v.Length(max=4000))
    accounts_needed_by = f.Date('%Y-%m-%d', load_default=None)
    opens_at = f.DateTime(_DATETIME_LOCAL, load_default=None)
    closes_at = f.DateTime(_DATETIME_LOCAL, load_default=None)
    extra_sponsor_user_id = f.Int(load_default=None)

    @post_load
    def _normalize(self, data, **kwargs):
        if data.get('name') is not None:
            data['name'] = data['name'].strip()
        self.assert_date_range(data.get('opens_at'), data.get('closes_at'),
                               field='closes_at',
                               message='The window must close after it opens.')
        return data


class RosterPasteForm(HtmxFormSchema):
    """One ``Name <email>`` per line; parsed by ``sam.manage.account_requests.parse_roster``."""

    roster = f.Str(required=True, validate=v.Length(min=1, max=20_000),
                   error_messages={'required': 'Paste at least one line.'})
    send_invite = f.Bool(load_default=False)


class RegisterForm(HtmxFormSchema):
    """The public form: the XRAS person field set, plus a "why" when there is
    no event code to say it. Nothing here is rendered into the verification
    mail. ``website`` is the honeypot, read by the route before loading."""

    email = f.Email(required=True, validate=v.Length(max=255))
    first_name = f.Str(required=True, validate=v.Length(min=1, max=64))
    middle_name = f.Str(load_default=None, validate=v.Length(max=64))
    last_name = f.Str(required=True, validate=v.Length(min=1, max=64))
    organization = f.Str(required=True, validate=v.Length(min=1, max=128))
    academic_status = f.Str(required=True, validate=v.Length(min=1, max=64))
    residence_country = f.Str(required=True, validate=v.Length(min=1, max=64))
    orcid = f.Str(load_default=None, validate=v.Regexp(
        r'^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$', error='An ORCID looks like 0000-0002-1825-0097.'))
    #: Required: the account team needs it for Duo enrollment.
    phone = f.Str(required=True, validate=v.Length(min=1, max=32))
    purpose_note = f.Str(load_default=None, validate=v.Length(max=500))
    event_code = f.Str(load_default=None, validate=v.Length(max=32))

    @post_load
    def _normalize(self, data, **kwargs):
        data['email'] = data['email'].strip().lower()
        for key in ('first_name', 'middle_name', 'last_name', 'organization',
                    'academic_status', 'residence_country', 'orcid', 'phone',
                    'purpose_note'):
            if data.get(key) is not None:
                data[key] = data[key].strip() or None
        if data.get('event_code'):
            data['event_code'] = data['event_code'].strip().upper()
        return data


class RegisterGateForm(HtmxFormSchema):
    """The accept-first gate: the terms box ticked."""

    accept = f.Bool(load_default=False)

    @post_load
    def _require(self, data, **kwargs):
        if not data['accept']:
            raise ValidationError({'accept': ['Please accept the terms of use to continue.']})
        return data


class VerifyCodeForm(HtmxFormSchema):
    code = f.Str(required=True, validate=v.Regexp(r'^\s*\d{6}\s*$'))

    @post_load
    def _strip(self, data, **kwargs):
        data['code'] = data['code'].strip()
        return data
