"""Purge permits and hard deletes for the LDAP sync API (``sam.manage.purge``)."""

import pytest

from factories import (
    make_adhoc_group,
    make_adhoc_group_tag,
    make_adhoc_system_account_entry,
    make_email_address,
    make_institution,
    make_organization,
    make_phone,
    make_project,
    make_user,
    make_user_institution,
    make_user_organization,
)
from sam.core.groups import AdhocGroup
from sam.core.organizations import Institution, Organization
from sam.core.users import EmailAddress, User
from sam.manage import purge
from sam.manage.ldapsync import SyncValidationError


class TestUserPermit:

    def test_unknown_uid_is_purgeable_not_a_500(self, session):
        """Legacy threw on a null username here, and the daemon lost the delete."""
        permit = purge.user_permit(session, unix_uid=987_654_321)
        assert permit['purgeable'] is True
        assert 'does not exist in SAM' in permit['message']

    def test_active_user_is_blocked_with_legacy_text(self, session):
        user = make_user(session, active=True)
        permit = purge.user_permit(session, unix_uid=user.unix_uid)
        assert permit == {'username': user.username, 'purgeable': False,
                          'message': f'SAM user {user.username} is active.'}

    def test_project_lead_is_blocked(self, session):
        project = make_project(session)
        lead = project.lead
        lead.active = False
        session.flush()
        message = purge.user_permit(session, username=lead.username)['message']
        assert f'SAM user {lead.username} is project lead.' in message.split('\n')

    def test_lever_off_refuses_an_existing_user(self, session):
        user = make_user(session, active=False)
        permit = purge.user_permit(session, unix_uid=user.unix_uid, enabled=False)
        assert permit['purgeable'] is False
        assert permit['message'] == purge.PURGE_DISABLED

    def test_lever_off_still_answers_unknown_as_purgeable(self, session):
        """Otherwise the daemon would PUT a tombstone, creating a row SAM never had."""
        assert purge.user_permit(session, unix_uid=987_654_321, enabled=False)['purgeable']


class TestUserPurge:

    def test_purges_an_unreferenced_user_and_its_owned_rows(self, session):
        user = make_user(session, active=False)
        make_email_address(session, user)
        make_phone(session, user)
        make_user_institution(session, user=user)
        make_user_organization(session, user=user)
        uid = user.user_id
        purge.purge_user(session, unix_uid=user.unix_uid)
        assert session.get(User, uid) is None
        assert session.query(EmailAddress).filter_by(user_id=uid).count() == 0

    def test_blocked_purge_is_a_400(self, session):
        user = make_user(session, active=True)
        with pytest.raises(SyncValidationError, match='is active.'):
            purge.purge_user(session, username=user.username)

    def test_unknown_user_is_a_noop(self, session):
        purge.purge_user(session, unix_uid=987_654_321)

    def test_lever_off_refuses(self, session):
        user = make_user(session, active=False)
        with pytest.raises(SyncValidationError, match='Purge disabled'):
            purge.purge_user(session, unix_uid=user.unix_uid, enabled=False)

    def test_needs_an_identifier(self, session):
        with pytest.raises(SyncValidationError, match='Either unixUid, upid, or username'):
            purge.purge_user(session)


class TestInstitutionAndOrganization:

    def test_institution_with_users_is_blocked(self, session):
        inst = make_institution(session)
        make_user_institution(session, user=make_user(session), institution=inst)
        permit = purge.institution_permit(session, inst.institution_id)
        assert permit['purgeable'] is False
        assert permit['message'] == f'SAM institution id {inst.institution_id} has associated users.'

    def test_free_institution_is_purged(self, session):
        inst = make_institution(session)
        purge.purge_institution(session, inst.institution_id)
        assert session.get(Institution, inst.institution_id) is None

    def test_unknown_institution_is_purgeable(self, session):
        assert purge.institution_permit(session, 987_654_321)['purgeable'] is True

    def test_organization_with_children_is_blocked(self, session):
        """Not checked by legacy; a delete would NULL the children's parent link."""
        parent = make_organization(session)
        make_organization(session, parent_org_id=parent.organization_id)
        message = purge.organization_permit(session, parent.organization_id)['message']
        assert 'has child organizations.' in message

    def test_free_organization_is_purged(self, session):
        org = make_organization(session)
        purge.purge_organization(session, org.organization_id)
        assert session.get(Organization, org.organization_id) is None


class TestGroup:

    def test_project_gid_is_not_purgeable(self, session):
        project = make_project(session)
        project.unix_gid = 91_234_567
        session.flush()
        permit = purge.group_permit(session, 91_234_567)
        assert permit == {'unix_gid': 91_234_567, 'purgeable': False,
                          'message': 'unix gid 91234567 belongs to a project group.'}

    def test_purge_by_gid_takes_tags_and_entries(self, session):
        group = make_adhoc_group(session)
        make_adhoc_group_tag(session, group, 'hpc')
        make_adhoc_system_account_entry(session, group, 'someone')
        gid = group.unix_gid
        purge.purge_group(session, gid=gid)
        assert AdhocGroup.get_by_unix_gid(session, gid) is None

    def test_purge_by_name_is_case_insensitive(self, session):
        group = make_adhoc_group(session)
        purge.purge_group(session, name=group.group_name.upper())
        assert AdhocGroup.get_by_unix_gid(session, group.unix_gid) is None

    def test_lever_off_refuses_an_existing_group(self, session):
        group = make_adhoc_group(session)
        assert purge.group_permit(session, group.unix_gid, enabled=False)['purgeable'] is False
        with pytest.raises(SyncValidationError, match='Purge disabled'):
            purge.purge_group(session, gid=group.unix_gid, enabled=False)
