"""Form schemas for the unix group masks (Admin > Users & Groups > Group masks, the
adhoc group card, the admin project card). The gid, the members and the guards resolve
in the handler (docs/plans/UNIX_GROUP_MASK.md § 7)."""

import marshmallow.fields as f
import marshmallow.validate as v

from . import HtmxFormSchema


class OpenGroupMaskForm(HtmxFormSchema):
    """Open a batch of masks: a gid (adhoc group or project), the members, the op."""
    unix_gid = f.Int(load_default=None)
    usernames = f.Str(load_default=None, validate=v.Length(max=2000))   # one per line
    username = f.Str(load_default=None, validate=v.Length(max=35))      # a card pinning one member
    op = f.Str(required=True, validate=v.OneOf(('add', 'remove'), error='Pick add or remove.'),
               error_messages={'required': 'Pick add or remove.'})
    expires_at = f.Date('%Y-%m-%d', load_default=None,
                        error_messages={'invalid': 'Invalid expiry date.'})
    note = f.Str(load_default=None, validate=v.Length(max=255))


class EditGroupMaskForm(HtmxFormSchema):
    """Edit an open mask: the expiry (blank clears it on an adhoc mask) and the note."""
    expires_at = f.Date('%Y-%m-%d', load_default=None,
                        error_messages={'invalid': 'Invalid expiry date.'})
    note = f.Str(load_default=None, validate=v.Length(max=255))


class WithdrawGroupMaskForm(HtmxFormSchema):
    """Withdraw carries no fields; the row id is in the URL."""
