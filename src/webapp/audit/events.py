"""
SQLAlchemy event handlers for audit logging.

Registers before_flush event listener to track INSERT, UPDATE, DELETE operations.
"""
import sys
from datetime import datetime
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy import inspect
from .logger import get_audit_logger


def init_audit_events(app, db, excluded_metadata, logfile_path):
    """
    Initialize SQLAlchemy event handlers for audit logging.

    Args:
        app: Flask application instance
        db: Flask-SQLAlchemy instance
        excluded_metadata: List of SQLAlchemy metadata objects to exclude (e.g., StatusBase.metadata)
        logfile_path: Path to audit log file
    """
    logger = get_audit_logger(logfile_path)

    # Convert to set for efficient lookups
    EXCLUDED_METADATA = set(excluded_metadata or [])

    # Security-sensitive models to exclude
    EXCLUDED_MODELS = {'ApiCredentials'}

    def responsible_user():
        """
        Get username of currently logged-in user.

        Returns:
            str: Username of authenticated user, or "anonymous" if not logged in
        """
        try:
            from flask_login import current_user

            if current_user and current_user.is_authenticated:
                return current_user.username
        except RuntimeError:
            # No Flask request context (CLI, background job)
            pass
        except Exception:
            # Any other error accessing current_user
            pass

        return "anonymous"

    def should_track(obj):
        """
        Determine if object changes should be tracked.

        Args:
            obj: SQLAlchemy model instance

        Returns:
            bool: True if should be tracked, False otherwise
        """
        # Skip objects without __table__ attribute
        if not hasattr(obj, "__table__"):
            return False

        # Skip excluded metadata (e.g., system_status database)
        if obj.__table__.metadata in EXCLUDED_METADATA:
            return False

        # Skip security-sensitive models
        if obj.__class__.__name__ in EXCLUDED_MODELS:
            return False

        return True

    def get_primary_key(obj):
        """
        Get primary key value(s) for an object.

        Args:
            obj: SQLAlchemy model instance

        Returns:
            tuple: Primary key value(s)
        """
        try:
            return inspect(obj).identity
        except Exception:
            # Fallback for objects without identity yet (new inserts)
            return None

    def diff_for_object(obj):
        """
        Extract changed attributes and their old/new values.

        Args:
            obj: SQLAlchemy model instance

        Returns:
            dict: Dictionary of {attribute: {'old': value, 'new': value}}
        """
        insp = inspect(obj)
        changes = {}

        for attr in insp.attrs:
            hist = attr.history
            if hist.has_changes():
                old_value = hist.deleted[0] if hist.deleted else None
                new_value = hist.added[0] if hist.added else None

                # Serialize datetime objects
                if isinstance(old_value, datetime):
                    old_value = old_value.isoformat()
                if isinstance(new_value, datetime):
                    new_value = new_value.isoformat()

                changes[attr.key] = {
                    "old": old_value,
                    "new": new_value
                }

        return changes

    @event.listens_for(Session, "before_flush")
    def before_flush(session, flush_context, instances):
        """
        Event handler triggered before database flush.

        Captures INSERT, UPDATE, DELETE operations and logs them.

        Args:
            session: SQLAlchemy session
            flush_context: Flush context object
            instances: Instances being flushed (unused)
        """
        try:
            user = responsible_user()

            # Track INSERT operations
            for obj in session.new:
                if should_track(obj):
                    model_name = obj.__class__.__name__
                    pk = get_primary_key(obj)

                    logger.info(
                        f"user={user} action=INSERT model={model_name} pk={pk}"
                    )

            # Track UPDATE operations
            for obj in session.dirty:
                if should_track(obj) and session.is_modified(obj, include_collections=False):
                    model_name = obj.__class__.__name__
                    pk = get_primary_key(obj)
                    changes = diff_for_object(obj)

                    if changes:
                        logger.info(
                            f"user={user} action=UPDATE model={model_name} "
                            f"pk={pk} changes={changes}"
                        )

            # Track DELETE operations
            for obj in session.deleted:
                if should_track(obj):
                    model_name = obj.__class__.__name__
                    pk = get_primary_key(obj)

                    logger.info(
                        f"user={user} action=DELETE model={model_name} pk={pk}"
                    )

        except Exception as e:
            # Log error but don't break application
            print(f"AUDIT ERROR: {e}", file=sys.stderr)
