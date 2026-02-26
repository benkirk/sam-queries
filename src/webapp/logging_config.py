"""Application logging configuration.

Single public function configure_logging(app) wires structured log handlers
into Flask's app.logger.

Environment variables:
    LOG_LEVEL   Log level name (default: INFO)
    LOG_FILE    Path for rotating log file (default: '' = console only)
"""
import logging
import logging.handlers
import os
import sys


def configure_logging(app):
    """Configure app.logger with console (always) and optional rotating file.

    Called once from create_app() immediately after audit init.
    """
    level = logging.getLevelName(app.config.get('LOG_LEVEL', 'INFO').upper())
    fmt = logging.Formatter(
        '%(asctime)s %(levelname)-8s %(name)s — %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    handlers = []

    # Console handler — always enabled
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(level)
    handlers.append(ch)

    # Optional rotating file handler
    log_file = app.config.get('LOG_FILE', '')
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
        )
        fh.setFormatter(fmt)
        fh.setLevel(level)
        handlers.append(fh)

    # Wire into Flask's app.logger (replace any defaults)
    app.logger.handlers = []
    app.logger.setLevel(level)
    for h in handlers:
        app.logger.addHandler(h)
    app.logger.propagate = False

    # Suppress noisy third-party loggers
    for noisy in ('werkzeug', 'sqlalchemy.engine', 'sqlalchemy.pool'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    app.logger.info(
        'SAM webapp starting — config: %s  log_level: %s',
        os.getenv('FLASK_CONFIG', 'development'),
        logging.getLevelName(level),
    )
