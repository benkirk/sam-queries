"""The Directory panels: the Configuration block, the user-card section and panel, the
organization dialog. Auth, render smoke and the fail-open shapes; the write path is covered
at the manage layer (tests/unit/manage/test_directory_reconcile.py)."""
import json
import os
import re

import pytest

from factories.directory import FakeDirectory, make_person_record, make_position_record
from sam.core.users import User
from sam.integration.directory import (DirectoryUnavailable, InstitutionRecord,
                                       OrganizationRecord, outage)
from sam.integration.directory.compare import DriftReport
from sam.manage.directory import Change, ReconcileResult

CARD = '/admin/user/benkirk'
PANEL = '/admin/htmx/user/benkirk/directory'
APPLY = '/admin/htmx/user/benkirk/directory/apply'


@pytest.fixture
def configured(app, monkeypatch):
    """A configured directory served by a fake reader; nothing dials out."""
    import sam.integration.directory.registry as registry
    monkeypatch.setitem(app.config, 'DIRECTORY_PROVIDER', 'fake')
    monkeypatch.setitem(app.config, 'DIRECTORY_URL', 'ldap://ldap.example.invalid')
    fake = FakeDirectory()
    monkeypatch.setattr(registry, 'reader_from_environment', lambda: fake)
    return fake


@pytest.fixture
def benkirk(session):
    return User.get_by_username(session, 'benkirk')


def _seed(fake, user, organization_id=375, **overrides):
    values = dict(upid=user.upid, username=user.username, unix_uid=user.unix_uid,
                  first_name='Benjamin', emails=(), phones=(),
                  positions=(make_position_record('P001', organization_id=organization_id),))
    values.update(overrides)
    return fake.add(make_person_record(**values))


class TestConfigurationBlock:
    def test_the_block_is_off_under_the_suite_pins(self, app):
        from webapp.extensions import db
        from webapp.utils.config_inspect import gather_runtime_state
        with app.app_context():
            block = gather_runtime_state(app, db)['directory']
        assert (block['provider'], block['configured'], block['mode'], block['latched'],
                block['misconfigured']) == ('', False, 'off', False, False)
        assert set(block['apply']) == {'person', 'position', 'collaboration', 'organization',
                                       'institution', 'group'}

    def test_modes(self, app, monkeypatch):
        from webapp.utils.config_inspect import _directory_block
        monkeypatch.setitem(app.config, 'DIRECTORY_PROVIDER', 'ucar')
        with app.app_context():
            assert _directory_block()['misconfigured'] is True
            monkeypatch.setitem(app.config, 'DIRECTORY_URL', 'ldap://ldap.example.invalid')
            assert _directory_block()['mode'] == 'configured, panels only'
            monkeypatch.setitem(app.config, 'DIRECTORY_CHECK_ON_SYNC', True)
            assert _directory_block()['mode'] == 'report on sync'
            monkeypatch.setenv('DIRECTORY_APPLY_PERSON', '1')
            assert _directory_block()['mode'] == 'apply: person'

    def test_the_card_renders_the_block(self, auth_client):
        html = auth_client.get('/admin/htmx/configuration').get_data(as_text=True)
        assert 'DIRECTORY_PROVIDER' in html
        assert re.search(r'<dd[^>]*>\s*off\s*</dd>', html)


class TestUserCardSection:
    def test_hidden_when_no_directory(self, auth_client):
        html = auth_client.get(CARD).get_data(as_text=True)
        assert 'Check directory' not in html

    def test_shown_when_configured_and_reads_nothing(self, auth_client, configured):
        html = auth_client.get(CARD).get_data(as_text=True)
        assert 'Check directory' in html and 'userDirectoryPanel' in html
        assert configured.calls == []


