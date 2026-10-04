"""Stdlib-only text cleaning for form, wire and vendor values."""
from typing import Any, Optional


def strip_or_none(value: Any, width: Optional[int] = None) -> Optional[str]:
    """``str(value)`` stripped, ``None`` when empty or missing, clipped to ``width`` when given."""
    if value is None:
        return None
    text = str(value).strip()
    return (text[:width] if width else text) or None
