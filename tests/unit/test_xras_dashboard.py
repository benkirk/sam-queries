"""HTTP-tier tests for the Allocations > XRAS page.

Scope follows the house convention: the HTTP tier covers **auth, 404 and render
smoke**. Route handlers use Flask-SQLAlchemy's ``db.session`` on its own
connection and only see committed snapshot rows, so happy-path writes are
covered a layer down — replay's behavior lives in
``tests/api/test_xras_access.py::TestReplay``.

What is genuinely worth pinning here is the **two-permission split**, because it
is the one thing a template change can silently break:

    VIEW_XRAS    the page, the table, the error lists
    MANAGE_XRAS  the raw payload (real PII) and the replay button

The payload gate is enforced twice — the route only asks the query layer for
``raw_payload`` when the viewer holds MANAGE_XRAS, so a VIEW-only response never
contains the bytes at all. A template-only gate would leave the PII sitting in
the HTML for anyone who opened view-source.
"""

import re
from pathlib import Path

import pytest

from webapp.utils.rbac import Permission


@pytest.fixture
def committed_action(app):
    """One real ``xras_action_log`` row, committed, with explicit cleanup.

    A factory-built row would be invisible here: route handlers read through
    Flask-SQLAlchemy's ``db.session`` on its own connection and only ever see
    committed rows, which is why the suite's per-test SAVEPOINT cannot help.

    Written through ``actions._record`` — the same helper the route and replay use
    — so there is exactly one insert path into this table. Deleted by primary key
    on the way out: a range predicate would take an open-ended gap lock and
    deadlock against other xdist workers inserting concurrently (see the
    ``action_log`` fixture in tests/api/test_xras_access.py).
    """
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from sam.integration.xras import XrasActionLog
    from webapp.api.xras import actions
    from webapp.extensions import db

    payload = '{"actionType":"Extension","requestNumber":"UCUB0166"}'
    with app.app_context():
        log_id = actions._record(
            status='received', raw_payload=payload, http_status=200,
            action_type='Extension', request_number='UCUB0166',
            remote_actor='samuel',
        )

    yield log_id

    with app.app_context(), Session(db.engine) as session:
        session.execute(delete(XrasActionLog).where(
            XrasActionLog.xras_action_log_id == log_id))
        session.commit()


@pytest.fixture
def committed_odd_status_action(app):
    """A committed row whose ``status`` is outside the five-value vocabulary.

    Not reachable through the writers — they only ever pass the five constants —
    which is the point: this is the "a bad write happened" case the query layer
    deliberately preserves rather than hides, and the only way to exercise the
    facet strip's handling of it. Same insert path and same PK-targeted cleanup as
    :func:`committed_action`.
    """
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from sam.integration.xras import XrasActionLog
    from webapp.api.xras import actions
    from webapp.extensions import db

    with app.app_context():
        log_id = actions._record(
            status='pending', raw_payload='{"actionType":"Extension"}',
            http_status=200, action_type='Extension', remote_actor='samuel',
        )

    yield log_id

    with app.app_context(), Session(db.engine) as session:
        session.execute(delete(XrasActionLog).where(
            XrasActionLog.xras_action_log_id == log_id))
        session.commit()


