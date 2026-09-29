"""
Logging configuration for collectors.
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class _ZoneFormatter(logging.Formatter):
    """Render asctime in a fixed zone, independent of the process TZ."""

    def __init__(self, fmt, datefmt, tz):
        super().__init__(fmt, datefmt)
        self._tz = tz

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, self._tz).strftime(datefmt)


def setup_logging(log_file=None, verbose=False):
    """
    Configure logging for collectors.

    Args:
        log_file: Path to log file (None = stdout only)
        verbose: Enable DEBUG level logging
    """
    level = logging.DEBUG if verbose else logging.INFO

    format_str = '[%(asctime)s] %(levelname)s [%(name)s] %(message)s'
    datefmt = '%Y-%m-%d %H:%M:%S'
    # LOG_TZ: display zone for log lines only; data timestamps follow the process TZ (UTC).
    formatter = logging.Formatter(format_str, datefmt=datefmt)
    if os.environ.get('LOG_TZ'):
        try:
            formatter = _ZoneFormatter(format_str, datefmt, ZoneInfo(os.environ['LOG_TZ']))
        except (ZoneInfoNotFoundError, ValueError):
            print(f"Warning: unknown LOG_TZ={os.environ['LOG_TZ']!r}; logging in process time",
                  file=sys.stderr)

    handlers = []

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    handlers.append(console)

    # File handler
    if log_file:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10*1024*1024,  # 10MB
                backupCount=5
            )
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except Exception as e:
            print(f"Warning: Could not create log file {log_file}: {e}", file=sys.stderr)

    # Configure root logger
    logging.basicConfig(level=level, handlers=handlers, force=True)

    # Suppress noisy libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
