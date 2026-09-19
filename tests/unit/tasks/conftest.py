"""Fixtures shared across the scheduled-task tests.

These are the byte-identical module-level copies that every task test used to
carry. Files that need a different `ledger` / `status_engine` still define their
own, which shadows the copy here.
"""

import pytest
from sqlalchemy.orm import Session

from sam.notify import NullTransport
from scheduling.ledger import TaskLedger


@pytest.fixture
def status_engine(app, status_session):
    from webapp.extensions import db
    return db.engines['system_status']


@pytest.fixture
def ledger(status_engine):
    return TaskLedger(lambda: Session(status_engine))


@pytest.fixture
def transport():
    return NullTransport()
