"""
Audit logging infrastructure for SAM ORM model changes.

Provides rotating file logger for tracking database changes.
"""
import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path


def ensure_log_directory(logfile_path):
    """
    Ensure log directory exists and is writable.

    Args:
        logfile_path: Desired log file path

    Returns:
        str: Usable log file path (may fall back to temp directory)
    """
    log_dir = Path(logfile_path).parent

    try:
        # Create directory if it doesn't exist
        log_dir.mkdir(parents=True, exist_ok=True)

        # Test write access
        test_file = log_dir / '.write_test'
        test_file.touch()
        test_file.unlink()

        return logfile_path
    except (PermissionError, OSError) as e:
        # Fall back to temp directory
        fallback_path = os.path.join(tempfile.gettempdir(), 'sam_audit.log')
        print(f"Warning: Could not create log directory {log_dir}: {e}", flush=True)
        print(f"Falling back to {fallback_path}", flush=True)
        return fallback_path


def get_audit_logger(logfile_path):
    """
    Get or create audit logger with rotating file handler.

    Uses singleton pattern - returns existing logger if already configured.

    Args:
        logfile_path: Path to audit log file

    Returns:
        logging.Logger: Configured audit logger
    """
    logger = logging.getLogger("model_audit")
    logger.setLevel(logging.INFO)

    # Singleton - only configure once
    if not logger.handlers:
        # Ensure directory exists or fall back
        logfile_path = ensure_log_directory(logfile_path)

        # Rotating file handler: 10MB files, 5 backups
        handler = RotatingFileHandler(
            logfile_path,
            maxBytes=10_000_000,   # 10 MB
            backupCount=5,
            encoding='utf-8'
        )

        # Format: timestamp [level] message
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)

        logger.addHandler(handler)

    return logger