@pytest.fixture
def view_only_client(auth_client, monkeypatch):
    """`benkirk` minus MANAGE_XRAS.

    `benkirk` is `[p for p in Permission]`, so there is no snapshot user who
    holds VIEW_XRAS but not MANAGE_XRAS — the split has to be simulated. Patching
    `get_user_permissions` is the narrowest way to do it: every gate in the
    request path (`require_permission`, the `has_permission` template global, the
    route's own check) reads through it.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without_manage(user):
        return {p for p in real(user) if p is not Permission.MANAGE_XRAS}

    monkeypatch.setattr(rbac, 'get_user_permissions', _without_manage)
    return auth_client


class TestXrasPageAccess:
    def test_page_renders_for_a_permitted_user(self, auth_client):
        resp = auth_client.get('/allocations/xras')
        assert resp.status_code == 200
        assert b'XRAS' in resp.data

    def test_page_is_denied_without_the_permission(self, non_admin_client):
        resp = non_admin_client.get('/allocations/xras')
        assert resp.status_code == 403

    def test_page_requires_login(self, client):
        resp = client.get('/allocations/xras')
        assert resp.status_code in (302, 401)

    @pytest.mark.parametrize('path', [
        '/allocations/xras_fragment',
        '/allocations/xras_pending_fragment',
        '/allocations/xras_action_details/1',
    ])
    def test_every_read_fragment_is_gated(self, non_admin_client, path):
        assert non_admin_client.get(path).status_code == 403

    def test_recheck_is_gated_on_manage_not_view(self, view_only_client):
        """The write needs MANAGE_XRAS even though the page needs only VIEW."""
        resp = view_only_client.post('/allocations/xras_recheck/1')
        assert resp.status_code == 403

    def test_view_only_user_still_gets_the_page(self, view_only_client):
        assert view_only_client.get('/allocations/xras').status_code == 200


class TestXrasFragments:
    def test_table_fragment_renders(self, auth_client):
        resp = auth_client.get('/allocations/xras_fragment')
        assert resp.status_code == 200
        # The summary strip enumerates every status, including at zero — an
        # absent bucket would read as "not measured" rather than "none".
        for state in (b'Received', b'Processed', b'Manual', b'Failed', b'Would succeed'):
            assert state in resp.data

    def test_table_fragment_survives_a_junk_sort_key(self, auth_client):
        """`sort_by` is whitelisted in the route; an unknown key falls back to
        the default rather than reaching `order_by`."""
        resp = auth_client.get('/allocations/xras_fragment?sort_by=raw_payload')
        assert resp.status_code == 200

    def test_table_fragment_survives_junk_pagination(self, auth_client):
        resp = auth_client.get(
            '/allocations/xras_fragment?page=abc&per_page=99999')
        assert resp.status_code == 200

    def test_pending_fragment_renders(self, auth_client):
        resp = auth_client.get('/allocations/xras_pending_fragment')
        assert resp.status_code == 200

    def test_pending_empty_state_does_not_claim_nothing_is_pending(
            self, auth_client):
        """The card can only see actions this log knows about, so an empty
        queue must not be presented as "all clear".

        WARNING: Both literals are copy assertions. If you reword the empty state,
        reword them — do not delete the second one, which is the honest half."""
        resp = auth_client.get('/allocations/xras_pending_fragment')
        assert b'Nothing needs attention' in resp.data
        assert b'does not mean nothing is pending' in resp.data

    def test_everything_empty_state_names_the_window(self, auth_client):
        """Under the toggle the empty state blames the date filter, and keeps
        the honest half."""
        resp = auth_client.get('/allocations/xras_pending_fragment',
                               query_string={'show_all': '1'})
        assert b'No XRAS activity in this window' in resp.data
        assert b'does not mean nothing is pending' in resp.data

    def test_missing_action_detail_is_a_message_not_a_404_page(self, auth_client):
        """It lands in a modal body, where a 404 error page would be worse than
        useless."""
        resp = auth_client.get('/allocations/xras_action_details/999999999')
        assert resp.status_code == 200
        assert b'Action not found' in resp.data


class TestPayloadGating:
    """The PII gate. `raw_payload` is the request body verbatim and carries
    participant names, emails, phone numbers and grant-officer contacts."""

    def test_manage_user_sees_the_payload_panel(self, auth_client,
                                                committed_action):
        resp = auth_client.get(
            f'/allocations/xras_action_details/{committed_action}')
        assert resp.status_code == 200
        assert b'Raw payload' in resp.data
        assert b'actionType' in resp.data, 'the payload bytes should be present'

    def test_view_only_user_gets_the_locked_notice_and_no_bytes(
            self, view_only_client, committed_action):
        resp = view_only_client.get(
            f'/allocations/xras_action_details/{committed_action}')
        assert resp.status_code == 200
        assert b'Requires the XRAS management permission' in resp.data
        # The bytes are absent from the RESPONSE, not merely undrawn: the route
        # never asked the query layer for them, so view-source shows nothing.
        assert b'actionType' not in resp.data
        assert b'UCUB0166' in resp.data, 'non-PII metadata still renders'


class TestNavigation:
    def test_tab_appears_on_the_sibling_allocations_pages(self, auth_client):
        resp = auth_client.get('/allocations/transactions')
        assert resp.status_code == 200
        assert b'/allocations/xras' in resp.data

    def test_tab_is_hidden_from_users_without_view_xras(self, auth_client,
                                                       monkeypatch):
        from webapp.utils import rbac

        real = rbac.get_user_permissions
        monkeypatch.setattr(
            rbac, 'get_user_permissions',
            lambda user: {p for p in real(user) if p is not Permission.VIEW_XRAS})

        resp = auth_client.get('/allocations/transactions')
        assert resp.status_code == 200
        assert b'/allocations/xras' not in resp.data


class TestFacetChips:
    """The summary strip's chips are filter controls, not decoration.

    They looked clickable before they were — the affordance mismatch is what
    prompted the feature — so these pin the wiring the click depends on.
    """

    def _chips(self, html, field):
        """Every chip button for one dimension, as (value, data-value) pairs."""
        pattern = (r'data-action="set-filter-submit"[^>]*?'
                   r'data-form-id="([^"]*)"[^>]*?'
                   r'data-field="([^"]*)"[^>]*?'
                   r'data-value="([^"]*)"')
        return [(m.group(1), m.group(3))
                for m in re.finditer(pattern, html, re.S)
                if m.group(2) == field]

    def test_status_chips_are_wired_to_the_filter_form(self, auth_client):
        from sam.queries.xras_actions import XRAS_ACTION_STATUSES

        html = auth_client.get('/allocations/xras_fragment').data.decode()
        chips = self._chips(html, 'status')
        # A superset is legal — an out-of-vocabulary status gets its own chip
        # (see the test below).
        #
        # WARNING: This asserted the count EXACTLY, on the reasoning that nothing in
        # the snapshot produces an extra chip. Under xdist that is false: the
        # very next test's `committed_odd_status_action` fixture COMMITS its
        # row (deliberately — the route reads db.session's own connection, so
        # a SAVEPOINT-scoped row would be invisible to it), and a committed row
        # is visible to every other worker. This test then saw a seventh chip
        # and failed, on about half of all runs, with nothing to do with the
        # code under test. Assert the vocabulary is present instead of
        # asserting nobody else exists.
        #
        # Read from the tuple rather than spelled out: this is a check that the
        # strip renders the vocabulary, not a second copy of it. Spelling it out is
        # what made adding `unmapped` break two tests that had no opinion about it.
        assert set(XRAS_ACTION_STATUSES) <= {value for _, value in chips}
        # Every chip writes into the form the filter panel actually renders.
        assert {form_id for form_id, _ in chips} == {'xras-filters'}

    def test_an_unknown_status_gets_its_own_chip(self, auth_client,
                                                 committed_odd_status_action):
        """A status outside the vocabulary must be visible, and filterable.

        ``summarize_xras_actions`` preserves it on purpose — *"A status outside the
        vocabulary would be a bug, not a filter miss — surface it rather than
        dropping it on the floor"* — and this strip used to re-derive its rows from
        ``XRAS_ACTION_STATUSES``, undoing that one layer up. The headline total
        counted the row regardless, so the strip disagreed with the number above it.

        Unlike a NULL ``action_type``, an unknown status is a real string the
        multi-select can express, so a chip for it works rather than being a control
        that cannot fire.
        """
        html = auth_client.get('/allocations/xras_fragment').data.decode()
        values = [value for _, value in self._chips(html, 'status')]
        assert 'pending' in values

    def test_the_known_statuses_keep_their_order_ahead_of_strays(
            self, auth_client, committed_odd_status_action):
        """The vocabulary is a stable, learnable strip in its declared order; a stray
        appends rather than reshuffling what an operator scans for."""
        from sam.queries.xras_actions import XRAS_ACTION_STATUSES

        html = auth_client.get('/allocations/xras_fragment').data.decode()
        values = [value for _, value in self._chips(html, 'status')]
        known = len(XRAS_ACTION_STATUSES)
        assert values[:known] == list(XRAS_ACTION_STATUSES)
        assert values[known:] == ['pending']

    def test_action_type_chips_are_wired(self, auth_client, committed_action):
        html = auth_client.get('/allocations/xras_fragment').data.decode()
        chips = self._chips(html, 'action_type')
        assert ('xras-filters', 'Extension') in chips

    def test_the_active_chip_clears_rather_than_reapplying(self, auth_client):
        """An empty data-value is how set-filter-submit clears a filter, so the
        selected chip doubles as its own clear button."""
        html = auth_client.get(
            '/allocations/xras_fragment?status=failed').data.decode()
        chips = dict((v, f) for f, v in self._chips(html, 'status'))
        # 'failed' is selected, so its chip carries the CLEAR value...
        assert '' in [v for _, v in self._chips(html, 'status')]
        # ...and is marked active.
        assert 'is-active' in html

    def test_status_chips_keep_their_colour_coding(self, auth_client):
        """has-badge is what tells the CSS to mark selection with a ring rather
        than a fill that would paint over the status palette."""
        html = auth_client.get('/allocations/xras_fragment').data.decode()
        assert 'facet-chip has-badge' in html

    def test_chips_are_buttons_not_links(self, auth_client):
        """They mutate a form; a bare href would be a lie to assistive tech and
        would navigate on middle-click."""
        html = auth_client.get('/allocations/xras_fragment').data.decode()
        for m in re.finditer(r'<(\w+)[^>]*data-action="set-filter-submit"', html):
            assert m.group(1) == 'button'


class TestFacetSelfExclusion:
    """A dimension's chips must ignore its own filter, or they collapse to zeros
    the moment one is picked and stop being a way to switch between values."""

    def _counts(self, html, field):
        """{value: count} scraped from one dimension's chips."""
        out = {}
        for m in re.finditer(
                r'data-field="' + field + r'"[^>]*?data-value="([^"]*)"'
                r'.*?facet-chip-count">([\d,]+)<', html, re.S):
            out[m.group(1)] = int(m.group(2).replace(',', ''))
        return out

    def test_other_statuses_stay_countable_while_one_is_selected(
            self, auth_client, committed_action):
        """The regression that would silently turn the chips back into dead
        ends. committed_action is a 'received' row, so filtering to a DIFFERENT
        status must still show it."""
        html = auth_client.get(
            '/allocations/xras_fragment?status=failed').data.decode()
        counts = self._counts(html, 'status')
        # The active chip's value is '' (its clear affordance), so 'received'
        # here is an unselected chip — and it must not read zero.
        assert counts.get('received', 0) >= 1, (
            'status facet was scoped by its own filter — the chips are dead ends')

    def test_the_headline_count_is_the_table_total_not_a_facet_total(
            self, auth_client, committed_action):
        """With each facet unscoped in one dimension, neither facet total is the
        number of rows on screen."""
        html = auth_client.get(
            '/allocations/xras_fragment?status=failed').data.decode()
        shown = int(re.search(r'Showing\s*</span>\s*<strong>([\d,]+)</strong>',
                              html).group(1).replace(',', ''))
        counts = self._counts(html, 'status')
        # The 'received' row exists but is filtered out of the table.
        assert counts.get('received', 0) >= 1
        assert shown < sum(counts.values())


