"""The Directory panels: the Configuration block, the user-card section and panel, the
organization and institution dialogs, the project and adhoc group panels. Auth, render smoke
and the fail-open shapes; the write path is covered at the manage layer
(tests/unit/manage/test_directory_reconcile.py)."""
import json
import os
import re
from datetime import datetime

import pytest

from factories import make_project, make_user
from factories.directory import (FakeDirectory, make_group_record, make_person_record,
                                 make_position_record)
from sam.core.groups import AdhocGroup
from sam.core.users import User
from sam.integration.directory import (DirectoryUnavailable, EmailRecord, InstitutionRecord,
                                       OrganizationRecord, outage)
from sam.integration.directory.compare import DriftReport
from sam.integration.directory.config import PERSON_KINDS
from sam.manage.directory import Change, ReconcileResult

CARD = '/admin/user/benkirk'
PANEL = '/admin/htmx/user/benkirk/directory'
APPLY = '/admin/htmx/user/benkirk/directory/apply'
GROUP_CARD = '/admin/group/csg'
GROUP_PANEL = '/admin/htmx/group/csg/directory'


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

    def test_shown_when_configured_and_reads_nothing(self, auth_client, configured, benkirk):
        html = auth_client.get(CARD).get_data(as_text=True)
        assert 'Check directory' in html and f'userDirectoryPanel-{benkirk.user_id}' in html
        assert configured.calls == []


class TestProjectPanel:
    """Routes read committed rows, so the HTTP tests seed the fake against ``active_project``."""

    @pytest.fixture
    def url(self, active_project):
        return f'/admin/htmx/project/{active_project.projcode}/directory'

    def test_card_section_hidden_without_a_directory(self, auth_client, active_project):
        html = auth_client.get(f'/admin/project/{active_project.projcode}').get_data(as_text=True)
        assert 'Check directory' not in html

    def test_card_section_reads_nothing(self, auth_client, configured, active_project):
        html = auth_client.get(f'/admin/project/{active_project.projcode}').get_data(as_text=True)
        assert 'Check directory' in html
        assert f'projectDirectoryPanel-{active_project.project_id}' in html
        assert configured.calls == []

    def test_non_admin_denied(self, non_admin_client, url):
        assert non_admin_client.get(url).status_code == 403

    def test_unknown_project(self, auth_client, configured):
        assert auth_client.get('/admin/htmx/project/NOPE9999/directory').status_code == 404

    def test_unconfigured_is_a_quiet_alert(self, auth_client, url):
        resp = auth_client.get(url)
        assert resp.status_code == 200 and b'No directory is configured' in resp.data

    def test_the_panel_is_report_only_with_managers_marked(self, auth_client, configured,
                                                           active_project, session, url):
        from sam.queries.ldapsync import project_group
        row = project_group(session, active_project)
        lead = active_project.lead
        configured.add(make_group_record(
            gid=active_project.unix_gid, name=active_project.projcode,
            tags=tuple(row['tags']), member_upids=tuple(row['upids']) + (999_999_999,),
            managers=tuple(row['managers'])))
        html = auth_client.get(url).get_data(as_text=True)
        assert configured.calls == [('group', active_project.unix_gid)]
        assert f'gid {active_project.unix_gid}' in html
        assert 'Report only: project groups flow from SAM to the directory' in html
        assert f'>{lead.username}<' in html and '>lead</span>' in html
        assert 'member_missing_in_sam' in html and 'upid 999999999' in html
        assert 'manager_missing' not in html and 'would apply' not in html
        assert 'hx-post' not in html and 'Levers on' not in html

    def test_absent_names_the_upstream_cause_for_a_project_created_after_it(
            self, auth_client, configured, active_project, session, app, url):
        from sam.manage.directory_panel import CREATES_STOPPED, project_group_panel
        html = auth_client.get(url).get_data(as_text=True)
        assert f'no group for gid {active_project.unix_gid}' in html
        expected = active_project.creation_time.date() >= CREATES_STOPPED
        assert ('2026-07-10' in html) is expected
        recent = make_project(session, lead=make_user(session, upid=True))
        recent.unix_gid, recent.creation_time = 77_777_778, datetime(2026, 7, 10)
        older = make_project(session, lead=recent.lead)
        older.unix_gid, older.creation_time = 77_777_780, datetime(2026, 7, 9, 23, 59, 59)
        session.flush()
        with app.app_context():
            assert project_group_panel(session, recent)['absent_note'].startswith('No project group')
            assert project_group_panel(session, older)['absent_note'] == ''

    def test_unavailable_is_an_alert_not_a_500(self, auth_client, configured, url):
        configured.raise_with = DirectoryUnavailable('timeout')
        resp = auth_client.get(url)
        assert resp.status_code == 200 and b'could not be read' in resp.data
        outage.reset()

    def test_a_project_without_a_gid_has_nothing_to_look_up(self, session, configured, app):
        from sam.manage.directory_panel import project_group_panel
        project = make_project(session, lead=make_user(session, upid=True))
        with app.app_context():
            panel = project_group_panel(session, project)
        assert (panel['available'], panel['error'], panel['managers']) == (False, 'no gid', [])
        assert configured.calls == []

    def test_managers_absent_from_sam_are_drift(self, session, configured, app):
        from sam.manage.directory_panel import project_group_panel
        project = make_project(session, lead=make_user(session, upid=True))
        project.unix_gid = 77_777_777
        session.flush()
        record = make_group_record(gid=77_777_777, name=project.projcode, member_upids=(project.lead.upid,),
                                   managers=(project.lead.upid, 999_999_998))
        with app.app_context():
            panel = project_group_panel(session, project, record=record)
        assert [(r['category'], r['directory'], r['disposition']) for r in panel['rows']] == [
            ('manager_missing_in_sam', 'upid 999999998', 'withheld')]
        assert [(m['username'], m['role']) for m in panel['managers']] == [
            (project.lead.username, 'lead'), ('', '')]


    def test_the_project_view_is_a_handful_of_statements(self, session, configured, app):
        """Sweep 29: loading the lead and admin User objects cost 11 statements (their selectin
        collections) to read two upids; one id-to-upid query reads them. Pinned."""
        from sqlalchemy import event
        from sam.manage.directory_panel import project_group_panel
        lead = make_user(session, upid=True)
        project = make_project(session, lead=lead)
        project.unix_gid = 77_777_779
        session.flush()
        record = make_group_record(gid=project.unix_gid, name=project.projcode,
                                   member_upids=(lead.upid,), managers=(lead.upid,))
        seen = []
        listen = lambda conn, cursor, statement, *a: seen.append(statement)
        engine = session.get_bind()
        event.listen(engine, 'before_cursor_execute', listen)
        try:
            with app.app_context():
                panel = project_group_panel(session, project, record=record)
        finally:
            event.remove(engine, 'before_cursor_execute', listen)
        assert panel['managers'][0]['role'] == 'lead'
        assert len(seen) <= 8, seen


