"""
Marshmallow form validation schemas for admin-only HTMX routes.

Currently covers: rate-limit bucket unblock action (Admin > Rate Limiting).
"""

import marshmallow.fields as f
import marshmallow.validate as v

from . import HtmxFormSchema


class ClearRateLimitForm(HtmxFormSchema):
    """Validate the actor key for the rate-limit unblock action.

    The actor is a Redis bucket identifier as produced by
    ``webapp.limiter._key_func`` (e.g. ``ip:10.0.0.5``, ``user:bdobbins``,
    ``apikey:collector``). Bucket existence is not checked here — it's a
    storage-layer concern, handled by ``events.clear_bucket`` in the
    route handler (best-effort SCAN+DEL on Redis).
    """

    actor = f.Str(required=True, validate=v.Length(min=1, max=128))
