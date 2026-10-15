"""The `xras_request_tickets` task: selection, the grace period, the levers, and
open -> comment -> applied with every repeat writing nothing (XRAS_REQUEST_TICKETS.md)."""

import json
import logging
import re
from datetime import datetime, timedelta

import pytest
import yaml
from factories.tickets import FakeTicketProvider
from factories.xras import make_xras_action

from sam import ExternalTicket
from sam.notify.models import NotificationLog
from sam.queries import xras_request_tickets as q
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import occurrence_key
from scheduling.tasks import xras_request_tickets as mod
from _paths import REPO_ROOT

NAME = 'xras_request_tickets'
#: 2033, as in the other XRAS task tests: the snapshot's real rows stay outside the
#: window. 17:00 UTC is 10:00 Mountain.
OCC = datetime(2033, 11, 16, 17, 0)
SLOT = datetime(2033, 11, 16, 10, 0)
RID = 9900417
MISSING = 'Username jdoe is missing'
PAYLOAD = json.dumps({'actionType': 'New', 'requestTitle': 'Clouds', 'roles': [
    {'roleType': 'PI', 'username': 'jdoe',
     'person': {'firstName': 'Jo', 'lastName': 'Doe', 'email': 'jdoe@example.invalid'}}]})


@pytest.fixture
def ctx(session):
    def build(dry_run=False, when=OCC):
        return TaskContext(now=when, occurrence=when, occurrence_key=occurrence_key(when),
                           task_name=NAME, dry_run=dry_run,
                           logger=logging.getLogger('test'), _sam_session=session)
    return build


@pytest.fixture
def provider(monkeypatch):
    fake = FakeTicketProvider()
    monkeypatch.setenv(mod.ENABLED_ENV, '1')
    monkeypatch.setattr('sam.integration.tickets.registry.provider_from_environment',
                        lambda **_: fake)
    return fake


def _refused(session, *, when, errors=(MISSING,), request_id=RID, action_type='New'):
    row = make_xras_action(session, status='failed', http_status=422,
                           action_type=action_type, request_number='NCAR4999',
                           errors=list(errors), received_time=when, payload=PAYLOAD)
    row.request_id = request_id
    session.flush()
    return row


def _applied(session, *, when, request_id=RID):
    row = make_xras_action(session, status='processed', action_type='New',
                           request_number='NCAR4999', projcode_result='UCSG0099',
                           received_time=when)
    row.request_id = request_id
    session.flush()
    return row


def _tickets(session):
    return [(t.provider, t.ticket_key, t.origin) for t in session.query(ExternalTicket)
            .filter_by(entity_type=q.ENTITY_TYPE, entity_id=RID)]


def _ledger(session):
    return [(r.dedup_key.rsplit(':', 1)[-1], r.status) for r in session.query(NotificationLog)
            .filter_by(kind=q.LEDGER_KIND, entity_id=RID)
            .order_by(NotificationLog.notification_log_id)]


class TestTheRegistration:

    def test_it_is_registered_against_sam_with_the_floor_lease(self):
        from scheduling.ledger import lease_for
        values = (REPO_ROOT / 'helm' / 'values.yaml').read_text()
        deadline = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', values, re.MULTILINE)
        assert TASKS[NAME].needs == ('sam',)
        assert lease_for(TASKS[NAME].expected_runtime).total_seconds() > int(deadline.group(1))

    def test_it_ships_disabled_in_production_and_runs_on_dev(self):
        """`SAM_TASKS_DISABLED` is fail-OPEN: enabling it in production edits this test too."""
        for name, expected in (('values.yaml', True), ('values-dev.yaml', False)):
            env = yaml.safe_load((REPO_ROOT / 'helm' / name).read_text())['tasks']['env']
            assert (NAME in env['SAM_TASKS_DISABLED'].split(',')) is expected, name


class TestThePlan:

    class _Ledger:
        def __init__(self, sent=()):
            self.sent = set(sent)

        def already_sent(self, key):
            return key in self.sent

    def _episode(self, **kw):
        return q.Episode(request_id=RID, request_number='NCAR4999', action_type='New',
                         first_refused=SLOT - timedelta(minutes=12), refusals=1,
                         latest_log_id=1, latest_refused=SLOT - timedelta(minutes=12),
                         messages=(MISSING,), payload={}, **kw)

    @pytest.mark.parametrize('minutes,state', [(9, 'waiting'), (10, 'open'), (60, 'open')])
    def test_the_grace_period_counts_from_the_first_refusal(self, minutes, state):
        episode = self._episode()
        until = episode.first_refused + timedelta(minutes=minutes)
        assert mod.plan(episode, None, self._Ledger(), until=until,
                        grace=timedelta(minutes=10)) == state

    def test_a_post_that_applied_inside_the_grace_period_needs_nothing(self):
        episode = self._episode(applied_at=SLOT, projcode='UCSG0099')
        assert mod.plan(episode, None, self._Ledger(), until=SLOT,
                        grace=timedelta(0)) == 'fixed'

    def test_a_ticket_comments_only_on_a_new_list_and_once_applied(self):
        episode = self._episode()
        seen = self._Ledger({q.content_key(RID, episode.messages)})
        assert mod.plan(episode, 'K-1', seen, until=SLOT, grace=timedelta(0)) == 'unchanged'
        assert mod.plan(episode, 'K-1', self._Ledger(), until=SLOT,
                        grace=timedelta(0)) == 'comment'
        applied = self._episode(applied_at=SLOT, projcode='UCSG0099')
        assert mod.plan(applied, 'K-1', self._Ledger(), until=SLOT,
                        grace=timedelta(0)) == 'applied'
        assert mod.plan(applied, 'K-1', self._Ledger({q.applied_key(RID)}), until=SLOT,
                        grace=timedelta(0)) == 'done'


