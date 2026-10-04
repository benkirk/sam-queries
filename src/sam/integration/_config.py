"""Flask-then-env config readers shared by the outbound integrations.

Try ``flask.current_app.config``, fall back to ``os.environ``, read per call
and never memoized at import (the webapp's config is not readable then). Used
by ``xras_api/config.py``, ``tickets/jira.py``, ``notify/config.py`` and
``queries/allocation_state.py``. ``caching/buckets.py`` reads app config only
and raises on a bad int, so it stays apart.
"""

from __future__ import annotations

import os
from typing import Any, Optional

TRUE = ('1', 'true', 'yes', 'on')


def raw(key: str, default: Any) -> Any:
    """App config inside an app context, else the environment.

    ``RuntimeError`` is ``current_app`` outside a context; ``ImportError`` is a
    Flask-free install, which the CLI and ``src/scheduling/`` both are.
    """
    try:
        from flask import current_app
        return current_app.config.get(key, os.environ.get(key, default))
    except (RuntimeError, ImportError):
        return os.environ.get(key, default)


def config_str(key: str, default: str = '') -> str:
    value = raw(key, default)
    return '' if value is None else str(value).strip()


def config_bool(key: str, default: bool = False) -> bool:
    value = raw(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE


def config_int(key: str, default: int, minimum: Optional[int] = 1) -> int:
    """An int of at least ``minimum`` (``None``: any int); anything else is the default."""
    value = raw(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum is None or parsed >= minimum else default


def config_float(key: str, default: float) -> float:
    """A positive float; anything else is the default."""
    value = raw(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