class TestGroupPanel:
    @pytest.fixture
    def csg(self, session):
        return AdhocGroup.get_by_name(session, 'csg')

    def test_card_section(self, auth_client, configured, csg):
        html = auth_client.get(GROUP_CARD).get_data(as_text=True)
        assert 'Check directory' in html and f'groupDirectoryPanel-{csg.group_id}' in html
        assert configured.calls == []

    def test_card_section_hidden_without_a_directory(self, auth_client):
        assert 'Check directory' not in auth_client.get(GROUP_CARD).get_data(as_text=True)

    def test_non_admin_denied(self, non_admin_client):
        assert non_admin_client.get(GROUP_PANEL).status_code == 403

    def test_unknown_group(self, auth_client, configured):
        resp = auth_client.get('/admin/htmx/group/no-such-group-xyz/directory')
        assert resp.status_code == 404 and b'Group not found' in resp.data

    def test_managers_are_shown_not_compared(self, auth_client, configured, csg, benkirk):
        configured.add(make_group_record(gid=csg.unix_gid, name='csg', tags=('hpc',),
                                         member_upids=(benkirk.upid,), managers=(benkirk.upid,)))
        html = auth_client.get(GROUP_PANEL).get_data(as_text=True)
        assert configured.calls == [('group', csg.unix_gid)]
        assert f'>{benkirk.username}<' in html and '>lead</span>' not in html
        assert 'manager_missing' not in html
        assert 'Report only: adhoc groups are authored in Google Groups' in html
        assert 'through the sync daemon' not in html

    def test_absent_has_no_project_note(self, auth_client, configured, csg):
        html = auth_client.get(GROUP_PANEL).get_data(as_text=True)
        assert f'no group for gid {csg.unix_gid}' in html and '2026-07-10' not in html


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
        assert 'Automatic apply on sync: none' in html
        assert 'Sync from directory' in html             # no lever needed for the operator's Sync

    def test_drift_rows_carry_both_values(self, auth_client, configured, benkirk):
        _seed(configured, benkirk, first_name='Someone Else', positions=())
        html = auth_client.get(PANEL).get_data(as_text=True)
        assert 'first_name' in html and 'Someone Else' in html and 'would apply' in html

    def test_sync_needs_no_lever(self, auth_client, configured, benkirk):
        _seed(configured, benkirk, first_name='Someone Else', positions=())
        html = auth_client.get(PANEL).get_data(as_text=True)
        assert 'would apply' in html and 'hx-post="' + APPLY in html
        assert 'Sync writes' in html and "benkirk&#39;s SAM record" in html

    def test_identity_rows_stay_withheld_under_sync(self, auth_client, configured, benkirk):
        _seed(configured, benkirk, unix_uid=(benkirk.unix_uid or 0) + 1, positions=())
        html = auth_client.get(PANEL).get_data(as_text=True)
        assert 'unix_uid' in html and '>withheld</span>' in html

    def test_extra_emails_name_the_address_and_are_withheld_when_the_record_has_none(
            self, auth_client, configured, benkirk):
        _seed(configured, benkirk, positions=())         # emails=() in the record
        html = auth_client.get(PANEL).get_data(as_text=True)
        assert '<td class="col-shrink">extra</td>' in html
        assert benkirk.primary_email in html

    def test_not_in_the_directory(self, auth_client, configured, benkirk):
        resp = auth_client.get(PANEL)
        assert resp.status_code == 200 and b'is not in the directory' in resp.data

    def test_unavailable_is_an_alert_not_a_500(self, auth_client, configured):
        configured.raise_with = DirectoryUnavailable('timeout')
        resp = auth_client.get(PANEL)
        assert resp.status_code == 200 and b'could not be read' in resp.data
        assert b'DirectoryUnavailable' in resp.data
        outage.reset()


