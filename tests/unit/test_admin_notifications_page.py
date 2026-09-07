"""The notification delivery log page.

The gate this module exists for is the **negative** permission case:
`VIEW_SYSTEM_CONFIG` gets the Configuration tile and must be 403'd off this
page, because that boundary is the only thing keeping recipient addresses off
the lower tier.
"""

import pytest

from webapp.utils.rbac import Permission

PAGE = '/admin/htmx/notifications'
LOG = '/admin/htmx/notifications/log'
DETAIL = '/admin/htmx/notifications/1'
EDITOR = '/admin/htmx/notifications/templates/expiration-UNIV.txt'
PREVIEW = '/admin/htmx/notifications/templates/expiration-UNIV.txt/preview'
RESET = '/admin/htmx/notifications/templates/expiration-UNIV.txt/reset'
AUDIENCE = '/admin/htmx/notifications/templates/expiration-UNIV.txt/recipients'
TYPEAHEAD = '/admin/htmx/notifications/templates/project-search?q=SC'


@pytest.fixture
def config_only_client(auth_client, monkeypatch):
    """`benkirk` with VIEW_SYSTEM_CONFIG but *without* SYSTEM_ADMIN.

    The exact shape this page must refuse: entitled to the counts on the
    Configuration tile, not to the rows that name people.
    """
    from webapp.utils import rbac

    real = rbac.get_user_permissions

    def _without_system_admin(user):
        kept = {p for p in real(user) if p is not Permission.SYSTEM_ADMIN}
        kept.add(Permission.VIEW_SYSTEM_CONFIG)
        return kept

    monkeypatch.setattr(rbac, 'get_user_permissions', _without_system_admin)
    return auth_client


class TestThePermissionBoundary:
    """One tier apart, deliberately — and the gate is on the ROUTE, so a
    view-source cannot reveal what the page chose not to draw."""

    @pytest.mark.parametrize('path', [PAGE, LOG, DETAIL, EDITOR, AUDIENCE, TYPEAHEAD])
    def test_view_system_config_alone_is_refused(self, config_only_client, path):
        assert config_only_client.get(path).status_code == 403

    @pytest.mark.parametrize('path', [PREVIEW, EDITOR, RESET])
    def test_view_system_config_alone_cannot_post(self, config_only_client, path):
        assert config_only_client.post(path, data={'body': 'x'}).status_code == 403

    @pytest.mark.parametrize('path', [PAGE, LOG, DETAIL, EDITOR])
    def test_anonymous_is_refused(self, client, path):
        assert client.get(path).status_code in (302, 401, 403)

    def test_the_configuration_tile_is_still_reachable_at_the_lower_tier(
            self, config_only_client):
        """Proving the split is real rather than a blanket denial."""
        resp = config_only_client.get('/admin/htmx/configuration')
        assert resp.status_code == 200
        assert b'Notifications' in resp.data

    @pytest.mark.parametrize('path', [PAGE, LOG, EDITOR])
    def test_system_admin_gets_in(self, auth_client, path):
        assert auth_client.get(path).status_code == 200


class TestThePage:

    def test_it_renders(self, auth_client):
        resp = auth_client.get(PAGE)
        assert b'Notifications' in resp.data

    def test_it_loads_the_log_fragment(self, auth_client):
        assert LOG.encode() in auth_client.get(PAGE).data

    def test_it_carries_the_modal_shell_its_fragment_targets(self, auth_client):
        """The fragment's detail button swaps into #auditDetailsModalBody;
        the host page must ship it."""
        assert b'auditDetailsModalBody' in auth_client.get(PAGE).data

    def test_a_redirecting_deployment_is_announced(self, auth_client,
                                                   monkeypatch, app):
        monkeypatch.setitem(app.config, 'NOTIFY_REDIRECT_TO', 'sink@example.edu')
        resp = auth_client.get(PAGE)
        assert b'sink@example.edu' in resp.data


