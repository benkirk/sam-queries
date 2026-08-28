"""HTMX form schemas for the XRAS pending-activation worklist.

WARNING: **Not the same family as :mod:`sam.schemas.forms.xras`.** That module holds the
plain-``marshmallow`` load schemas for the ``POST /api/xras/v1/actions`` JSON body,
and its docstring explains at length why they are deliberately *not*
``HtmxFormSchema`` subclasses — the base is ``ImmutableMultiDict``-shaped, its
empty-string dropping is data loss for a JSON body, and its pre-load will not
recurse into five nested arrays.

These are the opposite: snake_case ``request.form`` posts from the operator card,
where every one of those behaviors is exactly what is wanted. Two families with
opposite base classes and opposite empty-string semantics do not belong behind one
module name, which is why this is its own file.

The one-click actions (notify, activate, restore) carry **no body at all** and
therefore need no schema — see CLAUDE.md §9, whose tiers exist to kill inline
coercion ladders, and ``xras_replay`` in the allocations blueprint for the
established shape of a bodiless operator POST on this same page.
"""

import marshmallow.fields as f
import marshmallow.validate as v
from marshmallow import ValidationError, post_load

from . import HtmxFormSchema

__all__ = ['XrasActivationEventForm']

#: ``xras_activation_event.comment`` is ``TEXT``: 65,535 **bytes**, and utf8mb3
#: spends up to 3 of them per character. A char-counted cap at the column width
#: would therefore let MySQL truncate mid-string. 4,000 is a UI sanity bound well
#: inside the byte budget for any realistic note.
_COMMENT_MAX = 4000


class XrasActivationEventForm(HtmxFormSchema):
    """The note attached to a Comment, where the note *is* the payload.

    ``event_type`` is deliberately **not** a field here and not a URL segment
    either. It is a route-local constant, which is the security property: there is
    no request shape in which a client can name ``activated`` through the comment
    or dismiss endpoint.
    """

    comment = f.Str(required=True, validate=v.Length(min=1, max=_COMMENT_MAX))

    @post_load
    def _reject_whitespace_only(self, data, **kwargs):
        """``HtmxFormSchema._strip_empty_strings`` drops ``''`` but not ``'   '``,
        which would otherwise store a blank note and pass ``Length(min=1)``."""
        comment = (data.get('comment') or '').strip()
        if not comment:
            raise ValidationError({'comment': ['This field is required.']})
        data['comment'] = comment
        return data


class XrasDismissForm(HtmxFormSchema):
    """Dismiss takes an *optional* reason; Comment keeps :class:`XrasActivationEventForm`."""

    comment = f.Str(required=False, load_default=None,
                    validate=v.Length(max=_COMMENT_MAX))

    @post_load
    def _blank_is_none(self, data, **kwargs):
        """A whitespace-only reason is no reason, stored as NULL rather than ``'   '``."""
        data['comment'] = (data.get('comment') or '').strip() or None
        return data

