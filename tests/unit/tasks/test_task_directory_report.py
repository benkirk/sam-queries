"""`directory_report`: registration, the lease, the kill switch, and a FakeDirectory run."""
import logging
from datetime import datetime, timedelta

import pytest
from _paths import REPO_ROOT
from factories import make_project, make_user
from factories.directory import FakeDirectory, make_group_record
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import Daily

import scheduling.tasks  # noqa: F401  registers every task

NAME = 'directory_report'
OCC = datetime(2026, 10, 17, 9, 0)   # 03:00 Mountain as naive UTC


@pytest.fixture
def ctx(session):
    def build(dry_run=False):
        return TaskContext(now=OCC + timedelta(minutes=7), occurrence=OCC, occurrence_key='t',
                           task_name=NAME, logger=logging.getLogger('test'),
                           _sam_session=session, dry_run=dry_run)
    return build


@pytest.fixture
def configured(monkeypatch):
    import sam.integration.directory.registry as registry
    monkeypatch.setenv('DIRECTORY_PROVIDER', 'fake')
    monkeypatch.setenv('DIRECTORY_URL', 'ldap://ldap.example.invalid')
    fake = FakeDirectory()
    monkeypatch.setattr(registry, 'reader_from_environment', lambda: fake)
    return fake


class TestRegistration:
    def test_nightly_on_sam(self):
        t = TASKS[NAME]
        assert t.needs == ('sam',) and isinstance(t.schedule, Daily)
        assert (t.schedule.hour, t.schedule.minute, t.schedule.tz) == (3, 0, 'America/Denver')

    def test_the_lease_outlives_the_cronjob_deadline(self):
        import re
        from scheduling.ledger import lease_for
        values = (REPO_ROOT / 'helm' / 'values.yaml').read_text()
        match = re.search(r'^\s*activeDeadlineSeconds:\s*(\d+)', values, re.MULTILINE)
        assert match
        assert lease_for(TASKS[NAME].expected_runtime).total_seconds() > int(match.group(1))

    def test_it_ships_switched_off_in_prod_and_on_in_dev(self):
        """WARNING: `SAM_TASKS_DISABLED` is fail-OPEN: a registered task dispatches on the next
        wake unless the chart names it. Prod names it until its first weeks on dev are read;
        dev runs it (read-only). Delete the prod half in the commit that clears the switch."""
        for env, expect in (('values.yaml', True), ('values-dev.yaml', False)):
            values = (REPO_ROOT / 'helm' / env).read_text()
            line, = [ln for ln in values.splitlines() if ln.strip().startswith('SAM_TASKS_DISABLED:')]
            assert (NAME in line) is expect, (env, line)


class TestRun:
    def test_unconfigured_succeeds_with_nothing_to_compare(self, ctx):
        result = TASKS[NAME].fn(ctx())
        assert result.detail == {'configured': False} and result.message == 'directory not configured'

    def test_a_run_records_the_counts(self, ctx, configured, session):
        lead = make_user(session, upid=True)
        project = make_project(session, lead=lead)
        project.unix_gid = 77_790_001
        session.flush()
        configured.add(make_group_record(gid=project.unix_gid, name=project.projcode,
                                         tags=('exclude-from-google',), member_upids=(lead.upid,),
                                         managers=(lead.upid, 999_999_996)))
        result = TASKS[NAME].fn(ctx(dry_run=True))
        groups = result.detail['kinds']['group']
        assert groups['directory'] == 1 and groups['counts']['manager_missing_in_sam'] == 1
        assert groups['ids']['manager_missing_in_sam'] == [project.projcode]
        assert result.detail['dry_run'] is True and set(result.detail['kinds']) == {
            'person', 'organization', 'institution', 'group'}
        assert result.message.startswith('person:') and 'group: 1 read' in result.message
        assert [c for c in configured.calls] == [('records', k) for k in ('person', 'organization', 'institution', 'group')]