class TestUserPanel:
    def test_unauthenticated(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        assert client.get(PANEL).status_code in (302, 401)

    def test_non_admin_denied(self, non_admin_client):
        assert non_admin_client.get(PANEL).status_code == 403
        assert non_admin_client.post(APPLY).status_code == 403

    def test_unknown_user(self, auth_client, configured):
        resp = auth_client.get('/admin/htmx/user/no-such-user-xyz/directory')
        assert resp.status_code == 404 and b'User not found' in resp.data

    def test_unconfigured_is_a_quiet_alert(self, auth_client):
        resp = auth_client.get(PANEL)
        assert resp.status_code == 200 and b'No directory is configured' in resp.data

    def test_the_panel(self, auth_client, configured, benkirk, any_organization):
        _seed(configured, benkirk, organization_id=any_organization.organization_id)
        html = auth_client.get(PANEL).get_data(as_text=True)
        assert configured.calls == [('person', benkirk.upid)]
        assert f'uid {benkirk.unix_uid}' in html
        assert any_organization.acronym in html and 'Position</th>' in html
        assert '>would apply</span>' not in html         # no lever is on
        assert '>withheld</span>' in html and 'Levers on: none' in html
        assert 'hx-post' not in html                     # no Apply without a lever

    def test_drift_rows_carry_both_values(self, auth_client, configured, benkirk):
        _seed(configured, benkirk, first_name='Someone Else', positions=())
        html = auth_client.get(PANEL).get_data(as_text=True)
        assert 'first_name' in html and 'Someone Else' in html and 'withheld' in html

    def test_a_lever_shows_the_apply_button(self, auth_client, configured, benkirk, monkeypatch):
        monkeypatch.setenv('DIRECTORY_APPLY_PERSON', '1')
        _seed(configured, benkirk, first_name='Someone Else', positions=())
        html = auth_client.get(PANEL).get_data(as_text=True)
        assert 'would apply' in html and 'hx-post="' + APPLY in html

    def test_not_in_the_directory(self, auth_client, configured, benkirk):
        resp = auth_client.get(PANEL)
        assert resp.status_code == 200 and b'is not in the directory' in resp.data

    def test_unavailable_is_an_alert_not_a_500(self, auth_client, configured):
        configured.raise_with = DirectoryUnavailable('timeout')
        resp = auth_client.get(PANEL)
        assert resp.status_code == 200 and b'could not be read' in resp.data
        assert b'DirectoryUnavailable' in resp.data
        outage.reset()


class TestUserApply:
    def test_apply_runs_the_reconcile_once_and_shows_the_banner(self, auth_client, configured,
                                                                benkirk, monkeypatch):
        import webapp.dashboards.admin.directory_routes as routes
        _seed(configured, benkirk, first_name='Someone Else', positions=())
        seen = []

        def fake_reconcile(session, user, record, *, config, **kw):
            seen.append((user.username, record.upid, kw.get('dry_run', False)))
            return ReconcileResult('person', user.username,
                                   DriftReport('person', user.username, 'sam', True, ()),
                                   applied=(Change('person', 'name', 'first_name'),))
        monkeypatch.setattr(routes, 'reconcile_person', fake_reconcile)
        resp = auth_client.post(APPLY)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert seen == [('benkirk', benkirk.upid, False)]
        assert 'Applied 1, withheld 0' in html and 'Refresh card' in html
        assert configured.calls == [('person', benkirk.upid)]

    def test_apply_with_the_directory_down_is_the_alert(self, auth_client, configured):
        configured.raise_with = DirectoryUnavailable('timeout')
        resp = auth_client.post(APPLY)
        assert resp.status_code == 200 and b'could not be read' in resp.data
        outage.reset()


class TestOrganizationDialog:
    def test_the_row_action_appears_only_with_a_directory(self, auth_client, configured):
        html = auth_client.get('/admin/htmx/organizations-card?fresh=1').get_data(as_text=True)
        assert 'orgDirectoryModal' in html

    def test_non_admin_denied(self, non_admin_client, any_organization):
        oid = any_organization.organization_id
        assert non_admin_client.get(f'/admin/htmx/organization/{oid}/directory').status_code == 403
        assert non_admin_client.post(f'/admin/htmx/organization/{oid}/directory/apply').status_code == 403

    def test_unknown_organization(self, auth_client, configured):
        resp = auth_client.get('/admin/htmx/organization/999999999/directory')
        assert resp.status_code == 404 and b'Organization not found' in resp.data

    def test_the_dialog(self, auth_client, configured, any_organization):
        org = any_organization
        configured.add(OrganizationRecord(org.organization_id, org.acronym, 'Renamed in the directory',
                                          '', '', None, True))
        html = auth_client.get(f'/admin/htmx/organization/{org.organization_id}/directory').get_data(as_text=True)
        assert 'orgDirectoryModalLabel' in html and 'Renamed in the directory' in html
        assert 'withheld' in html and 'hx-post' not in html

    def test_apply_reloads_the_card_when_something_applied(self, auth_client, configured,
                                                           any_organization, monkeypatch):
        import webapp.dashboards.admin.directory_routes as routes
        org = any_organization
        configured.add(OrganizationRecord(org.organization_id, org.acronym, 'Renamed', '', '', None, True))
        monkeypatch.setattr(routes, 'reconcile_organization', lambda session, o, record, *, config, **kw:
                            ReconcileResult('organization', str(o.organization_id),
                                            DriftReport('organization', '', 'sam', True, ()),
                                            applied=(Change('organization', 'name', 'name'),)))
        resp = auth_client.post(f'/admin/htmx/organization/{org.organization_id}/directory/apply')
        assert resp.status_code == 200
        assert json.loads(resp.headers['HX-Trigger']) == {'reloadOrganizationsCard': {}}
        assert b'Applied 1' in resp.data


class TestInstitutionDialog:
    def test_the_row_action_appears_only_with_a_directory(self, auth_client, configured):
        html = auth_client.get('/admin/htmx/institutions-fragment?fresh=1').get_data(as_text=True)
        assert 'instDirectoryModal' in html

    def test_non_admin_denied(self, non_admin_client, any_institution):
        iid = any_institution.institution_id
        assert non_admin_client.get(f'/admin/htmx/institution/{iid}/directory').status_code == 403
        assert non_admin_client.post(f'/admin/htmx/institution/{iid}/directory/apply').status_code == 403

    def test_unknown_institution(self, auth_client, configured):
        resp = auth_client.get('/admin/htmx/institution/999999999/directory')
        assert resp.status_code == 404 and b'Institution not found' in resp.data

    def test_the_dialog(self, auth_client, configured, any_institution):
        inst = any_institution
        configured.add(InstitutionRecord(inst.institution_id, inst.acronym, 'Renamed in the directory',
                                         country='US', state_code='AK', org_type='University'))
        html = auth_client.get(f'/admin/htmx/institution/{inst.institution_id}/directory').get_data(as_text=True)
        assert 'instDirectoryModalLabel' in html and 'Renamed in the directory' in html
        assert 'withheld' in html and 'hx-post' not in html

    def test_apply_reloads_the_card_when_something_applied(self, auth_client, configured,
                                                           any_institution, monkeypatch):
        import webapp.dashboards.admin.directory_routes as routes
        inst = any_institution
        configured.add(InstitutionRecord(inst.institution_id, inst.acronym, 'Renamed'))
        monkeypatch.setattr(routes, 'reconcile_institution', lambda session, i, record, *, config, **kw:
                            ReconcileResult('institution', str(i.institution_id),
                                            DriftReport('institution', '', 'sam', True, ()),
                                            applied=(Change('institution', 'name', 'name'),)))
        resp = auth_client.post(f'/admin/htmx/institution/{inst.institution_id}/directory/apply')
        assert resp.status_code == 200
        assert json.loads(resp.headers['HX-Trigger']) == {'reloadOrganizationsCard': {}}
        assert b'Applied 1' in resp.data
