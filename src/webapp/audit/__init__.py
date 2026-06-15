"""
Audit logging blueprint for SAM ORM model changes.

Provides audit trail of INSERT, UPDATE, DELETE operations on SAM database models,
excluding system_status database and security-sensitive models (ApiCredentials).
"""
from flask import Blueprint
from .events import init_audit_events


# Blueprint for audit logging (no routes needed)
audit_bp = Blueprint("audit", __name__)


def init_audit(app, db, logfile_path=None, stdout=None):
    """
    Attach audit logging to Flask application.

    Args:
        app: Flask application instance
        db: Flask-SQLAlchemy instance
        logfile_path: Path to audit log file (default: /var/log/sam/model_audit.log)
        stdout: Also emit audit entries to STDOUT. When None, resolves from the
            AUDIT_LOG_STDOUT config flag (default on). STDOUT is the durable
            sink in CIRRUS/k8s where the log file is on ephemeral storage.

    Example:
        from webapp.audit import init_audit
        from system_status.base import StatusBase

        init_audit(
            app,
            db,
            logfile_path='/var/log/sam/model_audit.log'
        )
    """
    # Default log file path
    if logfile_path is None:
        logfile_path = app.config.get('AUDIT_LOG_PATH', '/var/log/sam/model_audit.log')

    # Resolve STDOUT mirroring from config when not explicitly set
    if stdout is None:
        stdout = app.config.get('AUDIT_LOG_STDOUT', True)

    # Initialize SQLAlchemy event handlers
    init_audit_events(app, db, logfile_path, stdout=stdout)

    # Register blueprint
    app.register_blueprint(audit_bp)