class TestDefaultWindowUpperBound:
    """The default window is unbounded above, and that is load-bearing.

    `received_time` is a MySQL DATETIME with second resolution, and MySQL
    **rounds** fractional seconds rather than truncating. A row written at
    10:10:24.894 is therefore stored as 10:10:25 — *after* an `end_date` captured
    microseconds earlier in the same request. With a `datetime.now()` upper
    bound, an action that arrived moments ago is missing from the log until the
    clock ticks past it, which is the exact question this page exists to answer.
    """

    def test_default_window_has_no_upper_bound(self):
        from werkzeug.datastructures import MultiDict

        from webapp.dashboards.allocations.xras._shared import _parse_xras_filters

        filters, _, _ = _parse_xras_filters(MultiDict())
        assert filters['start_date'] is not None, 'still a 30-day lower bound'
        assert filters['end_date'] is None

    def test_a_row_written_this_instant_is_visible(self, auth_client,
                                                   committed_action):
        """The regression, end to end: the fixture commits a row and the very
        next request must show it. This failed before the fix roughly whenever
        the write landed on a sub-second >= .5."""
        html = auth_client.get('/allocations/xras_fragment').data.decode()
        assert 'UCUB0166' in html

    def test_an_explicit_end_date_still_bounds(self):
        """Unbounded is the DEFAULT, not the behavior — an explicit To date is
        still honored, normalized to the end of that day."""
        from werkzeug.datastructures import MultiDict

        from webapp.dashboards.allocations.xras._shared import _parse_xras_filters

        filters, _, _ = _parse_xras_filters(
            MultiDict([('end_date', '2026-01-15')]))
        assert filters['end_date'].strftime('%Y-%m-%d %H:%M:%S') \
            == '2026-01-15 23:59:59'


class TestScopeSeam:
    """`scope_rows` is the one "queue vs. everything" switch both worklist
    cards go through, so the two cannot disagree on what `show_all` means."""

    ROWS = [{'id': 1, 'queued': True}, {'id': 2, 'queued': False}]

    def test_default_applies_the_queue_and_never_the_window(self):
        from werkzeug.datastructures import MultiDict

        from webapp.dashboards.allocations.xras._shared import scope_rows

        def boom(row, window):
            raise AssertionError('the window must not be consulted by default')

        rows = scope_rows(self.ROWS, MultiDict(),
                          queue=lambda r: r['queued'], in_window=boom)
        assert [r['id'] for r in rows] == [1]

    def test_show_all_applies_the_parsed_window_to_every_row(self):
        from werkzeug.datastructures import MultiDict

        from webapp.dashboards.allocations.xras._shared import scope_rows

        seen = []

        def in_window(row, window):
            seen.append(window['days'])
            return True

        rows = scope_rows(self.ROWS, MultiDict([('show_all', '1'), ('days', '7')]),
                          queue=lambda r: False, in_window=in_window)
        assert [r['id'] for r in rows] == [1, 2]
        assert seen == [7, 7], 'the window dict is parsed once and passed through'

    def test_activity_in_window_matches_the_sql_bounds(self):
        """Inclusive at both ends, an absent bound is open, a dateless row is
        kept — so `show_all` renders exactly what the SQL window would."""
        from datetime import datetime

        from webapp.dashboards.allocations.xras._shared import _activity_in_window

        since, until = datetime(2026, 8, 1), datetime(2026, 8, 31, 23, 59, 59)
        window = {'since': since, 'until': until}
        assert _activity_in_window({'received_time': since}, window)
        assert _activity_in_window({'received_time': until}, window)
        assert not _activity_in_window({'received_time': datetime(2026, 7, 31)}, window)
        assert not _activity_in_window({'received_time': datetime(2026, 9, 1)}, window)
        assert _activity_in_window({'received_time': datetime(2020, 1, 1)},
                                   {'since': None, 'until': None})
        assert _activity_in_window({'received_time': None}, window)


