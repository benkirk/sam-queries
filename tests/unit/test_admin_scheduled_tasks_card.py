"""The Scheduled tasks tile on Admin → Configuration.

Two things here matter more than "the tile renders":

1. **The kill-switch warning.** Production ships with
   `SAM_TASKS_DISABLED: "cleanup_status_snapshots"`, so the first thing this
   card shows in production is a dispatcher waking hourly and deliberately
   doing nothing. If the card does not say so loudly it looks healthy.
2. **The degrade.** `task_run` does not exist until Alembic 0006 is applied,
   and it is not applied on staging or production yet — so this card *will*
   render before its table exists.
"""

import itertools
from datetime import timedelta

import pytest

from system_status.models.task_run import TaskRun
from system_status.timeutil import utcnow_naive

CONFIG_URL = '/admin/htmx/configuration'

_KEY_SEQ = itertools.count(1)


def _make_task_run(session, *, task_name='cleanup_status_snapshots',
                   state='succeeded', trigger_type='schedule', age=None,
                   heartbeat_age=None, runner_id='samuel-tasks-0-aaaaa'):
    """One committed ledger row — see the sibling page module on why commit
    is available on this bind."""
    now = utcnow_naive()
    claimed_at = now - (age or timedelta(0))
    row = TaskRun(
        task_name=task_name,
        occurrence_key=(
            f"{claimed_at.strftime('%Y%m%dT%H%M')}{next(_KEY_SEQ):04d}Z"),
        state=state, trigger_type=trigger_type, attempt=1,
        claimed_at=claimed_at,
        heartbeat_at=now - heartbeat_age if heartbeat_age else claimed_at,
        finished_at=None if state == 'running' else claimed_at,
        duration_ms=None if state == 'running' else 1234,
        runner_id=runner_id, detail=None,
    )
    session.add(row)
    session.commit()
    return row


class TestTheTileRenders:

    def test_the_card_is_present(self, auth_client):
        resp = auth_client.get(CONFIG_URL)
        assert resp.status_code == 200
        assert b'Scheduled tasks' in resp.data

    def test_it_links_to_the_run_history(self, auth_client):
        assert b'/admin/htmx/tasks' in auth_client.get(CONFIG_URL).data

    def test_the_details_link_is_NOT_gated_on_system_admin(self, auth_client,
                                                           monkeypatch):
        """Unlike Notifications: the page it targets is VIEW_SYSTEM_CONFIG,
        the same tier as this card, so the link cannot 403."""
        from webapp.utils import rbac
        from webapp.utils.rbac import Permission

        real = rbac.has_permission

        def _no_system_admin(permission, *args, **kwargs):
            if permission is Permission.SYSTEM_ADMIN:
                return False
            return real(permission, *args, **kwargs)

        monkeypatch.setattr(rbac, 'has_permission', _no_system_admin)
        assert b'/admin/htmx/tasks' in auth_client.get(CONFIG_URL).data

    def test_the_registered_task_is_named(self, auth_client):
        """The registry is read for its import side effects; if that import
        stops happening the tile silently reports zero tasks."""
        assert b'cleanup_status_snapshots' in auth_client.get(CONFIG_URL).data


class TestTheKillSwitchWarning:
    """The single most important pixel on this card."""

    def test_a_disabled_task_is_called_out(self, auth_client, monkeypatch):
        monkeypatch.setenv('SAM_TASKS_DISABLED', 'cleanup_status_snapshots')
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        section = html[html.index('Scheduled tasks'):]
        assert 'Disabled' in section[:3000], \
            'a kill-switched dispatcher must not look healthy'
        assert 'alert-warning' in section[:3000]

    def test_no_warning_when_nothing_is_disabled(self, auth_client,
                                                 monkeypatch):
        monkeypatch.delenv('SAM_TASKS_DISABLED', raising=False)
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        section = html[html.index('Scheduled tasks'):]
        assert 'Disabled:' not in section[:3000]

    def test_the_switch_is_read_through_the_dispatchers_own_accessor(
            self, monkeypatch):
        """One mechanism: the card and `run_due` must agree on what is
        disabled, so the card parses nothing of its own."""
        from scheduling.runner import disabled_tasks

        monkeypatch.setenv('SAM_TASKS_DISABLED', ' a , b ,')
        assert disabled_tasks() == {'a', 'b'}


