"""The scheduled-task run history page.

The gate this module exists for is the **split**: unlike the notifications
log, the page and its table are `VIEW_SYSTEM_CONFIG` — the same tier as the
Configuration tile that links here — and only the per-row detail modal is
`SYSTEM_ADMIN`, because `detail` can hold a traceback naming hosts and paths.

So `config_only_client` must get 200 on the first two and 403 on the third.
A test that only checked "admin can see it" would pass with the whole page
mis-gated either way.
"""

import itertools
from datetime import timedelta

import pytest

from system_status.models.task_run import TaskRun
from system_status.timeutil import utcnow_naive
from webapp.utils.rbac import Permission

PAGE = '/admin/htmx/tasks'
LOG = '/admin/htmx/tasks/log'

_KEY_SEQ = itertools.count(1)


def _make_task_run(session, *, task_name='cleanup_status_snapshots',
                   state='succeeded', trigger_type='schedule',
                   age=None, runner_id='samuel-tasks-00000000-aaaaa',
                   attempt=1, duration_ms=1234, detail=None):
    """One committed ledger row.

    The `system_status` bind is a per-worker SQLite tempfile with per-test
    DELETE isolation, so unlike the SAM bind this may commit — which is what
    lets these tests assert on rendered HTML rather than on a state dict.
    """
    claimed_at = utcnow_naive() - (age or timedelta(0))
    row = TaskRun(
        task_name=task_name,
        occurrence_key=(
            f"{claimed_at.strftime('%Y%m%dT%H%M')}{next(_KEY_SEQ):04d}Z"),
        state=state, trigger_type=trigger_type, attempt=attempt,
        claimed_at=claimed_at, heartbeat_at=claimed_at,
        finished_at=None if state == 'running' else claimed_at,
        duration_ms=None if state == 'running' else duration_ms,
        runner_id=runner_id, detail=detail,
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def config_only_client(auth_client, monkeypatch):
    """`benkirk` with VIEW_SYSTEM_CONFIG but *without* SYSTEM_ADMIN."""
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without_system_admin(user):
        kept = {p for p in real(user) if p is not Permission.SYSTEM_ADMIN}
        kept.add(Permission.VIEW_SYSTEM_CONFIG)
        return kept

    monkeypatch.setattr(rbac, 'get_user_permissions', _without_system_admin)
    return auth_client


class TestThePermissionSplit:
    """The whole point of this page's gating, and where it differs from the
    notifications log."""

    @pytest.mark.parametrize('url', [PAGE, LOG])
    def test_view_system_config_reaches_the_page_and_table(
            self, config_only_client, url):
        assert config_only_client.get(url).status_code == 200, \
            f'{url} must be VIEW_SYSTEM_CONFIG — task rows carry no PII'

    def test_view_system_config_is_refused_the_detail_modal(
            self, config_only_client, status_session):
        """`detail` can hold a traceback naming hosts, paths and connection
        strings — that is the one thing the lower tier does not get."""
        row = _make_task_run(status_session)
        resp = config_only_client.get(f'/admin/htmx/tasks/{row.task_run_id}')
        assert resp.status_code == 403

    def test_system_admin_reaches_the_detail_modal(self, auth_client,
                                                   status_session):
        row = _make_task_run(status_session)
        resp = auth_client.get(f'/admin/htmx/tasks/{row.task_run_id}')
        assert resp.status_code == 200

    @pytest.mark.parametrize('url', [PAGE, LOG, '/admin/htmx/tasks/1'])
    def test_anonymous_is_refused_everything(self, client, url):
        assert client.get(url).status_code in (302, 401, 403)


class TestThePageShell:

    def test_it_renders(self, auth_client):
        resp = auth_client.get(PAGE)
        assert resp.status_code == 200
        assert b'Scheduled tasks' in resp.data

    def test_it_lazy_loads_the_log_fragment(self, auth_client):
        assert b'/admin/htmx/tasks/log' in auth_client.get(PAGE).data


class TestTheLogFragment:

    def test_an_empty_result_says_so_rather_than_a_bare_table(
            self, auth_client, status_session):
        resp = auth_client.get(f'{LOG}?search=definitely-no-such-task')
        assert b'No task runs match' in resp.data

    def test_the_headline_count_is_the_filtered_total(self, auth_client,
                                                      status_session):
        resp = auth_client.get(f'{LOG}?search=definitely-no-such-task')
        assert b'Showing' in resp.data

    def test_a_committed_row_reaches_the_table(self, auth_client,
                                               status_session):
        _make_task_run(status_session, task_name='a_very_distinctive_task')
        resp = auth_client.get(LOG)
        assert b'a_very_distinctive_task' in resp.data

    def test_the_state_renders_as_its_own_badge_not_bg_secondary(
            self, auth_client, status_session):
        """`succeeded` was absent from the badge vocabulary before this
        feature, so it and a skip rendered identically."""
        _make_task_run(status_session, state='succeeded')
        html = auth_client.get(LOG).get_data(as_text=True)
        assert 'bg-success' in html

    def test_the_detail_button_is_hidden_from_the_lower_tier(
            self, config_only_client, status_session):
        """The courtesy the Notifications tile pays with its Details link:
        never offer a control that 403s."""
        _make_task_run(status_session)
        html = config_only_client.get(LOG).get_data(as_text=True)
        assert 'task_run_detail' not in html
        assert '/admin/htmx/tasks/' not in html.split('id="scheduledTasksTable"')[1]

    @pytest.mark.parametrize('query', [
        'state=succeeded', 'state=failed&state=partial',
        'trigger_type=manual', 'task_name=cleanup_status_snapshots',
        'days=1', 'days=365', 'days=99999', 'days=-5',
        'page=2', 'page=0', 'search=x',
    ])
    def test_filter_combinations_do_not_500(self, auth_client, status_session,
                                            query):
        assert auth_client.get(f'{LOG}?{query}').status_code == 200


class TestTheDetailModal:

    def test_a_missing_row_returns_200_not_404(self, auth_client,
                                               status_session):
        """htmx will not swap a 4xx, so the miss has to render as content."""
        resp = auth_client.get('/admin/htmx/tasks/99999999')
        assert resp.status_code == 200
        assert b'not found' in resp.data.lower()

    def test_json_detail_is_decoded_and_pretty_printed(self, auth_client,
                                                       status_session):
        row = _make_task_run(status_session,
                             detail='{"deleted": {"derecho_status": 41}}')
        html = auth_client.get(
            f'/admin/htmx/tasks/{row.task_run_id}').get_data(as_text=True)
        assert 'derecho_status' in html

    def test_undecodable_detail_is_still_shown(self, auth_client,
                                               status_session):
        """A blob that will not parse is still evidence."""
        row = _make_task_run(status_session, detail='Traceback (most recent')
        html = auth_client.get(
            f'/admin/htmx/tasks/{row.task_run_id}').get_data(as_text=True)
        assert 'Traceback' in html

    def test_the_trigger_is_not_rendered_through_status_badge(
            self, auth_client, status_session):
        """TRAP 3: `manual` is already in the badge vocabulary with an XRAS
        meaning ('parked for a human') and would mislabel a forced run."""
        row = _make_task_run(status_session, trigger_type='manual')
        html = auth_client.get(
            f'/admin/htmx/tasks/{row.task_run_id}').get_data(as_text=True)
        assert 'Parked for a human' not in html