class TestAttentionQueueToggle:
    """The Activations card's `show_all` switch, on an empty DB — what the
    HTTP tier can pin: it renders, it binds, it explains, it does not lie."""

    FRAGMENT = '/allocations/xras_pending_fragment'
    SWITCH = 'id="xras-activity-show-all"'

    def _switch(self, body):
        return body.split(self.SWITCH)[1].split('>')[0]

    def test_the_switch_renders_on_an_empty_queue_unchecked(self, auth_client):
        body = auth_client.get(self.FRAGMENT).get_data(as_text=True)
        assert self.SWITCH in body
        assert 'checked' not in self._switch(body)
        assert 'Everything in the window (0)' in body

    def test_the_switch_belongs_to_the_filter_form(self, auth_client):
        body = auth_client.get(self.FRAGMENT).get_data(as_text=True)
        switch = self._switch(body)
        assert 'name="show_all"' in switch
        assert 'form="xras-activity-filters"' in switch
        assert 'hx-include="#xras-window-filters, #xras-activity-filters"' in switch

    def test_show_all_checks_the_switch(self, auth_client):
        body = auth_client.get(self.FRAGMENT,
                               query_string={'show_all': '1'}).get_data(as_text=True)
        assert 'checked' in self._switch(body)

    def test_the_switch_explains_both_sets_in_a_popover(self, auth_client):
        body = auth_client.get(self.FRAGMENT).get_data(as_text=True)
        assert 'data-bs-toggle="popover"' in body
        assert 'needs attention' in body and 'everything in the window' in body

    def test_an_empty_queue_shows_no_hidden_count_badge(self, auth_client):
        body = auth_client.get(self.FRAGMENT).get_data(as_text=True)
        assert 'more with Everything in the window' not in body
        assert '0 need attention' in body


# ===========================================================================
# The activation worklist — Notify / Activate / Dismiss / Restore / Comment
# ===========================================================================

#: Every write route on the pending card, plus the two modal-body GETs. All
#: require MANAGE_XRAS — including the history GET, because its timeline
#: surfaces ``notified_to`` (project lead/admin contact detail), which is the
#: same category of data the raw-payload gate exists for.
_ACTIVATION_WRITE_PATHS = [
    ('POST', '/allocations/xras_notify/1'),
    ('POST', '/allocations/xras_activate/1'),
    ('POST', '/allocations/xras_dismiss/1'),
    ('POST', '/allocations/xras_restore/1'),
    ('POST', '/allocations/xras_comment/1'),
    ('GET', '/allocations/xras_dismiss_form/1'),
    ('GET', '/allocations/xras_notify_form/1'),
    ('GET', '/allocations/xras_history/1'),
]