class TestDirectoryGates:
    """Reading is VIEW_DIRECTORY, Apply is MANAGE_DIRECTORY; VIEW_USERS and EDIT_USERS confer
    neither."""

    _CARD = {'VIEW_USERS', 'EDIT_USERS', 'ACCESS_ADMIN_DASHBOARD'}

    def _as(self, monkeypatch, client, *extra):
        from webapp.extensions import db
        from webapp.utils import rbac
        from webapp.utils.rbac import Permission
        with client.session_transaction() as sess:
            uid = int(sess['_user_id'])
        with client.application.app_context():
            username = db.session.get(User, uid).username
        monkeypatch.setitem(rbac.USER_PERMISSION_OVERRIDES, username,
                            {Permission[p] for p in self._CARD | set(extra)})

    def test_user_edit_alone_neither_reads_nor_applies(self, non_admin_client, configured, benkirk,
                                                       monkeypatch, any_organization):
        self._as(monkeypatch, non_admin_client)
        assert 'Check directory' not in non_admin_client.get(CARD).get_data(as_text=True)
        assert non_admin_client.get(PANEL).status_code == 403
        assert non_admin_client.post(APPLY).status_code == 403
        oid = any_organization.organization_id
        assert non_admin_client.get(f'/admin/htmx/organization/{oid}/directory').status_code == 403
        assert non_admin_client.get(GROUP_PANEL).status_code == 403
        assert configured.calls == []

    def test_view_directory_reads_but_never_applies(self, non_admin_client, configured, benkirk,
                                                    monkeypatch):
        self._as(monkeypatch, non_admin_client, 'VIEW_DIRECTORY')
        monkeypatch.setenv('DIRECTORY_APPLY_PERSON', '1')
        _seed(configured, benkirk, first_name='Someone Else', positions=())
        assert 'Check directory' in non_admin_client.get(CARD).get_data(as_text=True)
        html = non_admin_client.get(PANEL).get_data(as_text=True)
        assert 'Someone Else' in html and 'hx-post="' + APPLY not in html
        assert 'Sync from directory' not in html
        assert non_admin_client.post(APPLY).status_code == 403


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

    def test_sync_runs_under_the_operator_config_and_logs_who(self, auth_client, configured,
                                                              benkirk, monkeypatch, caplog):
        import webapp.dashboards.admin.directory_routes as routes
        _seed(configured, benkirk, first_name='Someone Else', positions=())
        seen = []

        def fake_reconcile(session, user, record, *, config, **kw):
            seen.append(config.apply_kinds)
            return ReconcileResult('person', user.username,
                                   DriftReport('person', user.username, 'sam', True, ()),
                                   applied=(Change('person', 'name', 'first_name'),))
        monkeypatch.setattr(routes, 'reconcile_person', fake_reconcile)
        caplog.set_level('INFO', logger='sam.manage.directory')
        assert auth_client.post(APPLY).status_code == 200
        assert seen == [PERSON_KINDS]                    # no lever is set in the suite
        assert any('directory apply person benkirk' in r.getMessage()
                   and r.getMessage().endswith('by=benkirk') for r in caplog.records)

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
