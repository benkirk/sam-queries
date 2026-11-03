"""`unix_group_masks`: registration, the lease, the kill switch, and the sweep against a
FakeDirectory (expire, confirm, re-assert, count)."""
import logging
from datetime import datetime, timedelta

import pytest
from _paths import REPO_ROOT
from factories import (make_adhoc_group, make_adhoc_group_tag, make_adhoc_system_account_entry,
                       make_project, make_unix_group_mask, make_user)
from factories.directory import FakeDirectory, make_group_record
from sam.integration.directory import DirectoryUnavailable
from sam.manage.groups import open_mask, sweep_headline, sweep_masks
from scheduling.registry import TASKS, TaskContext
from scheduling.schedules import Hourly

import scheduling.tasks  # noqa: F401  registers every task

NAME = 'unix_group_masks'
OCC = datetime(2026, 11, 3, 16, 40)   # 09:40 Mountain as naive UTC
NOW = datetime(2026, 11, 3, 9, 40)
T0 = NOW - timedelta(days=2)


@pytest.fixture
def ctx(session):
    def build(dry_run=False):
        return TaskContext(now=OCC + timedelta(minutes=3), occurrence=OCC, occurrence_key='t',
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


def _group(session):
    group = make_adhoc_group(session)
    make_adhoc_group_tag(session, group, 'hpc')
    session.expire(group, ['tags', 'system_accounts'])
    return group


def _rows(group):
    return sorted((e.access_branch_name, e.username) for e in group.system_accounts)


class TestRegistration:
    def test_hourly_on_sam(self):
        t = TASKS[NAME]
        assert t.needs == ('sam',) and isinstance(t.schedule, Hourly)
        assert t.schedule.minute == 40

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
        dev runs it. Delete the prod half in the commit that clears the switch."""
        for env, expect in (('values.yaml', True), ('values-dev.yaml', False)):
            values = (REPO_ROOT / 'helm' / env).read_text()
            line, = [ln for ln in values.splitlines() if ln.strip().startswith('SAM_TASKS_DISABLED:')]
            assert (NAME in line) is expect, (env, line)

    def test_the_task_never_names_a_ticket_writer(self):
        text = (REPO_ROOT / 'src' / 'scheduling' / 'tasks' / 'unix_group_masks.py').read_text()
        assert 'provider_from_environment' not in text


class TestSweep:
    def test_an_overdue_mask_expires_and_takes_its_rows_back(self, session):
        group, user = _group(session), make_user(session)
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='add',
                         requested_by='op', now=T0, expires_at=T0 + timedelta(days=1))
        counts = sweep_masks(session, None, now=NOW, by='task:t')
        assert mask.resolution == 'expired' and mask.closed_by == 'task:t' and _rows(group) == []
        assert counts['expired'] == 1 and counts['directory'] is False

    def test_an_overdue_project_mask_expires_too(self, session):
        project, user = make_project(session), make_user(session)
        project.unix_gid = 96_500_000 + (project.project_id % 100_000)
        session.flush()
        mask = make_unix_group_mask(session, unix_gid=project.unix_gid, kind='project',
                                    username=user.username, expires_at=T0 + timedelta(days=1), when=T0)
        sweep_masks(session, None, now=NOW, by='task:t')
        assert mask.resolution == 'expired'

    def test_the_directory_agreeing_confirms_and_keeps_the_rows(self, session):
        group, user = _group(session), make_user(session, upid=True)
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='add',
                         requested_by='op', now=T0)
        fake = FakeDirectory()
        fake.add(make_group_record(gid=group.unix_gid, name=group.group_name,
                                   member_upids=(user.upid,)))
        counts = sweep_masks(session, fake, now=NOW, by='task:t')
        assert mask.resolution == 'confirmed' and _rows(group) == [('hpc', user.username)]
        assert counts['confirmed'] == 1 and fake.calls == [('group', group.unix_gid)]

    def test_a_remove_confirms_once_the_member_is_gone_upstream(self, session):
        group, user = _group(session), make_user(session, upid=True)
        make_adhoc_system_account_entry(session, group, user.username)
        session.expire(group, ['system_accounts'])
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='remove',
                         requested_by='op', now=T0)
        fake = FakeDirectory()
        fake.add(make_group_record(gid=group.unix_gid, name=group.group_name, member_upids=(1,)))
        sweep_masks(session, fake, now=NOW, by='task:t')
        assert mask.resolution == 'confirmed' and _rows(group) == []

    def test_disagreement_leaves_the_mask_open_and_re_asserts_a_drifted_row(self, session):
        group, user = _group(session), make_user(session, upid=True)
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='add',
                         requested_by='op', now=T0)
        for row in list(group.system_accounts):            # something drifted the row away
            group.system_accounts.remove(row)
        session.flush()
        fake = FakeDirectory()
        fake.add(make_group_record(gid=group.unix_gid, name=group.group_name, member_upids=(1,)))
        counts = sweep_masks(session, fake, now=NOW, by='task:t')
        assert mask.is_active and counts['reasserted'] == 1 and counts['confirmed'] == 0
        assert _rows(group) == [('hpc', user.username)]

    def test_a_login_without_a_upid_is_counted_not_read(self, session):
        group, user = _group(session), make_user(session)
        user.upid = None
        session.flush()
        open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        fake = FakeDirectory()
        counts = sweep_masks(session, fake, now=NOW, by='task:t')
        assert counts['unconfirmable'] == 1 and fake.calls == []

    def test_an_unreadable_directory_skips_confirmation_and_reports(self, session):
        group, user = _group(session), make_user(session, upid=True)
        mask = open_mask(session, gid=group.unix_gid, username=user.username, op='add',
                         requested_by='op', now=T0)
        fake = FakeDirectory()
        fake.raise_with = DirectoryUnavailable('down')
        counts = sweep_masks(session, fake, now=NOW, by='task:t')
        assert mask.is_active and counts['unreadable'] == 1
        assert 'directory unreadable' in sweep_headline(counts)

    def test_the_task_runs_the_sweep(self, ctx, configured, session):
        group, user = _group(session), make_user(session, upid=True)
        open_mask(session, gid=group.unix_gid, username=user.username, op='add', requested_by='op', now=T0)
        configured.add(make_group_record(gid=group.unix_gid, name=group.group_name,
                                         member_upids=(user.upid,)))
        result = TASKS[NAME].fn(ctx())
        assert result.detail['confirmed'] >= 1 and result.detail['as_of'] == NOW.isoformat()
        assert result.message.startswith(f"{result.detail['open']} open")
        assert result.state == 'succeeded'
