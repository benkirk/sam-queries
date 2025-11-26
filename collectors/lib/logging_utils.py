"""
Logging configuration for collectors.
"""

import logging
import logging.handlers
import sys


def setup_logging(log_file=None, verbose=False):
    """
    Configure logging for collectors.

    Args:
        log_file: Path to log file (None = stdout only)
        verbose: Enable DEBUG level logging
    """
    level = logging.DEBUG if verbose else logging.INFO

    format_str = '[%(asctime)s] %(levelname)s [%(name)s] %(message)s'
    formatter = logging.Formatter(format_str, datefmt='%Y-%m-%d %H:%M:%S')

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
