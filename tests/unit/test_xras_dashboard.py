"""HTTP-tier tests for the Allocations > XRAS page.

Scope follows the house convention: the HTTP tier covers **auth, 404 and render
smoke**. Route handlers use Flask-SQLAlchemy's ``db.session`` on its own
connection and only see committed snapshot rows, so happy-path writes are
covered a layer down — replay's behaviour lives in
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

    def test_replay_is_gated_on_manage_not_view(self, view_only_client):
        """The write needs MANAGE_XRAS even though the page needs only VIEW."""
        resp = view_only_client.post('/allocations/xras_replay/1')
        assert resp.status_code == 403

    def test_view_only_user_still_gets_the_page(self, view_only_client):
        assert view_only_client.get('/allocations/xras').status_code == 200


class TestXrasFragments:
    def test_table_fragment_renders(self, auth_client):
        resp = auth_client.get('/allocations/xras_fragment')
        assert resp.status_code == 200
        # The summary strip enumerates every status, including at zero — an
        # absent bucket would read as "not measured" rather than "none".
        for state in (b'Received', b'Processed', b'Manual', b'Failed', b'Replayed'):
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
        """The card can only see projects this log knows about. While capture
        mode is on, "empty" must not be presented as "all clear"."""
        resp = auth_client.get('/allocations/xras_pending_fragment')
        if b'No XRAS projects awaiting activation' in resp.data:
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
        # One per status in the fixed vocabulary. A superset is legal — an
        # out-of-vocabulary status gets its own chip (see the test below) — but
        # nothing in the snapshot should produce one, so this asserts the floor
        # exactly rather than loosening to >=.
        #
        # Read from the tuple rather than spelled out: this is a check that the
        # strip renders the vocabulary, not a second copy of it. Spelling it out is
        # what made adding `unmapped` break two tests that had no opinion about it.
        assert len(chips) == len(XRAS_ACTION_STATUSES)
        # Every chip writes into the form the filter panel actually renders.
        assert {form_id for form_id, _ in chips} == {'xras-filters'}
        assert {value for _, value in chips} == set(XRAS_ACTION_STATUSES)

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

        from webapp.dashboards.allocations.blueprint import _parse_xras_filters

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
        """Unbounded is the DEFAULT, not the behaviour — an explicit To date is
        still honoured, normalised to the end of that day."""
        from werkzeug.datastructures import MultiDict

        from webapp.dashboards.allocations.blueprint import _parse_xras_filters

        filters, _, _ = _parse_xras_filters(
            MultiDict([('end_date', '2026-01-15')]))
        assert filters['end_date'].strftime('%Y-%m-%d %H:%M:%S') \
            == '2026-01-15 23:59:59'


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
        assert b'not permanent' in resp.data

    @pytest.mark.parametrize('endpoint', ['xras_comment', 'xras_dismiss'])
    def test_a_blank_note_is_rejected_with_a_VISIBLE_error(
            self, auth_client, active_project, endpoint):
        """`_strip_empty_strings` drops '' but not '   ' — the post_load guard is
        what stops a whitespace-only note passing `Length(min=1)`.

        ⚠️  Asserting on the *rendered* error, not merely on a re-render.
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


class TestPendingCardGating:
    """Contact details are fetched in the ROUTE only for MANAGE_XRAS, so a
    VIEW-only response cannot leak them through view-source."""

    def test_view_only_card_has_no_actions_column(self, view_only_client):
        resp = view_only_client.get('/allocations/xras_pending_fragment')
        assert resp.status_code == 200
        assert b'Recipients' not in resp.data
        assert b'xras_notify' not in resp.data
        assert b'xras_activate' not in resp.data

    def test_view_only_user_cannot_force_dismissed_rows(self, view_only_client):
        """`include_dismissed` is ANDed with MANAGE_XRAS in the route — a
        hand-typed query param must not reveal the hidden set."""
        resp = view_only_client.get(
            '/allocations/xras_pending_fragment?include_dismissed=1')
        assert resp.status_code == 200
        assert b'Restore' not in resp.data

    def test_manage_user_gets_the_show_dismissed_toggle_on_the_page(
            self, auth_client):
        """The toggle lives on the PAGE, outside the fragment: if every pending
        row were dismissed, a toggle inside the fragment would vanish with the
        table and the rows would be unrecoverable."""
        resp = auth_client.get('/allocations/xras')
        assert resp.status_code == 200
        assert b'include_dismissed' in resp.data
        assert b'Show dismissed' in resp.data

    def test_view_only_user_does_not_get_the_toggle(self, view_only_client):
        resp = view_only_client.get('/allocations/xras')
        assert resp.status_code == 200
        assert b'Show dismissed' not in resp.data


class TestStatusVocabularyIsRenderable:
    """Every status the table can hold must have a badge, a label and a tooltip.

    ``XRAS_ACTION_STATUSES`` and ``badges.html`` are two files that have to agree, and
    nothing else makes them. The macro falls back to ``bg-secondary`` with the raw string
    for an unknown state, so a missing entry does not raise — it renders a grey chip
    labelled ``unmapped`` with no explanation, on the page an operator reaches for when
    something has gone wrong. Silent, and exactly when it matters most.

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

    @pytest.mark.parametrize(
        'dict_name', ['_STATUS_VARIANTS', '_STATUS_LABELS', '_STATUS_TOOLTIPS'])
    def test_every_status_has_an_entry(self, dict_name):
        from sam.queries.xras_actions import XRAS_ACTION_STATUSES

        missing = set(XRAS_ACTION_STATUSES) - self._keys(dict_name)
        assert not missing, f'{dict_name} is missing {sorted(missing)}'
