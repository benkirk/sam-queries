"""Output helpers for CLI commands.

``output_json`` bypasses Rich entirely so piped consumers (jq, dashboards, cron
jobs) get clean, parseable JSON on stdout. ``print_table`` keeps a Rich table
whole when stdout is not a terminal.
"""

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from rich.console import Console
from rich.measure import Measurement
from rich.table import Table


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


def refuse_json_write(kind: str, flags: str) -> int:
    """The one envelope for a write asked under ``--format json`` (src/cli/README.md, Exit Codes)."""
    from cli.core.utils import EXIT_ERROR
    output_json({'kind': kind, 'error': 'json_unsupported_for_writes',
                 'message': f'--format json cannot be combined with {flags}'})
    return EXIT_ERROR


def print_table(console: Console, table: Table) -> None:
    """Print ``table``; off a terminal (a pipe, ssh, a file) at its natural width, never ellipsized."""
    if console.is_terminal:
        console.print(table)
        return
    # Console.print caps its own width= at the console's, so widen the console itself.
    width = console.width
    console.width = max(width, Measurement.get(console, console.options.update_width(100_000), table).maximum)
    try:
        console.print(table)
    finally:
        console.width = width
