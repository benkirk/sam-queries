"""Form schema for the notification template editor (Admin > Notifications)."""

import marshmallow.fields as f
import marshmallow.validate as v

from . import HtmxFormSchema


class NotificationTemplateForm(HtmxFormSchema):
    """The edited body. Compilation is checked by the handler, not here."""

    body = f.Str(required=True, validate=v.Length(min=1, max=65535))
