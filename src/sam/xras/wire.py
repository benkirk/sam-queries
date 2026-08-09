"""Reading one field off an XRAS action payload.

One function, in its own module, because five others wanted it and each grew its own
copy — ``dispatch``, ``extractors``, ``roster``, ``handlers.extension``, plus an
inlined fifth in ``handlers.transfer``. Four of the five were byte-identical and the
fifth differed only in a parameter name, which is the shape duplication takes when
nobody has anywhere to put the thing.

It lives at package root rather than under ``handlers/`` because three of its callers
are not handlers, and it deliberately imports nothing — every other module in the
package can depend on it without creating a cycle.
"""

__all__ = ['get_field']


def get_field(obj, key: str):
    """Read one wire field from a loaded dict or an attribute-carrying object.

    ``XrasActionSchema`` loads to a plain dict with camelCase keys, which is what the
    route and replay both hand over. Tests find it convenient to pass a namespace
    instead, and the corpus fixtures are raw dicts. Keeping both readable costs one
    function; requiring one of them would cost every call site a conversion.

    A missing key and an explicit JSON ``null`` are indistinguishable here, and that is
    load-bearing rather than sloppy — see :func:`sam.xras.extractors._clean`, which
    documents the one place where legacy could tell them apart and we cannot.
    """
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
