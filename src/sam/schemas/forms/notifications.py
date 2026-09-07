"""Form schemas for Admin > Notifications: the template editor and addressing."""

import marshmallow.fields as f
import marshmallow.validate as v

from sam.notify.addressing_store import FIELDS
from sam.notify.kinds import addressing_scopes, families

from . import HtmxFormSchema

ALL_SCOPES = tuple(s for family in families() for s in addressing_scopes(family))


class NotificationTemplateForm(HtmxFormSchema):
    """The edited body. Compilation is checked by the handler, not here."""

    body = f.Str(required=True, validate=v.Length(min=1, max=65535))


class AddAddressingForm(HtmxFormSchema):
    """One operator-added copy: an address, cc or bcc, and the scope it applies to."""

    address = f.Email(required=True, validate=v.Length(max=255))
    field = f.Str(required=True, validate=v.OneOf(FIELDS))
    scope = f.Str(required=True, validate=v.OneOf(ALL_SCOPES))
