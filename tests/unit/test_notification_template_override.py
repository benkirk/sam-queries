"""`NotificationTemplateOverride` — the operator template store."""

import uuid
from datetime import datetime, timedelta

import pytest
from factories.notify import make_template_override
from sqlalchemy.exc import IntegrityError

from sam import NotificationTemplateOverride


def _name():
    """Unique per call: `name` is UNIQUE and xdist workers share the database."""
    return f'test-{uuid.uuid4().hex[:12]}.txt'


class TestCreate:

    def test_it_stamps_the_app_clock(self, session):
        before = datetime.now()
        row = make_template_override(session, name=_name())
        assert row.notification_template_override_id
        assert row.modified_time.tzinfo is None
        assert before - timedelta(seconds=1) <= row.modified_time <= datetime.now()
        assert row.modified_by == 'benkirk'

    def test_get_by_name_finds_it_and_only_it(self, session):
        name = _name()
        row = make_template_override(session, name=name)
        assert NotificationTemplateOverride.get_by_name(session, name) is row
        assert NotificationTemplateOverride.get_by_name(session, _name()) is None

    @pytest.mark.parametrize('kwargs', [
        {'name': ''}, {'name': 'x' * 65}, {'modified_by': ''},
        {'modified_by': 'y' * 36}, {'body': None},
    ])
    def test_it_validates(self, session, kwargs):
        args = {'name': _name(), 'body': 'b', 'modified_by': 'benkirk', **kwargs}
        with pytest.raises(ValueError):
            NotificationTemplateOverride.create(session, **args)

    def test_the_name_is_unique(self, session):
        name = _name()
        make_template_override(session, name=name)
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                make_template_override(session, name=name)


class TestUpdate:

    def test_it_replaces_the_body_and_restamps(self, session):
        row = make_template_override(session, name=_name(), body='one')
        first = row.modified_time
        row.update(body='two', modified_by='someone')
        assert row.body == 'two'
        assert row.modified_by == 'someone'
        assert row.modified_time >= first

    def test_it_validates(self, session):
        row = make_template_override(session, name=_name())
        with pytest.raises(ValueError):
            row.update(body='x', modified_by='')
        with pytest.raises(ValueError):
            row.update(body=None, modified_by='benkirk')

    def test_a_body_with_four_byte_characters_survives(self, session):
        """utf8mb4 on `body`: an emoji in a template must not be refused."""
        row = make_template_override(session, name=_name(), body='Thanks \U0001F389')
        session.flush()
        session.refresh(row)
        assert row.body.endswith('\U0001F389')