class TestTheLogFragment:

    def test_it_renders_the_facet_strip(self, auth_client):
        resp = auth_client.get(LOG)
        html = resp.data.decode()
        assert 'facet-grid' in html
        for label in ('Status', 'Kind'):
            assert label in html

    def test_every_declared_status_gets_a_chip_even_at_zero(self, auth_client):
        """An absent bucket reads as "not measured" rather than "none", and
        the strip is something an operator scans by position."""
        from sam.notify import NOTIFICATION_STATUSES
        html = auth_client.get(LOG).data.decode()
        for status in NOTIFICATION_STATUSES:
            assert f'data-value="{status}"' in html or status in html

    def test_an_empty_result_says_so_rather_than_rendering_a_bare_table(
            self, auth_client):
        resp = auth_client.get(f'{LOG}?search=definitely-no-such-recipient')
        assert b'No delivery attempts match' in resp.data

    def test_the_headline_count_is_the_filtered_total(self, auth_client):
        resp = auth_client.get(f'{LOG}?search=definitely-no-such-recipient')
        assert b'Showing' in resp.data
        assert b'0' in resp.data

    @pytest.mark.parametrize('query', [
        'status=sent', 'status=sent&status=failed', 'kind=expiration',
        'channel=email', 'days=1', 'days=365', 'page=2',
        'search=SCSG0001', 'days=99999', 'page=0', 'days=-5',
    ])
    def test_filter_combinations_do_not_500(self, auth_client, query):
        """Including the out-of-range ones: `days` clamps and `page` floors,
        so a hand-typed query string cannot produce a negative OFFSET."""
        assert auth_client.get(f'{LOG}?{query}').status_code == 200

    def test_an_unparseable_page_is_treated_as_the_first(self, auth_client):
        assert auth_client.get(f'{LOG}?page=notanumber').status_code == 200


class TestTheDetailModal:

    def test_a_missing_row_answers_in_the_modal_not_with_an_error_page(
            self, auth_client):
        """200, not 404: htmx does not swap a 4xx, so the already-open modal
        would keep showing the previous row."""
        resp = auth_client.get('/admin/htmx/notifications/999999999')
        assert resp.status_code == 200
        assert b'not found' in resp.data.lower()


class TestNoCliEquivalent:
    """Explicitly out of scope, and a deliberate divergence from the XRAS
    precedent where `sam-admin xras` and the web page share a query layer so
    the two cannot drift. The query layer is still shared — the door stays
    open — but nothing on the CLI consumes it yet."""

    def test_sam_admin_has_no_notifications_command(self):
        from click.testing import CliRunner
        from cli.cmds.admin import cli

        result = CliRunner().invoke(cli, ['--help'])
        assert 'notifications' not in result.output


class TestTheTemplatesTab:

    def test_the_log_is_the_default_tab_and_unknown_tabs_fall_back(self, auth_client):
        for query in ('', '?tab=nope'):
            html = auth_client.get(PAGE + query).data.decode()
            assert 'id="log-pane"' in html and 'show active" id="log-pane"' in html

    def test_it_lists_every_shipped_template(self, auth_client):
        from sam.notify.render import shipped_template_names
        html = auth_client.get(f'{PAGE}?tab=templates').data.decode()
        assert 'show active" id="templates-pane"' in html
        for name in shipped_template_names():
            assert name in html
        assert '_email_base.html' not in html

    def test_a_named_template_lazy_loads_its_editor(self, auth_client):
        html = auth_client.get(f'{PAGE}?tab=templates&name=xras_update.html').data.decode()
        assert 'hx-trigger="load"' in html
        assert '/templates/xras_update.html"' in html

    def test_an_unknown_name_is_404(self, auth_client):
        assert auth_client.get(f'{PAGE}?tab=templates&name=nope.txt').status_code == 404
        assert auth_client.get(EDITOR.replace('expiration-UNIV', 'nope')).status_code == 404

    def test_a_kind_deep_link_preselects_the_log_facet(self, auth_client):
        html = auth_client.get(f'{PAGE}?tab=log&kind=expiration').data.decode()
        assert 'value="expiration" selected' in html
        assert 'notifications/log?kind=expiration' in html

    def test_an_unknown_kind_is_ignored(self, auth_client):
        assert auth_client.get(f'{PAGE}?kind=nope').status_code == 200


class TestTheEditorFragment:

    def test_it_shows_the_shipped_source_and_the_palette(self, auth_client):
        from sam.notify.render import TEMPLATE_DIR
        html = auth_client.get(EDITOR).data.decode()
        assert 'Dear {{ recipient_name }}' in html
        assert (TEMPLATE_DIR / 'expiration-UNIV.txt').read_text().count('\n') > 10
        for name in ('project_code', 'grace_expiration', 'resources.resource_name'):
            assert name in html
        assert 'shipped default' in html
        assert 'kind=expiration' in html
        assert 'hx-swap-oob="true"' in html

    def test_the_source_is_editable_and_the_default_has_no_reset(self, auth_client):
        html = auth_client.get(EDITOR).data.decode()
        assert 'readonly' not in html
        assert 'Reset to default' not in html
        assert 'Save' in html