class TestTheStaleAlert:

    def test_a_stuck_run_is_called_out(self, auth_client, status_session):
        _make_task_run(status_session, state='running',
                       age=timedelta(hours=6), heartbeat_age=timedelta(hours=6))
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        section = html[html.index('Scheduled tasks'):]
        assert 'stuck in' in section[:4000]

    def test_no_alert_when_everything_finished(self, auth_client,
                                               status_session):
        _make_task_run(status_session, state='succeeded')
        html = auth_client.get(CONFIG_URL).get_data(as_text=True)
        section = html[html.index('Scheduled tasks'):]
        assert 'stuck in' not in section[:4000]


class TestTheCounts:

    def test_the_state_block_carries_counts_and_no_addresses(self, app):
        """Task rows carry no PII, and this asserts the tile's state block
        cannot start doing so — `runner_id` is a pod name and `detail` is
        excluded from the card entirely."""
        from webapp.extensions import db
        from webapp.utils.config_inspect import gather_runtime_state

        with app.app_context():
            block = gather_runtime_state(app, db)['scheduled_tasks']

        assert 'detail' not in block
        for key, value in block.items():
            if key in ('tasks', 'disabled'):
                continue
            assert '@' not in str(value), \
                f'scheduled_tasks.{key} carries an address: {value!r}'

    def test_every_card_state_key_is_present(self, app):
        from webapp.extensions import db
        from webapp.utils.config_inspect import gather_runtime_state

        with app.app_context():
            block = gather_runtime_state(app, db)['scheduled_tasks']

        for key in ('succeeded', 'partial', 'failed', 'skipped',
                    'stale_running', 'window_hours', 'last_dispatch_age'):
            assert key in block, f'{key} missing — the template reads it'


class TestUnavailableTable:
    """TRAP 2 — `task_run` arrives with Alembic 0006, which staging and
    production have not applied. The tab must degrade, not 500."""

    def test_a_missing_table_degrades_rather_than_500s(self, auth_client,
                                                       monkeypatch):
        import system_status.queries.task_runs as queries

        def _boom(*args, **kwargs):
            raise RuntimeError("Table 'task_run' doesn't exist")

        monkeypatch.setattr(queries, 'summarize_task_runs', _boom)
        resp = auth_client.get(CONFIG_URL)
        assert resp.status_code == 200
        assert b'unavailable' in resp.data.lower()

    def test_the_fallback_carries_every_key_the_template_reads(
            self, app, monkeypatch):
        """The bug the notifications block hit and fixed: a key read outside
        the `unavailable` short-circuit must exist in the fallback too."""
        import system_status.queries.task_runs as queries

        from webapp.extensions import db
        from webapp.utils.config_inspect import gather_runtime_state

        def _boom(*args, **kwargs):
            raise RuntimeError("Table 'task_run' doesn't exist")

        monkeypatch.setattr(queries, 'summarize_task_runs', _boom)
        with app.app_context():
            block = gather_runtime_state(app, db)['scheduled_tasks']

        assert block['unavailable'] is True
        for key in ('tasks', 'disabled', 'succeeded', 'partial', 'failed',
                    'skipped', 'stale_running', 'total', 'by_state',
                    'last_dispatch', 'last_dispatch_age', 'window_hours'):
            assert key in block, f'{key} missing from the degraded fallback'

    def test_the_session_is_usable_after_the_degrade(self, app, monkeypatch):
        """Without the rollback in the handler, any later `db.session` use in
        the same request raises PendingRollbackError instead of its own
        error."""
        import system_status.queries.task_runs as queries

        from webapp.extensions import db
        from webapp.utils.config_inspect import gather_runtime_state

        def _boom(*args, **kwargs):
            db.session.execute(__import__('sqlalchemy').text('SELECT bogus'))

        monkeypatch.setattr(queries, 'summarize_task_runs', _boom)
        with app.app_context():
            gather_runtime_state(app, db)
            # The point: this must raise on its own terms, not
            # PendingRollbackError.
            db.session.execute(__import__('sqlalchemy').text('SELECT 1'))
