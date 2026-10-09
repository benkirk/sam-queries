"""Stdlib-only text cleaning for form, wire and vendor values."""
from typing import Any, Optional


def strip_or_none(value: Any, width: Optional[int] = None) -> Optional[str]:
    """``str(value)`` stripped, ``None`` when empty or missing, clipped to ``width`` when given."""
    if value is None:
        return None
    text = str(value).strip()
    return (text[:width] if width else text) or None


def ci_unique(values, *, sort: bool = False) -> list:
    """Dedupe case-insensitively, first spelling wins, blanks dropped; ``sort`` orders by the folded key."""
    seen = {}
    for v in values or ():
        if v is not None and str(v).strip():
            seen.setdefault(v.lower(), v)
    return [seen[k] for k in sorted(seen)] if sort else list(seen.values())
