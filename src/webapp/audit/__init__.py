"""
Audit logging blueprint for SAM ORM model changes.

Provides audit trail of INSERT, UPDATE, DELETE operations on SAM database models,
excluding system_status database and security-sensitive models (ApiCredentials).
"""
from flask import Blueprint
from .events import init_audit_events


# Blueprint for audit logging (no routes needed)
audit_bp = Blueprint("audit", __name__)


def init_audit(app, db, excluded_metadata=None, logfile_path=None):
    """
    Attach audit logging to Flask application.

    Args:
        app: Flask application instance
        db: Flask-SQLAlchemy instance
        excluded_metadata: List of SQLAlchemy metadata objects to exclude
                          (e.g., [StatusBase.metadata] for system_status database)
        logfile_path: Path to audit log file (default: /var/log/sam/model_audit.log)

    Example:
        from webapp.audit import init_audit
        from system_status.base import StatusBase

        init_audit(
            app,
            db,
            excluded_metadata=[StatusBase.metadata],
            logfile_path='/var/log/sam/model_audit.log'
        )
    """
    # Default log file path
    if logfile_path is None:
        logfile_path = app.config.get('AUDIT_LOG_PATH', '/var/log/sam/model_audit.log')

    # Initialize SQLAlchemy event handlers
    init_audit_events(app, db, excluded_metadata, logfile_path)

    # Register blueprint
    app.register_blueprint(audit_bp)
