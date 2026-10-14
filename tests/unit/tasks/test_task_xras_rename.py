"""The `xras_rename` task: its selection, its two skips, the dry run, and siblings.

The rename itself (pre-read, audit, card and override moves) is covered at the
service layer in `test_xras_remediation_service.py`; here it is faked so nothing
escapes the per-test SAVEPOINT.
"""

import logging
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from factories.xras import make_xras_action

from sam.integration.xras_api.admin_client import XrasAdminClient
from sam.integration.xras_api.client import XrasApiClient
from sam.manage import xras_remediation as remediation
from sam.queries.xras_actions import rename_candidates
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import occurrence_key
from scheduling.tasks import xras_rename as mod
from _paths import REPO_ROOT

NAME = 'xras_rename'

#: 2033, as in `test_task_xras_notices.py`: the snapshot's real action-log rows stay
#: outside the window. 17:00 UTC is 10:00 Mountain.
OCC = datetime(2033, 11, 16, 17, 0)
SLOT = datetime(2033, 11, 16, 10, 0)


@pytest.fixture
def ctx(session):
    def build(dry_run=False):
        return TaskContext(now=OCC, occurrence=OCC, occurrence_key=occurrence_key(OCC),
                           task_name=NAME, dry_run=dry_run,
                           logger=logging.getLogger('test'), _sam_session=session)
    return build


@pytest.fixture
def levers(monkeypatch):
    state = {'api': True, 'write': True}
    monkeypatch.setattr('sam.integration.xras_api.config.xras_api_configured',
                        lambda *a: state['api'])
    monkeypatch.setattr('sam.integration.xras_api.config.xras_write_configured',
                        lambda *a: state['write'])
    monkeypatch.delenv(mod.SINCE_ENV, raising=False)
    return state


def _landed(session, number, projcode, request_id, *, when=SLOT - timedelta(hours=1),
            status='processed', action_type='New'):
    row = make_xras_action(session, status=status, action_type=action_type,
                           request_number=number, projcode_result=projcode,
                           received_time=when)
    row.request_id = request_id
    session.flush()
    return row


class TestTheRegistration:

    def test_it_is_registered_against_sam(self):
        assert TASKS[NAME].needs == ('sam',)

    def test_its_lease_outlives_the_job_deadline(self):
        from scheduling.ledger import lease_for
        values = (REPO_ROOT / 'helm' / 'values.yaml').read_text()
        deadline = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', values, re.MULTILINE)
        assert lease_for(TASKS[NAME].expected_runtime).total_seconds() > int(deadline.group(1))

    def test_it_ships_switched_off_in_production(self):
        """WARNING: `SAM_TASKS_DISABLED` is fail-OPEN, and this task writes to XRAS."""
        values = (REPO_ROOT / 'helm' / 'values.yaml').read_text()
        line, = [ln for ln in values.splitlines()
                 if ln.strip().startswith('SAM_TASKS_DISABLED:')]
        assert NAME in line, line


class TestTheWindow:

    def test_it_looks_back_fourteen_days_from_the_local_slot(self):
        assert mod.window_start(OCC, env={}) == SLOT - timedelta(days=14)

    def test_the_backfill_override_widens_it(self):
        assert mod.window_start(OCC, env={mod.SINCE_ENV: '2026-08-24'}) == datetime(2026, 8, 24)

    def test_a_malformed_override_fails_the_run(self):
        with pytest.raises(ValueError):
            mod.window_start(OCC, env={mod.SINCE_ENV: 'last tuesday'})


class TestTheSelection:

    def test_a_processed_post_under_its_token_is_a_candidate(self, session):
        _landed(session, 'NCAR9101', 'UXYZ9101', 9101)
        (c,) = rename_candidates(session, since=SLOT - timedelta(days=1))
        assert (c['request_id'], c['request_number'], c['projcode']) == (
            9101, 'NCAR9101', 'UXYZ9101')

    def test_what_is_not(self, session):
        since = SLOT - timedelta(days=1)
        _landed(session, 'UXYZ9102', 'UXYZ9102', 9102)                  # already the projcode
        _landed(session, 'NCAR9103', None, 9103, status='failed')       # nothing minted
        _landed(session, 'NCAR9104', 'UXYZ9104', 9104,
                when=since - timedelta(minutes=1))                      # before the window
        assert rename_candidates(session, since=since) == []

    def test_one_per_line_newest_first(self, session):
        _landed(session, 'NCAR9105', 'UXYZ9105', 9105, when=SLOT - timedelta(hours=3))
        _landed(session, 'NCAR9105', 'UXYZ9105', 9105, action_type='Extension')
        (c,) = rename_candidates(session, since=SLOT - timedelta(days=1))
        assert c['action_type'] == 'Extension'


