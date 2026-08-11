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

    # Optional plugin loggers (hpc-usage-queries). These are module-level
    # `getLogger(__name__)` under their own package roots, so they inherit the
    # ROOT logger — which this app never configures. Left alone they resolve to
    # WARNING with no handlers and emit NOTHING, silently swallowing the
    # plugin's own diagnostics: most usefully `jobs_timeseries`'s DEBUG line
    # naming which path served a request (`daily_summary` rollup vs a full
    # `jobs` scan, ~100x apart) and how many days of the window were covered.
    # Without this, that decision is unobservable from inside SAM.
    #
    # Cheap to wire: the webapp-side plugin modules log 6 DEBUG lines and one
    # ERROR in total, so at the default INFO this adds no output at all — it
    # only makes LOG_LEVEL=DEBUG mean what it says. Named explicitly rather
    # than configuring root, which would also unmute every other library.
    # `sam` joins them for the same reason and one sharper one: sam.notify
    # logs every send, suppression and failure, and without a handler here a
    # delivery is completely unobservable from inside the app — the mailer
    # would be the one subsystem whose whole job is side effects and whose
    # diagnostics went nowhere. It is also the package the CLI shares, so a
    # `sam-admin` run and a webapp send now report identically.
    for plugin_pkg in ('job_history', 'fs_scans', 'sam'):
        plog = logging.getLogger(plugin_pkg)
        plog.handlers = []
        plog.setLevel(level)
        for h in handlers:
            plog.addHandler(h)
        plog.propagate = False

    # Suppress noisy third-party loggers
    for noisy in ('werkzeug', 'sqlalchemy.engine', 'sqlalchemy.pool'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    app.logger.info(
        'SAM webapp starting — config: %s  log_level: %s',
        os.getenv('FLASK_CONFIG', 'development'),
        logging.getLevelName(level),
    )
