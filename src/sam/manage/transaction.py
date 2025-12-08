"""
Transaction management utilities for SAM management functions.

Provides context managers to ensure proper commit/rollback handling
for functions that use session.flush() instead of session.commit().
"""
from contextlib import contextmanager
from sqlalchemy.orm import Session


@contextmanager
def management_transaction(session: Session):
    """
    Context manager ensuring commit/rollback for management functions.

    Usage:
        with management_transaction(db.session):
            add_user_to_project(session, proj_id, user_id)
            change_project_admin(session, proj_id, admin_id)
        # Auto-commits on success, rolls back on exception

    Args:
        session: SQLAlchemy session

    Yields:
        Session: The same session (for convenience)

    Raises:
        Any exception raised within the context block (after rollback)
    """
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
