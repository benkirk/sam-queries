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