class TestTheSelection:

    def test_only_refused_news_and_renewals_with_a_request_id(self, session):
        _refused(session, when=SLOT - timedelta(hours=1))
        _refused(session, when=SLOT - timedelta(hours=1), request_id=RID + 1,
                 action_type='Supplement')
        make_xras_action(session, status='duplicate', http_status=422, action_type='New',
                         received_time=SLOT - timedelta(hours=1))
        episodes = q.ticket_episodes(session, since=SLOT - timedelta(days=1), until=SLOT)
        assert [e.request_id for e in episodes] == [RID]

    def test_the_episode_ends_at_the_post_that_applied(self, session):
        _refused(session, when=SLOT - timedelta(hours=3), errors=('first',))
        _refused(session, when=SLOT - timedelta(hours=2), errors=(MISSING,))
        _applied(session, when=SLOT - timedelta(hours=1))
        _refused(session, when=SLOT - timedelta(minutes=30), errors=('later',))
        episode, = q.ticket_episodes(session, since=SLOT - timedelta(days=1), until=SLOT)
        assert (episode.refusals, episode.messages) == (2, (MISSING,))
        assert episode.projcode == 'UCSG0099'


class TestTheLevers:

    def test_off_is_a_shadow_that_writes_nothing(self, ctx, session, monkeypatch):
        monkeypatch.delenv(mod.ENABLED_ENV, raising=False)
        _refused(session, when=SLOT - timedelta(hours=1))
        result = mod.xras_request_tickets(ctx())
        assert result.detail['open'] == 1 and result.message.startswith('shadow')
        assert _tickets(session) == [] and _ledger(session) == []

    def test_a_dry_run_writes_nothing_even_when_armed(self, ctx, session, provider):
        _refused(session, when=SLOT - timedelta(hours=1))
        assert mod.xras_request_tickets(ctx(dry_run=True)).detail['open'] == 1
        assert provider.calls == [] and _ledger(session) == []

    def test_armed_without_a_writer_raises(self, ctx, session, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, '1')
        monkeypatch.delenv('TICKET_PROVIDER', raising=False)
        with pytest.raises(mod.TicketWritesUnavailable, match='cannot write'):
            mod.xras_request_tickets(ctx())

    def test_the_cap_raises_before_any_filing(self, ctx, session, provider, monkeypatch):
        monkeypatch.setenv(mod.MAX_ENV, '1')
        _refused(session, when=SLOT - timedelta(hours=1))
        _refused(session, when=SLOT - timedelta(hours=1), request_id=RID + 1)
        with pytest.raises(mod.TicketCapExceeded) as caught:
            mod.xras_request_tickets(ctx())
        assert caught.value.task_detail['opens'] == 2
        assert provider.created == []


class TestTheLifecycle:

    def test_open_then_comment_on_change_then_applied_each_once(self, ctx, session, provider):
        _refused(session, when=SLOT - timedelta(hours=2))
        result = mod.xras_request_tickets(ctx())
        assert result.detail['open'] == 1
        draft, = provider.created
        assert draft.handle == f'SAM-XR-{RID}' and draft.kind == 'xras_request'
        assert draft.summary == f'XRAS request NCAR4999 cannot be posted [SAM-XR-{RID}]'
        assert 'jdoe:\n    Username jdoe is missing' in draft.body
        assert 'To fix a person:' in draft.body and 'Jo Doe (jdoe)' in draft.body
        assert _tickets(session) == [('fake', 'FAKE-1', 'created')]
        assert q.request_ticket_state(session, RID) == q.TicketState('open', 'FAKE-1')

        assert mod.xras_request_tickets(ctx()).detail['unchanged'] == 1, 'a repeat writes nothing'
        notes = len(provider.comments)

        _refused(session, when=SLOT - timedelta(hours=1),
                 errors=(MISSING, 'Username ann is inactive'))
        assert mod.xras_request_tickets(ctx()).detail['comment'] == 1
        key, text, internal = provider.comments[notes]
        assert key == 'FAKE-1' and internal and '- Username ann is inactive' in text

        _applied(session, when=SLOT - timedelta(minutes=30))
        assert mod.xras_request_tickets(ctx()).detail['applied'] == 1
        assert 'applied as UCSG0099' in provider.comments[-1][1]
        assert mod.xras_request_tickets(ctx()).detail['done'] == 1
        assert len(provider.created) == 1
        assert [k for k, _ in _ledger(session)] == ['open', q.blocker_digest((MISSING,)),
                                                    q.blocker_digest((MISSING,
                                                                      'Username ann is inactive')),
                                                    'applied']

    def test_a_failed_comment_is_retried_next_wake(self, ctx, session, provider):
        _refused(session, when=SLOT - timedelta(hours=2))
        mod.xras_request_tickets(ctx())
        _refused(session, when=SLOT - timedelta(hours=1), errors=('other',))

        def down(*_a, **_k):
            raise RuntimeError('desk down')
        provider.comment = down
        result = mod.xras_request_tickets(ctx())
        assert (result.detail['failed'], result.partial_failures) == (1, 1)
        del provider.comment
        assert mod.xras_request_tickets(ctx()).detail['comment'] == 1

    def test_dry_run_mints_a_local_key_and_posts_nothing(self, ctx, session, monkeypatch):
        monkeypatch.setenv(mod.ENABLED_ENV, '1')
        monkeypatch.setenv('TICKET_PROVIDER', 'dry-run')
        _refused(session, when=SLOT - timedelta(hours=1))
        assert mod.xras_request_tickets(ctx()).detail['provider'] == 'dry-run'
        assert _tickets(session) == [('dry-run', f'DRY-SAM-XR-{RID}', 'created')]
