"""Core utilities for SAM CLI."""

import logging
import sys

# Exit codes
EXIT_SUCCESS = 0
EXIT_NOT_FOUND = 1
EXIT_ERROR = 2
EXIT_KEYBOARD_INTERRUPT = 130


# Every CLI log line is "LEVEL logger.name: message" on stderr; stdout is the
# envelope. scripts/cirrus_healthcheck.sh strips exactly this shape from the
# merged container log (kubectl cannot separate the streams) before parsing.
CLI_LOG_FORMAT = '%(levelname)s %(name)s: %(message)s'


class _StderrHandler(logging.StreamHandler):
    """Resolves sys.stderr at emit time, so a swapped stream (CliRunner) is honored."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def configure_logging(verbose: bool = False) -> logging.Handler:
    """Route Python logging to stderr with CLI_LOG_FORMAT; idempotent."""
    root = logging.getLogger()
    root.setLevel(logging.INFO if verbose else logging.WARNING)
    for handler in root.handlers:
        if isinstance(handler, _StderrHandler):
            return handler
    handler = _StderrHandler()
    handler.setFormatter(logging.Formatter(CLI_LOG_FORMAT))
    root.addHandler(handler)
    return handler


_DURATION_DAYS = {'d': 1, 'w': 7, 'm': 30, 'y': 365}


def parse_duration_days(spec: str, option: str) -> int:
    """``'90'``, ``'90d'``, ``'2w'``, ``'6m'`` (30 d) or ``'3y'`` (365 d) as whole days."""
    import click

    s = (spec or '').strip().lower()
    unit = _DURATION_DAYS.get(s[-1:], None) if s else None
    digits = s[:-1] if unit else s
    try:
        n = int(digits) * (unit or 1)
    except ValueError:
        raise click.BadParameter(f"{option} must be N, Nd, Nw, Nm or Ny (e.g. 1y), got: {spec!r}")
    if n < 1:
        raise click.BadParameter(f"{option} must be at least one day")
    return n
