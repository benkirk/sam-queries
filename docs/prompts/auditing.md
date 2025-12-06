# SAM ORM Change Logging

lets develop a brief plan to implement change tracking of the SAM ORM models to a server-side log file.  We want to track changes to the 'sam' database and want to ignore changes to the 'system_status' database.

Lets examine and revise this propsal:

---

Below is a Flask blueprint that:

✓ Logs SQLAlchemy model changes (inserts, updates, deletes)
✓ Excludes models tied to a separate engine
✓ Writes logs to a file, not the DB
✓ Captures timestamp + the logged-in user (Flask-Login or your own user loader)
✓ Has zero impact on your main app structure


1. Blueprint directory structure
```pre
webapp/
    audit/
        __init__.py
        logger.py
        events.py
    ...
```

2. audit/__init__.py
```python
from flask import Blueprint
from .events import init_audit_events

audit_bp = Blueprint("audit", __name__)

def init_audit(app, db, excluded_metadata=None, logfile_path=None):
    """
    Attach audit logging to the Flask app.

    app                = Flask application
    db                 = SQLAlchemy() instance
    excluded_metadata  = iterable of MetaData objects (secondary DBs)
    logfile_path       = where to write the audit log
    """
    init_audit_events(app, db, excluded_metadata, logfile_path)
    app.register_blueprint(audit_bp)
```


3.  audit/logger.py
A very lightweight rotating file logger.
```python
import logging
from logging.handlers import RotatingFileHandler

def get_audit_logger(logfile_path):
    logger = logging.getLogger("model_audit")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = RotatingFileHandler(
            logfile_path,
            maxBytes=10_000_000,   # 10 MB
            backupCount=5
        )
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
```

4. audit/events.py

This is the core.
It attaches to SQLAlchemy before_flush, collects changes, and writes them to the audit log.

```python
from sqlalchemy import event
from sqlalchemy.orm import Session, inspect
from flask import g
from flask_login import current_user
from .logger import get_audit_logger
from datetime import datetime

def init_audit_events(app, db, excluded_metadata, logfile_path):

    logger = get_audit_logger(logfile_path)

    EXCLUDED_METADATA = set(excluded_metadata or [])

    def responsible_user():
        # Supports flask_login OR your custom SAM authentication
        try:
            if current_user and not current_user.is_anonymous:
                return getattr(current_user, "username", str(current_user))
        except Exception:
            pass
        return "anonymous"

    def should_track(obj):
        if not hasattr(obj, "__table__"):
            return False
        if obj.__table__.metadata in EXCLUDED_METADATA:
            return False
        return True

    def diff_for_object(obj):
        insp = inspect(obj)
        changes = {}
        for attr in insp.attrs:
            hist = attr.history
            if hist.has_changes():
                changes[attr.key] = {
                    "old": hist.deleted[0] if hist.deleted else None,
                    "new": hist.added[0] if hist.added else None,
                }
        return changes

    @event.listens_for(Session, "before_flush")
    def before_flush(session, flush_context, instances):
        # CREATE
        for obj in session.new:
            if should_track(obj):
                logger.info(
                    f"user={responsible_user()} action=CREATE model={obj.__class__.__name__} "
                    f"data={obj.__dict__}"
                )

        # UPDATE
        for obj in session.dirty:
            if should_track(obj) and session.is_modified(obj, include_collections=False):
                changes = diff_for_object(obj)
                if changes:
                    logger.info(
                        f"user={responsible_user()} action=UPDATE model={obj.__class__.__name__} "
                        f"pk={inspect(obj).identity} changes={changes}"
                    )

        # DELETE
        for obj in session.deleted:
            if should_track(obj):
                logger.info(
                    f"user={responsible_user()} action=DELETE model={obj.__class__.__name__} "
                    f"pk={inspect(obj).identity}"
                )
```


5. Integration in your Flask app

In your run.py:
```python

def create_app():
    app = Flask(__name__)
    db.init_app(app)

    init_audit(
        app,
        db,
        excluded_metadata=[SecondaryBase.metadata],  # Exclude separate engine
        logfile_path="/var/log/sam/model_audit.log"
    )

    return app
```