class TestTheSkips:

    def test_no_outgoing_api_skips_visibly(self, ctx, levers):
        levers['api'] = False
        result = TASKS[NAME].fn(ctx())
        assert 'skipped' in result.message and result.detail['candidates'] == 0

    def test_a_live_run_without_the_write_lever_skips(self, ctx, levers):
        levers['write'] = False
        assert 'XRAS_WRITE_ENABLED' in TASKS[NAME].fn(ctx()).message


class TestTheRuns:

    @pytest.fixture
    def renames(self, monkeypatch):
        calls = []

        def fake(factory, *, request_id, old_number, new_number, operator, client):
            calls.append((request_id, old_number, new_number, operator))
            status = 'already_renamed' if request_id == 9202 else 'verified'
            return remediation.RemediationOutcome(1, status=status)

        monkeypatch.setattr(remediation, 'rename_request', fake)
        return calls

    def test_a_live_run_renames_each_line_and_its_siblings(self, ctx, levers, renames,
                                                          session, monkeypatch):
        _landed(session, 'NCAR9201', 'UXYZ9201', 9201)
        _landed(session, 'NCAR9202', 'UXYZ9202', 9202)
        siblings = {'NCAR9201': [{'requestId': 9299}], 'NCAR9202': []}
        admin = SimpleNamespace(reader=SimpleNamespace(
            get_request_family_by_number=lambda n: siblings[n]))
        monkeypatch.setattr(XrasAdminClient, 'from_environment',
                            classmethod(lambda cls, *a, **k: admin))

        result = TASKS[NAME].fn(ctx())

        assert sorted(renames) == [
            (9201, 'NCAR9201', 'UXYZ9201', 'task:xras_rename'),
            (9202, 'NCAR9202', 'UXYZ9202', 'task:xras_rename'),
            (9299, 'NCAR9201', 'UXYZ9201', 'task:xras_rename')]
        d = result.detail
        assert (d['candidates'], d['siblings'], d['verified'], d['already_renamed']) == (2, 1, 2, 1)
        assert result.partial_failures == 0

    def test_a_dry_run_reads_and_writes_nothing(self, ctx, levers, renames, session,
                                                monkeypatch):
        levers['write'] = False
        _landed(session, 'NCAR9301', 'UXYZ9301', 9301)
        _landed(session, 'NCAR9302', 'UXYZ9302', 9302)
        lines = {9301: {'requestNumber': 'NCAR9301', 'roles': [
                     {'person': {'username': 'pi-user'},
                      'roles': [{'roleId': 1, 'role': 'PI', 'roleTypeId': 13}]}]},
                 9302: {'requestNumber': 'UXYZ9302', 'roles': []}}
        reader = SimpleNamespace(get_request_by_id=lambda rid: lines[rid])
        monkeypatch.setattr(XrasApiClient, 'from_environment',
                            classmethod(lambda cls, *a, **k: reader))

        result = TASKS[NAME].fn(ctx(dry_run=True))

        assert renames == []
        d = result.detail
        assert (d['rename'], d['already_renamed']) == (1, 1)
        assert {(s['request_id'], s['state'], s.get('xa_user')) for s in d['sample']} == {
            (9301, 'rename', 'pi-user'), (9302, 'already_renamed', None)}

    def test_a_failed_rename_is_a_partial_failure(self, ctx, levers, session, monkeypatch):
        _landed(session, 'NCAR9401', 'UXYZ9401', 9401)
        monkeypatch.setattr(remediation, 'rename_request', lambda f, **k:
                            remediation.RemediationOutcome(7, status='rejected', error='401'))
        monkeypatch.setattr(XrasAdminClient, 'from_environment',
                            classmethod(lambda cls, *a, **k: SimpleNamespace(reader=None)))
        result = TASKS[NAME].fn(ctx())
        assert result.partial_failures == 1 and result.detail['failed'] == 1
