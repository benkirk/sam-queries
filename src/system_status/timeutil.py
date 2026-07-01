"""Naive-UTC clock for the system_status / collector convention.

The status subsystem stores naive **UTC** timestamps (unlike the SAM/MySQL
side, which is naive local/Mountain).  Its reads, window computations, writes,
and model defaults must therefore use a UTC wall-clock regardless of the
process timezone (the app pod runs in the DB's local TZ; see
``helm/values.yaml`` ``TZ``).

Kept deliberately dependency-free (stdlib only) so the low-level model modules
(``base.py``, ``models/*.py``) can import it at definition time without pulling
in ``sam.fmt`` → ``config`` — which collides with ``webapp/config.py`` during
the webapp's early boot import order.
"""
from datetime import datetime, timezone


def utcnow_naive() -> datetime:
    """Return the current UTC time as a naive datetime (tzinfo stripped)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