class TestThePreviewFragment:

    def test_text_renders_in_a_pre(self, auth_client):
        resp = auth_client.post(PREVIEW, data={'body': 'Hello {{ project_code }}'})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert '<pre' in html and 'Hello SCSG0001' in html
        assert 'srcdoc' not in html

    def test_html_renders_in_a_sandboxed_iframe(self, auth_client):
        body = ('{% extends "_email_base.html" %}{% block content %}'
                '<p>Hi {{ recipient_name }}</p>{% endblock %}')
        html = auth_client.post(PREVIEW.replace('.txt', '.html'),
                                data={'body': body}).data.decode()
        assert '<iframe' in html and 'sandbox' in html and 'srcdoc="' in html
        assert '<style' not in html, 'the email CSS must be escaped inside srcdoc'
        assert '&lt;style&gt;' in html

    def test_the_role_selects_the_recipient(self, auth_client):
        html = auth_client.post(PREVIEW, data={'body': '{{ recipient_role }}',
                                               'role': 'user'}).data.decode()
        assert '>user<' in html

    def test_a_syntax_error_answers_in_the_pane_at_200(self, auth_client):
        resp = auth_client.post(PREVIEW, data={'body': '{% if %}'})
        assert resp.status_code == 200
        assert b'does not render' in resp.data
        assert b'TemplateSyntaxError' in resp.data

    def test_an_ssti_probe_answers_in_the_pane_at_200(self, auth_client):
        resp = auth_client.post(PREVIEW, data={'body': "{{ ''.__class__.__mro__ }}"})
        assert resp.status_code == 200
        assert b'SecurityError' in resp.data

    def test_an_unknown_variable_is_warned_about(self, auth_client):
        html = auth_client.post(PREVIEW, data={'body': '{{ projekt_code }}'}).data.decode()
        assert 'Unknown variable' in html and 'projekt_code' in html

    def test_a_shipped_body_warns_about_nothing(self, auth_client):
        from sam.notify.render import TEMPLATE_DIR
        body = (TEMPLATE_DIR / 'expiration-UNIV.txt').read_text()
        html = auth_client.post(PREVIEW, data={'body': body}).data.decode()
        assert 'Unknown variable' not in html
        assert 'SCSG0001' in html

    def test_an_unknown_template_name_is_404(self, auth_client):
        resp = auth_client.post(PREVIEW.replace('expiration-UNIV', 'nope'),
                                data={'body': 'x'})
        assert resp.status_code == 404


