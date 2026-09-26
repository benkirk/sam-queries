"""Fixtures shared by the registration, event-page and invitation tests."""

from datetime import date, timedelta
from uuid import uuid4

import pytest


@pytest.fixture
def committed_event(app):
    """``(code, project_id)`` of a committed, open event on an existing project,
    visible to db.session. The code is unique per test: xdist workers share one
    database and this row is really committed."""
    from sam.core.account_requests import AccountRequestEvent
    from sam.projects.projects import Project
    from webapp.extensions import db
    with app.app_context():
        project = db.session.query(Project).filter(Project.is_active).first()
        event = AccountRequestEvent.create(
            db.session, event_code=f'ZZ-EVT-{uuid4().hex[:8].upper()}',
            name='ZZ Self-Enroll Test', project_id=project.project_id,
            accounts_needed_by=date.today() + timedelta(days=30), created_by='benkirk')
        db.session.commit()
        code, project_id, event_id = (event.event_code, project.project_id,
                                      event.account_request_event_id)
    yield code, project_id
    with app.app_context():
        db.session.query(AccountRequestEvent).filter(
            AccountRequestEvent.account_request_event_id == event_id).delete()
        db.session.commit()


@pytest.fixture
def signed_in(app, session):
    """A test client with benkirk's session cookie, and his user row."""
    from sam import User
    user = User.get_by_username(session, 'benkirk')
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.user_id)
        sess['_fresh'] = True
    return client, user
