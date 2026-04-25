"""JSON output helper for CLI commands.

Bypasses Rich entirely so piped consumers (jq, dashboards, cron jobs)
get clean, parseable JSON on stdout.
"""

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any


class _SAMEncoder(json.JSONEncoder):
    """Encode types the SAM ORM commonly returns."""

    def default(self, obj: Any):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, set):
            return sorted(obj)
        return super().default(obj)


def output_json(data: Any) -> None:
    """Write `data` as indented JSON to stdout with a trailing newline."""
    json.dump(data, sys.stdout, cls=_SAMEncoder, indent=2, sort_keys=False)
    sys.stdout.write('\n')