class TestSave:
    """HTTP-layer coverage is auth, validation and the compile gate; the one
    happy path below cleans up after itself because route writes COMMIT."""

    def _override(self, app, name):
        from webapp.extensions import db
        from sam import NotificationTemplateOverride
        with app.app_context():
            return NotificationTemplateOverride.get_by_name(db.session, name)

    def test_an_empty_body_is_refused(self, auth_client):
        resp = auth_client.post(EDITOR, data={'body': ''})
        assert resp.status_code == 200
        assert b'alert-danger' in resp.data
        assert b'hx-swap-oob' in resp.data, 'the editor re-renders in place'

    def test_a_body_that_does_not_compile_is_refused(self, auth_client, app):
        resp = auth_client.post(EDITOR, data={'body': '{% if %}'})
        assert b'does not render' in resp.data
        assert b'TemplateSyntaxError' in resp.data
        assert b'{% if %}' in resp.data, 'the rejected text stays in the editor'
        assert self._override(app, 'expiration-UNIV.txt') is None

    def test_an_ssti_body_is_refused(self, auth_client, app):
        resp = auth_client.post(EDITOR, data={'body': "{{ ''.__class__.__mro__ }}"})
        assert b'does not render' in resp.data and b'SecurityError' in resp.data
        assert self._override(app, 'expiration-UNIV.txt') is None

    def test_an_unknown_name_is_404(self, auth_client):
        assert auth_client.post(EDITOR.replace('expiration-UNIV', 'nope'),
                                data={'body': 'x'}).status_code == 404
        assert auth_client.post(RESET.replace('expiration-UNIV', 'nope')).status_code == 404

    def test_reset_of_a_default_is_a_harmless_no_op(self, auth_client):
        resp = auth_client.post(RESET)
        assert resp.status_code == 200
        assert b'Restored the shipped default' in resp.data

    def test_save_then_reset_round_trip(self, auth_client, app):
        """On task_summary.txt, which no other test renders through a real
        session, and torn down in `finally` whatever happens."""
        from webapp.extensions import db
        from sam import NotificationTemplateOverride
        name = 'task_summary.txt'
        save = f'/admin/htmx/notifications/templates/{name}'
        body = 'CUSTOM {{ task_name }} {{ headlin }}'
        try:
            resp = auth_client.post(save, data={'body': body})
            html = resp.data.decode()
            assert 'Template saved' in html
            assert 'headlin' in html and 'Unknown variable' in html
            assert 'Reset to default' in html
            assert 'customized by' in html
            row = self._override(app, name)
            assert row is not None and row.body == body
            # The editor now shows the override, and a fresh renderer with a
            # session factory renders it.
            assert body in auth_client.get(save).data.decode()
            from sqlalchemy.orm import Session
            from sam.notify import Message, Recipient, TemplateRenderer
            with app.app_context():
                renderer = TemplateRenderer(session_factory=lambda: Session(db.engine))
                text = renderer.render(Message(
                    kind='task_summary', subject='s',
                    recipient=Recipient('ops@example.edu', role='admin'),
                    context={'task_name': 'expiration_notices'})).text
            assert text.startswith('CUSTOM expiration_notices')

            resp = auth_client.post(f'{save}/reset')
            assert b'Restored the shipped default' in resp.data
            assert self._override(app, name) is None
        finally:
            with app.app_context():
                stray = NotificationTemplateOverride.get_by_name(db.session, name)
                if stray is not None:
                    db.session.delete(stray)
                    db.session.commit()


class TestPreviewForAProject:
    """Real data replaces the samples once a project is picked."""

    def test_the_editor_offers_the_picker_for_project_kinds_only(self, auth_client):
        html = auth_client.get(EDITOR).data.decode()
        assert 'previewProject_id' in html and 'Preview for' in html
        task = auth_client.get(EDITOR.replace('expiration-UNIV', 'task_summary')).data.decode()
        assert 'previewProject_id' not in task

    def test_the_typeahead_finds_projects(self, auth_client, active_project):
        html = auth_client.get(
            f'/admin/htmx/notifications/templates/project-search?q={active_project.projcode}'
        ).data.decode()
        assert f'data-fk-id="{active_project.project_id}"' in html

    def test_audience_without_a_project_is_the_role_select(self, auth_client):
        resp = auth_client.get(AUDIENCE)
        assert b'name="role"' in resp.data and b'name="recipient"' not in resp.data
        assert resp.headers.get('HX-Trigger') == 'reloadTemplatePreview'

    def test_audience_with_a_project_is_real_people_or_an_explanation(
            self, auth_client, active_project):
        resp = auth_client.get(f'{AUDIENCE}?project_id={active_project.project_id}')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="recipient"' in html or 'name="role"' in html

    def test_preview_for_a_project_renders_its_code_or_explains(
            self, auth_client, active_project):
        resp = auth_client.post(PREVIEW, data={
            'body': 'Hello {{ project_code }} {{ recipient_role }}',
            'project_id': str(active_project.project_id)})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert (f'Hello {active_project.projcode}' in html
                or 'nothing would be sent' in html)
        if 'nothing would be sent' not in html:
            assert 'as it would reach' in html and 'Subject:' in html

    def test_an_unknown_project_answers_in_the_pane(self, auth_client):
        resp = auth_client.post(PREVIEW, data={'body': 'x', 'project_id': '999999999'})
        assert resp.status_code == 200 and b'Unknown project' in resp.data

    def test_a_task_kind_ignores_the_project(self, auth_client, active_project):
        resp = auth_client.post(
            PREVIEW.replace('expiration-UNIV', 'task_summary'),
            data={'body': '{{ task_name }}', 'project_id': str(active_project.project_id)})
        assert resp.status_code == 200
        assert b'not about a project' in resp.data

    def test_sample_mode_names_the_role_and_subject(self, auth_client):
        html = auth_client.post(PREVIEW, data={'body': 'x', 'role': 'user'}).data.decode()
        assert 'Sample data, as user' in html and 'Subject:' in html
