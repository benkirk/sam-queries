"""HTMX form schemas for the XRAS Remediations card.

Sibling of :mod:`sam.schemas.forms.xras_activation` — snake_case
``request.form`` posts from an operator card, so the ``HtmxFormSchema`` base
and its empty-string dropping are exactly what is wanted. (Not to be confused
with :mod:`sam.schemas.forms.xras`, the JSON load schemas for the *inbound*
action endpoint, which deliberately use a different base.)

⚠️ **What these schemas do NOT do.** They do not check that a username exists
in XRAS, that a role-holder may act, or that an action is in a withdrawable
state. Every one of those is a live remote read, and a schema that pretended to
answer them from a form body would be answering from stale hope. The handlers
do it against XRAS, and the client verifies afterwards regardless. What lives
here is the shape of the body and nothing else.

The bodiless posts — re-submit, role removal — have no schema on purpose. A
schema with no fields is furniture; the precedent is the one-click activation
actions on the sibling card.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load

from . import HtmxFormSchema

__all__ = ['XrasMergeForm', 'XrasRemediationReasonForm', 'XrasRoleForm',
           'XrasResourceAmountForm', 'XrasActionDatesForm',
           'XrasRequestAttributesForm', 'XrasActionFieldsForm']

#: ``xras_remediation_event.comment`` is TEXT — 65,535 **bytes**, and utf8mb4
#: spends up to 4 per character. A char-counted cap at the column width would
#: let MySQL truncate mid-string; 4,000 is a UI bound well inside the budget.
_COMMENT_MAX = 4000

#: ``username`` / ``target_username`` are ``VARCHAR(64)``, wider than
#: ``users.username`` because ARC placeholders (``<name>-user-<token>``) are.
_USERNAME_MAX = 64


def _clean(value):
    return (value or '').strip()


class XrasMergeForm(HtmxFormSchema):
    """Choose the real identity to fold a placeholder into. **Destructive.**

    Two ways to name the target and **exactly one may be used**: a radio over
    the ranked candidates the modal found, or a free-form override from the
    user picker for when none of them is right. They are separate fields rather
    than one so the two carry different weight — a candidate was matched on
    email or organization, an override is a human asserting something the
    search could not.

    ⚠️ There is deliberately **no default when several candidates match.** The
    measured case is two real identities for one human, differing only by email
    and organization (a university address and an NCAR-staff one); a name-based
    guess picks arbitrarily, and merge deletes the loser's roles into the
    winner. So the modal preselects nothing and this schema accepts nothing
    implicit.
    """

    candidate = f.Str(load_default=None, validate=v.Length(max=_USERNAME_MAX))
    target_username = f.Str(load_default=None,
                            validate=v.Length(max=_USERNAME_MAX))
    comment = f.Str(load_default=None, validate=v.Length(max=_COMMENT_MAX))

    @post_load
    def _exactly_one_target(self, data, **kwargs):
        candidate = _clean(data.get('candidate'))
        override = _clean(data.get('target_username'))

        if candidate and override and candidate != override:
            raise ValidationError({'target_username': [
                'Choose a listed candidate or enter a different account, '
                'not both.']})

        target = candidate or override
        if not target:
            raise ValidationError({'candidate': [
                'Select the real XRAS identity to merge into.']})

        data['target_username'] = target
        data['comment'] = _clean(data.get('comment')) or None
        data.pop('candidate', None)
        return data


class XrasRemediationReasonForm(HtmxFormSchema):
    """The operator's reason. **Required**, and only for withdraw.

    Withdrawing de-approves someone's award back to a draft and rewrites the
    XRAS record so the history no longer shows an approval. Recording that
    without a stated reason is not an audit trail, and the moment of the
    decision is the cheapest time to capture one. Same argument as the Dismiss
    reason on the sibling card, with more at stake.
    """

    comment = f.Str(required=True, validate=v.Length(min=1, max=_COMMENT_MAX))

    @post_load
    def _reject_whitespace_only(self, data, **kwargs):
        """``_strip_empty_strings`` drops ``''`` but not ``'   '``."""
        comment = _clean(data.get('comment'))
        if not comment:
            raise ValidationError({'comment': ['This field is required.']})
        data['comment'] = comment
        return data


def _role_names():
    """The wire spellings, resolved at load time.

    Imported lazily so this module keeps costing nothing to import: the schema
    package is pulled in by every form consumer, and the role vocabulary lives
    beside the client that speaks it.
    """
    from sam.integration.xras_api.admin_client import ROLE_TYPES
    return [r.name for r in ROLE_TYPES]


class XrasRoleForm(HtmxFormSchema):
    """Add one username to a request's roster in one role.

    ``role_type`` is validated against the client's own vocabulary rather than
    a literal list, because the two role API families spell it differently
    (integer id in one, string name in the other) and a second copy here is how
    they would drift. The **name** is what this route takes.

    ⚠️ An unknown username does not fail — XRAS would *create* that identity,
    with ``isReconciled`` defaulting true, which is the exact mechanism that
    mints the stuck placeholders this card exists to clean up. The handler
    therefore resolves the username against XRAS before writing; the schema
    only guarantees there is one.
    """

    username = f.Str(required=True,
                     validate=v.Length(min=1, max=_USERNAME_MAX))
    role_type = f.Str(required=True)
    comment = f.Str(load_default=None, validate=v.Length(max=_COMMENT_MAX))

    @post_load
    def _normalize(self, data, **kwargs):
        username = _clean(data.get('username'))
        if not username:
            raise ValidationError({'username': ['This field is required.']})

        allowed = _role_names()
        role = _clean(data.get('role_type'))
        if role not in allowed:
            raise ValidationError({'role_type': [
                f"Must be one of: {', '.join(allowed)}."]})

        data['username'] = username
        data['role_type'] = role
        data['comment'] = _clean(data.get('comment')) or None
        return data


class XrasResourceAmountForm(HtmxFormSchema):
    """The requested amount for one resource on one action.

    ``amount`` is the **requested** figure — on our current key the editor
    touches the Requested stage, never the award (Phase 0). ``comment`` doubles
    as the resource's XRAS ``comments`` field and the audit note; an empty one
    clears the resource comment back to null, which is deliberate.

    The ids (request, action, resource) come from the URL, not the body — the
    schema only shapes what the operator typed.
    """

    amount = f.Decimal(required=True, places=None,
                       validate=v.Range(min=0,
                                        error='Amount must be zero or more.'))
    comment = f.Str(load_default=None, validate=v.Length(max=_COMMENT_MAX))

    @post_load
    def _normalize(self, data, **kwargs):
        data['comment'] = _clean(data.get('comment')) or None
        return data


class XrasActionDatesForm(HtmxFormSchema):
    """An allocation-date range for one action.

    Both dates are required — a half-open range is not a thing XRAS stores. The
    end may equal the begin (a single-day allocation) but never precede it.
    ``comment`` is audit-only; the dates endpoint takes no comment field.
    """

    begin_date = f.Date(required=True)
    end_date = f.Date(required=True)
    comment = f.Str(load_default=None, validate=v.Length(max=_COMMENT_MAX))

    @post_load
    def _check_range(self, data, **kwargs):
        begin, end = data.get('begin_date'), data.get('end_date')
        if begin and end and end < begin:
            raise ValidationError({'end_date': [
                'End date must not precede the begin date.']})
        data['comment'] = _clean(data.get('comment')) or None
        return data


#: UI bounds for the free-text metadata fields. Generous — the real limit is
#: XRAS's, which a too-long value surfaces as a 400 the modal renders. `abstract`
#: and `userComments` are long-form; title/shortTitle are one-liners.
_TITLE_MAX = 500
_SHORT_TITLE_MAX = 255
_LONGTEXT_MAX = 20000


class XrasRequestAttributesForm(HtmxFormSchema):
    """Edit a request's text attributes: title, short title, abstract.

    Only the fields the reports feed reads back are here — a field that cannot
    be re-read cannot be verified, and every write here verifies. ``title`` is
    required (a request needs one); ``short_title``/``abstract`` may be blanked
    to clear them. The form is prefilled with the current values, so a save
    rewrites all three to what the operator sees — the ones they did not touch to
    their existing values.
    """

    title = f.Str(required=True, validate=v.Length(min=1, max=_TITLE_MAX))
    short_title = f.Str(load_default=None,
                        validate=v.Length(max=_SHORT_TITLE_MAX))
    abstract = f.Str(load_default=None, validate=v.Length(max=_LONGTEXT_MAX))

    @post_load
    def _normalize(self, data, **kwargs):
        title = _clean(data.get('title'))
        if not title:
            raise ValidationError({'title': ['This field is required.']})
        data['title'] = title
        # short_title/abstract: keep '' (a deliberate clear) distinct from a
        # value; empty-string dropping already turned a blank into a missing
        # key, which the route reads as "clear" via request.form presence.
        return data


class XrasActionFieldsForm(HtmxFormSchema):
    """Edit an action's text fields. Just ``user_comments`` in B2a."""

    user_comments = f.Str(load_default=None,
                          validate=v.Length(max=_LONGTEXT_MAX))