@pytest.fixture
def no_governance_client(auth_client, monkeypatch):
    """`benkirk` with MANAGE_XRAS but *without* EDIT_PROJECTS.

    The exact shape Activate must refuse: holding the XRAS management permission
    is not enough to flip `project.active`, which is a GOVERNANCE_FIELD.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without_edit_projects(user):
        return {p for p in real(user) if p is not Permission.EDIT_PROJECTS}

    monkeypatch.setattr(rbac, 'get_user_permissions', _without_edit_projects)
    return auth_client


class TestActivationRouteGating:

    @pytest.mark.parametrize('method,path', _ACTIVATION_WRITE_PATHS)
    def test_denied_without_any_xras_permission(self, non_admin_client,
                                                method, path):
        resp = getattr(non_admin_client, method.lower())(path)
        assert resp.status_code == 403

    @pytest.mark.parametrize('method,path', _ACTIVATION_WRITE_PATHS)
    def test_denied_with_view_but_not_manage(self, view_only_client,
                                             method, path):
        """VIEW_XRAS buys the card. Every action on it, and the history that
        carries contact details, needs MANAGE_XRAS."""
        resp = getattr(view_only_client, method.lower())(path)
        assert resp.status_code == 403

    @pytest.mark.parametrize('method,path', _ACTIVATION_WRITE_PATHS)
    def test_requires_login(self, client, method, path):
        resp = getattr(client, method.lower())(path)
        assert resp.status_code in (302, 401)


class TestActivateGovernanceGate:
    """`active` is a GOVERNANCE_FIELD: MANAGE_XRAS alone must not flip it.

    `can_edit_project_governance` is flat EDIT_PROJECTS with **no** steward
    override, so a project lead cannot activate their own project either. That is
    why this is an in-body check rather than `require_project_permission`, which
    means "X OR project lead/admin" and would be strictly too permissive.
    """

    def test_manage_xras_without_edit_projects_is_403(self, no_governance_client,
                                                      active_project):
        resp = no_governance_client.post(
            f'/allocations/xras_activate/{active_project.project_id}')
        assert resp.status_code == 403

    def test_the_403_outranks_the_already_active_shortcut(
            self, no_governance_client, active_project):
        """A permission failure must not be masked by the idempotency
        early-return — `active_project` is active, so an ordering slip would
        answer 200 'already active' to someone who may not activate anything."""
        resp = no_governance_client.post(
            f'/allocations/xras_activate/{active_project.project_id}')
        assert resp.status_code == 403
        assert b'already active' not in resp.data


class TestActivationMissingProject:
    """A project id that does not resolve. These land in a modal body or a toast,
    so the answer must be a message rather than a 404 error page."""

    @pytest.mark.parametrize('path', [
        '/allocations/xras_notify/999999999',
        '/allocations/xras_activate/999999999',
        '/allocations/xras_dismiss/999999999',
        '/allocations/xras_restore/999999999',
        '/allocations/xras_comment/999999999',
    ])
    def test_write_routes_report_not_found(self, auth_client, path):
        resp = auth_client.post(path)
        assert resp.status_code == 404
        assert b'not found' in resp.data.lower()

    @pytest.mark.parametrize('path', [
        '/allocations/xras_dismiss_form/999999999',
        '/allocations/xras_history/999999999',
    ])
    def test_modal_bodies_report_not_found_inline(self, auth_client, path):
        resp = auth_client.get(path)
        assert resp.status_code == 200
        assert b'Project not found' in resp.data


class TestActivationModalBodies:

    def test_history_renders_for_a_real_project(self, auth_client,
                                                active_project):
        resp = auth_client.get(
            f'/allocations/xras_history/{active_project.project_id}')
        assert resp.status_code == 200
        assert b'Activation history' in resp.data
        # An untouched project has no events, and says so rather than rendering
        # an empty list that reads as a loading failure.
        assert b'Nothing has been recorded' in resp.data
        assert b'Add a comment' in resp.data

    def test_dismiss_form_renders_and_states_it_is_reversible(
            self, auth_client, active_project):
        resp = auth_client.get(
            f'/allocations/xras_dismiss_form/{active_project.project_id}')
        assert resp.status_code == 200
        assert b'Reason' in resp.data
        assert b'ot permanent' in resp.data
        assert b'Restore' in resp.data

    def test_dismiss_form_does_not_promise_to_hide_the_row(
            self, auth_client, active_project):
        """The copy outlived the behavior once already. Dismissing stopped
        removing anything when the card became a ledger keyed on actions —
        a modal still promising to hide the project describes the old card."""
        resp = auth_client.get(
            f'/allocations/xras_dismiss_form/{active_project.project_id}')
        body = resp.data.decode()
        assert 'hides the project' not in body
        assert 'row stays' in body

    @pytest.mark.parametrize('endpoint', ['xras_comment'])
    def test_a_blank_note_is_rejected_with_a_VISIBLE_error(
            self, auth_client, active_project, endpoint):
        """`_strip_empty_strings` drops '' but not '   ' — the post_load guard is
        what stops a whitespace-only note passing `Length(min=1)`.

        WARNING: Asserting on the *rendered* error, not merely on a re-render.
        The field macros read `field_errors` out of the template context, and a
        `{% from ... import %}` without `with context` gives them none — so the
        form comes back looking untouched and the rejection is completely silent.
        That is exactly what happened here, and only a browser pass caught it.
        Matching the word "required" alone is NOT enough: `required=True` puts a
        literal `required` attribute on the textarea, so that assertion passes
        against a form with no error rendered at all.
        """
        resp = auth_client.post(
            f'/allocations/{endpoint}/{active_project.project_id}',
            data={'comment': '   '})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'invalid-feedback' in html, 'no field-error block rendered'
        assert 'This field is required.' in html

    @pytest.mark.parametrize('posted', [{}, {'comment': ''}, {'comment': '   '}])
    def test_dismiss_accepts_an_empty_reason(self, auth_client, active_project,
                                             monkeypatch, posted):
        """The reason is optional: absent, empty and whitespace all dismiss, and
        the event stores NULL rather than a blank string."""
        from webapp.dashboards.allocations.xras import lifecycle_routes as blueprint
        seen = []
        monkeypatch.setattr(blueprint, '_record_activation_event',
                            lambda project, event_type, **kw: seen.append((event_type, kw)))
        resp = auth_client.post(
            f'/allocations/xras_dismiss/{active_project.project_id}', data=posted)
        assert resp.status_code == 200
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')
        assert seen == [('dismissed', {'comment': None})]

    def test_dismiss_keeps_a_reason_when_given(self, auth_client, active_project,
                                               monkeypatch):
        from webapp.dashboards.allocations.xras import lifecycle_routes as blueprint
        seen = []
        monkeypatch.setattr(blueprint, '_record_activation_event',
                            lambda project, event_type, **kw: seen.append((event_type, kw)))
        resp = auth_client.post(
            f'/allocations/xras_dismiss/{active_project.project_id}',
            data={'comment': '  Duplicate of UHSS0002.  '})
        assert resp.status_code == 200
        assert seen == [('dismissed', {'comment': 'Duplicate of UHSS0002.'})]

    def test_the_dismiss_form_marks_the_reason_optional(self, auth_client,
                                                        active_project):
        resp = auth_client.get(
            f'/allocations/xras_dismiss_form/{active_project.project_id}')
        html = resp.data.decode()
        assert '(optional)' in html
        assert 'required' not in html.split('<textarea', 1)[1].split('>', 1)[0]


class TestActivityCardGating:
    """Contact details are fetched in the ROUTE only for MANAGE_XRAS, so a
    VIEW-only response cannot leak them through view-source."""

    def test_view_only_card_has_no_actions_column(self, view_only_client):
        resp = view_only_client.get('/allocations/xras_pending_fragment')
        assert resp.status_code == 200
        assert b'Recipients' not in resp.data
        assert b'xras_notify' not in resp.data
        assert b'xras_activate' not in resp.data

    def test_the_filter_form_lives_on_the_page_not_the_fragment(
            self, auth_client):
        """Outside the fragment on purpose: the container re-fetches a BARE
        hx-get on refreshXrasTab, and hx-include on it is what carries the
        window and the chips through a write."""
        resp = auth_client.get('/allocations/xras')
        assert resp.status_code == 200
        assert b'xras-activity-filters' in resp.data
        # The pane now includes TWO forms: the shared window control and its
        # own facet state. The window moved to the page shell so that only one
        # start_date/end_date pair exists across the three tabs.
        assert b'hx-include="#xras-window-filters, #xras-activity-filters"' \
            in resp.data


@pytest.fixture
def manage_not_admin_client(auth_client, monkeypatch):
    """`benkirk` with MANAGE_XRAS but *without* ADMIN_XRAS — the bar the bulk
    dismiss sits above (the Part C pattern)."""
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without_admin_xras(user):
        return {p for p in real(user) if p is not Permission.ADMIN_XRAS}

    monkeypatch.setattr(rbac, 'get_user_permissions', _without_admin_xras)
    return auth_client


class TestBulkDismissNotifyOnlyIsAdminOnly:
    """The bulk 'dismiss notify-only' lever is ADMIN_XRAS — a higher bar than the
    per-row Dismiss (MANAGE_XRAS), because it clears many projects at once."""

    PATH = '/allocations/xras_dismiss_notify_only'

    def test_a_manage_only_operator_is_forbidden(self, manage_not_admin_client):
        """MANAGE_XRAS alone is not enough — the whole point of the gate.
        (`view_only_client` is no negative here: it strips MANAGE but KEEPS
        ADMIN_XRAS, so it is authorized.)"""
        assert manage_not_admin_client.post(self.PATH).status_code == 403

    def test_a_no_xras_operator_is_forbidden(self, non_admin_client):
        assert non_admin_client.post(self.PATH).status_code == 403

    def test_requires_login(self, client):
        assert client.post(self.PATH).status_code in (302, 401)

    def test_an_admin_gets_a_reload_trigger(self, auth_client):
        """Empty DB -> zero targets, but the route still succeeds and fires the
        card reload; '0' is a valid, distinguishable outcome."""
        resp = auth_client.post(self.PATH)
        assert resp.status_code == 200
        assert 'refreshXrasTab' in resp.headers.get('HX-Trigger', '')

    def test_the_button_renders_for_an_admin_with_targets(self, auth_client,
                                                          monkeypatch):
        from webapp.dashboards.allocations.xras import card_routes
        monkeypatch.setattr(card_routes, 'notify_only_project_ids',
                            lambda rows: [1, 2, 3])
        body = auth_client.get('/allocations/xras_pending_fragment').get_data(
            as_text=True)
        assert 'xras_dismiss_notify_only' in body
        assert 'Dismiss 3 notify-only' in body

    def test_the_button_is_hidden_from_a_manage_only_operator(
            self, manage_not_admin_client, monkeypatch):
        from webapp.dashboards.allocations.xras import card_routes
        monkeypatch.setattr(card_routes, 'notify_only_project_ids',
                            lambda rows: [1, 2, 3])
        body = manage_not_admin_client.get(
            '/allocations/xras_pending_fragment').get_data(as_text=True)
        assert 'xras_dismiss_notify_only' not in body

    def test_the_button_is_absent_with_no_targets(self, auth_client):
        body = auth_client.get('/allocations/xras_pending_fragment').get_data(
            as_text=True)
        assert 'xras_dismiss_notify_only' not in body


class TestStatusVocabularyIsRenderable:
    """Every status the table can hold must have a badge, a label and a tooltip.

    The vocabulary tuples and ``badges.html`` are files that have to agree, and
    nothing else makes them. The macro falls back to ``bg-secondary`` with the raw string
    for an unknown state, so a missing entry does not raise — it renders a gray chip
    labeled ``unmapped`` with no explanation, on the page an operator reaches for when
    something has gone wrong. Silent, and exactly when it matters most.

    ``badges.html`` is a **shared** namespace: three domains' vocabularies live in one
    flat set of dicts. Only XRAS was gated here originally; notification statuses and
    scheduled-task run states are covered now too, so the next domain to join cannot
    quietly render gray. (The collision that namespace already carries is ``manual``,
    which is XRAS's — scheduled-task *triggers* are deliberately NOT rendered through
    this macro for that reason. See the note in badges.html.)

    Parsed out of the template source rather than rendered, because these are Jinja
    ``{%- set -%}`` literals with no macro that exposes them.
    """

    BADGES = (Path(__file__).resolve().parents[2] / 'src' / 'webapp' / 'templates'
              / 'dashboards' / 'fragments' / 'badges.html')

    def _keys(self, dict_name):
        source = self.BADGES.read_text()
        body = re.search(rf'{dict_name} = \{{(.*?)\}}', source, re.S)
        assert body, f'{dict_name} not found in badges.html'
        return set(re.findall(r"'([^']+)':", body.group(1)))

    @staticmethod
    def _vocabulary(name):
        if name == 'XRAS_ACTION_STATUSES':
            from sam.queries.xras_actions import XRAS_ACTION_STATUSES
            return XRAS_ACTION_STATUSES
        if name == 'NOTIFICATION_STATUSES':
            from sam.notify import NOTIFICATION_STATUSES
            return NOTIFICATION_STATUSES
        from system_status.models.task_run import TASK_STATES
        return TASK_STATES

    @pytest.mark.parametrize(
        'dict_name', ['_STATUS_VARIANTS', '_STATUS_LABELS', '_STATUS_TOOLTIPS'])
    @pytest.mark.parametrize(
        'vocabulary', ['XRAS_ACTION_STATUSES', 'NOTIFICATION_STATUSES',
                       'TASK_STATES'])
    def test_every_status_has_an_entry(self, dict_name, vocabulary):
        missing = set(self._vocabulary(vocabulary)) - self._keys(dict_name)
        assert not missing, \
            f'{dict_name} is missing {sorted(missing)} from {vocabulary}'


class TestNotifyForceToggle:
    """The force override on the Notify modal, and the thing that reveals it.

    Suppression is right by default: the dedup key exists so that re-opening the
    modal and clicking Send does not mail the same handoff twice. But the cases
    that actually reach an operator — a bad address since corrected, a template
    fixed after the fact, a recipient who lost the mail — are exactly the cases
    where a second send is the point, and before this the only recovery was a
    ``DELETE`` against ``notification_log``.

    Rendered directly rather than driven over HTTP: the route needs a committed
    inactive project, a committed ``xras_action_log`` row naming it *and* a
    committed ``notification_log`` row before the toggle can appear at all, and
    none of that exercises the thing worth pinning. What can silently break is
    the template — dropping the checkbox, or dropping the ``hx-include`` that is
    the only reason its value ever reaches the route.

    ``Notifier.send_many(force=True)`` bypassing suppression is covered a layer
    down, in ``test_notify_service.py::test_force_overrides_suppression``.
    """

    TEMPLATE = 'dashboards/allocations/partials/xras_notify_form.html'

    def _render(self, app, already_notified, people=None):
        from types import SimpleNamespace

        from flask import render_template

        if people is None:
            people = [{'name': 'Ben Kirk', 'email': 'benkirk@ucar.edu',
                       'role': 'lead'}]
        with app.test_request_context():
            return render_template(
                self.TEMPLATE,
                project=SimpleNamespace(projcode='UHSS0001'),
                people=people,
                preview=None,
                preview_error=None,
                already_notified=already_notified,
                notify_enabled=True,
                redirect_to=None,
                post_url='/allocations/xras_notify/1',
            )

    def _recipient(self, address='benkirk@ucar.edu'):
        from types import SimpleNamespace
        return SimpleNamespace(address=address, name='Ben Kirk', role='lead')

    def test_absent_when_nothing_would_be_suppressed(self, app):
        """The ordinary case. A toggle the operator sees every time is one they
        learn to tick without reading it, which is the failure mode that makes
        an override worse than not having one."""
        body = self._render(app, already_notified=[])
        # Assert on the checkbox itself, not the bare id: the Send button's
        # `hx-include` names that id unconditionally, which is deliberate —
        # a selector matching nothing is harmless, and making it conditional
        # is one more thing to forget.
        assert 'id="xrasNotifyForce"' not in body
        assert 'name="force"' not in body
        assert 'already been notified' not in body

    def test_offered_when_a_duplicate_would_be_suppressed(self, app):
        body = self._render(app, already_notified=[self._recipient()])
        assert 'name="force"' in body
        assert 'id="xrasNotifyForce"' in body
        assert 'already been notified' in body
        assert 'benkirk@ucar.edu' in body

    def test_send_button_includes_the_checkbox(self, app):
        """Without ``hx-include`` the box renders, ticks, and is never sent —
        the request carries no ``force`` key and the send is suppressed anyway,
        with the UI showing a control that does nothing."""
        body = self._render(app, already_notified=[self._recipient()])
        assert 'hx-include="#xrasNotifyForce"' in body

    def test_partial_suppression_names_the_count(self, app):
        """Two recipients, one already told. The operator needs to see that the
        situation is mixed before deciding — "these recipients" would misdescribe
        it, and the addresses listed are the ones the override actually acts on."""
        body = self._render(
            app,
            already_notified=[self._recipient('lead@ucar.edu')],
            people=[{'name': 'Lead', 'email': 'lead@ucar.edu', 'role': 'lead'},
                    {'name': 'Admin', 'email': 'admin@ucar.edu', 'role': 'admin'}],
        )
        assert '1 of 2 recipients have' in body
        assert 'These recipients have' not in body


class TestActivityRowExpansion:
    """The inline delivery detail, and the two things that can silently break it.

    Rendered directly rather than driven over HTTP: the route needs committed
    action, project and notification rows before an expandable row exists at
    all, and none of that exercises what is actually fragile — where the
    collapse toggle sits, and who is allowed to see the addresses inside.
    """

    TEMPLATE = 'dashboards/allocations/partials/xras_activity_card.html'

    def _row(self, **over):
        from types import SimpleNamespace
        row = {
            'action_log_id': 42, 'action_type': 'Supplement',
            'service': 'supplement', 'received_time': None, 'status': 'processed',
            'projcode': 'UHSS0001', 'project_id': 7, 'title': 'A project',
            'project_active': True, 'is_latest_action': True,
            'kind': 'xras_supplement', 'notifiable': True,
            'dismissed': False, 'dismissed_time': None, 'dismissed_by': None,
            'dismissed_reason': None, 'comment_count': 0,
            'needs_activation': False, 'tags': ['notified'],
            'notified': True, 'notified_time': None, 'notified_age': None,
            'delivered_count': 1, 'failed_count': 0, 'suppressed_count': 0,
            'notifications': [SimpleNamespace(
                status='sent', recipient='pi@example.edu',
                intended_recipient=None, creation_time=None,
                requested_by='benkirk', error=None)],
        }
        row.update(over)
        return row

    def _render(self, app, *, may_manage, rows=None, **counts):
        from flask import render_template
        counts = {'show_all': False, 'attention_total': 1, 'window_total': 1,
                  'hidden_count': 0, 'outside_count': 0, 'recent_days': 3,
                  **counts}
        with app.test_request_context():
            return render_template(
                self.TEMPLATE,
                rows=rows if rows is not None else [self._row()],
                recipients={7: [{'name': 'A PI', 'email': 'pi@example.edu',
                                 'role': 'lead'}]} if may_manage else {},
                may_activate={}, may_manage=may_manage,
                window={'days': 30, 'since': None, 'until': None,
                        'start_date': '', 'end_date': '', 'custom': False},
                window_pill_choices=((7, '7D'), (30, '30D')),
                tag_values=[], type_values=[],
                selected_tags=[], selected_types=[],
                form_id='xras-activity-filters',
                fragment_url='/allocations/xras_pending_fragment',
                target_id='alloc-xras-pending', **counts)

    def _actions_cell(self, body):
        cell = body.split('<td class="text-end"')[1].split('</td>')[0]
        assert 'xras_history' in cell, 'wrong cell — test is vacuous'
        return cell

    def test_the_actions_are_one_icon_only_strip(self, app):
        """Four verbs at a fixed width: the label is the `title`, never text
        beside the icon, so a row with everything to offer stays one line."""
        cell = self._actions_cell(self._render(app, may_manage=True))
        assert 'btn-group btn-group-sm' in cell
        for button in cell.split('<button')[1:]:
            visible = button.split('>', 1)[1]
            for tag in ('<i class', '</i>', '<span class', '</span>', '</button>'):
                visible = visible.replace(tag, '')
            assert 'Notify' not in visible and 'Activate' not in visible
            assert 'aria-label="' in button

    def test_every_live_row_offers_dismiss_and_a_dismissed_one_restore(self, app):
        """Dismiss is how a row leaves the attention queue, so it is not gated
        on needing activation any more than Notify is."""
        live = self._actions_cell(self._render(app, may_manage=True))
        assert 'xras_dismiss_form' in live and 'xras_restore' not in live
        gone = self._actions_cell(self._render(
            app, may_manage=True,
            rows=[self._row(dismissed=True, dismissed_by='benkirk',
                            tags=['notified', 'dismissed'])]))
        assert 'xras_restore' in gone and 'xras_dismiss_form' not in gone

    def test_a_dismissal_without_a_reason_never_renders_none(self, app):
        html = self._render(
            app, may_manage=True, show_all=True,
            rows=[self._row(dismissed=True, dismissed_by='benkirk',
                            dismissed_reason=None, tags=['notified', 'dismissed'])])
        assert 'title="None"' not in html
        assert 'No reason given' in html

    def test_the_state_column_renders_only_under_everything(self, app):
        """In the queue the column could only ever say Active or Needs
        activation; the latter rides on the Action cell there instead."""
        queue = self._render(app, may_manage=True,
                             rows=[self._row(needs_activation=True, project_active=False)])
        assert '>State<' not in queue
        action_cell = queue.split('<span class="badge bg-secondary">Supplement</span>')[1].split('</td>')[0]
        assert 'Needs activation' in action_cell
        everything = self._render(app, may_manage=True, show_all=True,
                                  rows=[self._row(needs_activation=True, project_active=False)])
        assert '>State<' in everything
        assert everything.count('Needs activation') == 1

    def test_the_expansion_spans_the_columns_actually_drawn(self, app):
        """Only a manager gets an expansion row, so both cases carry Actions."""
        for show_all, span in ((True, 7), (False, 6)):
            body = self._render(app, may_manage=True, show_all=show_all)
            assert f'colspan="{span}"' in body, show_all

    def test_the_strip_never_wraps(self, app):
        cell = self._actions_cell(self._render(app, may_manage=True))
        assert 'btn-group btn-group-sm flex-nowrap' in cell

    def test_the_hidden_count_badge_names_the_switch(self, app):
        body = self._render(app, may_manage=True, window_total=3, hidden_count=2)
        assert '1 need attention' in body
        assert '2 more with Everything in the window' in body
        assert 'outside the date filter' not in body

    def test_show_all_warns_about_queue_rows_outside_the_window(self, app):
        body = self._render(app, may_manage=True, show_all=True,
                            attention_total=2, outside_count=1)
        assert '1 needing attention outside the date filter' in body
        assert 'more with Everything in the window' not in body
        assert 'need attention</span>' not in body

    def test_the_actions_cell_carries_no_collapse_toggle(self, app):
        """Bootstrap's collapse data-api runs in the CAPTURE phase on document,
        so a toggle anywhere up the tree from a button fires BEFORE that
        button's own handler and `stopPropagation` cannot help. Notify and
        Activate live in the last cell; the toggle must not.
        See dashboards/fragments/collapse.html."""
        body = self._render(app, may_manage=True)
        actions_cell = body.split('<td class="text-end"')[1].split('</td>')[0]
        assert 'data-bs-toggle="collapse"' not in actions_cell
        assert 'xras_notify_form' in actions_cell, 'wrong cell — test is vacuous'

    def test_the_row_itself_carries_no_collapse_toggle(self, app):
        """Same reason, one level up: on the <tr> it would swallow every
        button in the row."""
        body = self._render(app, may_manage=True)
        for line in body.splitlines():
            if line.strip().startswith('<tr'):
                assert 'data-bs-toggle="collapse"' not in line

    def test_the_delivery_detail_renders_for_a_manager(self, app):
        body = self._render(app, may_manage=True)
        assert 'id="xras-activity-42"' in body
        assert 'pi@example.edu' in body

    def test_the_delivery_detail_is_absent_for_view_only(self, app):
        """Every delivery row names a real person's address — the same reason
        the Recipients column is MANAGE_XRAS."""
        body = self._render(app, may_manage=False)
        assert 'id="xras-activity-42"' not in body
        assert 'pi@example.edu' not in body

    def test_a_row_with_no_deliveries_still_expands_to_show_recipients(self, app):
        """The expansion is where the addresses live now that the Recipients
        column is gone, so an un-notified row must still open — that is exactly
        the row where an operator wants to see who is about to be mailed."""
        body = self._render(app, may_manage=True,
                            rows=[self._row(notifications=[], notified=False,
                                            delivered_count=0,
                                            tags=['not_notified'])])
        assert 'id="xras-activity-42"' in body
        assert 'pi@example.edu' in body
        assert 'Not notified' in body
        assert 'Delivery' not in body, 'no deliveries — no Delivery table'

    def test_a_notifiable_row_with_nobody_to_mail_says_so_in_the_row(self, app):
        """The one thing the Recipients column carried that was a PROBLEM
        rather than a fact. Buried in the expansion it would go unseen, so it
        is promoted to the Notified cell."""
        from flask import render_template
        with app.test_request_context():
            body = render_template(
                self.TEMPLATE,
                rows=[self._row(notifications=[], notified=False,
                                delivered_count=0, tags=['not_notified'])],
                recipients={},          # <- nobody on file
                may_activate={}, may_manage=True,
                window={'days': 30, 'since': None, 'until': None,
                        'start_date': '', 'end_date': '', 'custom': False},
                window_pill_choices=((7, '7D'), (30, '30D')),
                tag_values=[], type_values=[],
                selected_tags=[], selected_types=[],
                form_id='xras-activity-filters',
                fragment_url='/allocations/xras_pending_fragment',
                target_id='alloc-xras-pending')
        assert 'No recipients' in body
        assert 'No lead or admin email address on file' in body

    def test_a_row_needing_activation_offers_no_notify_for_an_unmapped_service(
            self, app):
        """A service with no kind would make Notify post a message nobody can
        build. `transfer` is the only one left — `adjust` gained a kind once
        the reduction wording was written."""
        body = self._render(app, may_manage=True,
                            rows=[self._row(kind=None, notifiable=False,
                                            action_type='Transfer',
                                            notifications=[], notified=False,
                                            tags=[])])
        assert 'xras_notify_form' not in body


# `TestSignedIncrements` moved to tests/unit/test_xras_notices_builder.py
# along with the helper it exercises — it was the one white-box test in this
# file, reaching past the routes into a private blueprint symbol.
