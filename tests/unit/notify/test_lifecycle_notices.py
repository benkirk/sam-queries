"""`sam.queries.lifecycle_notices` — the project lifecycle notice builders:
the Renew/Extend fan-out (#581) and the manual Notify classifier."""

from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource

from sam.core.users import EmailAddress
from sam.notify.models import NotificationLog
from sam.queries.lifecycle_notices import (
    build_lifecycle_messages, build_renewal_messages, classify_tree_for_notice,
    renewal_dedup_key,
)


def _with_email(session, user, address):
    session.add(EmailAddress(user_id=user.user_id, email_address=address,
                             is_primary=True, active=True))
    session.flush()
    session.refresh(user)
    return user


@pytest.fixture
def tree(session):
    """A two-level tree: root (lead+admin) with one leaf child (lead)."""
    root_lead = _with_email(session, make_user(session), 'rootlead@example.edu')
    root_admin = _with_email(session, make_user(session), 'rootadmin@example.edu')
    child_lead = _with_email(session, make_user(session), 'childlead@example.edu')

    # Factory-generated projcodes (unique per xdist worker); tests read them
    # back from the returned objects rather than hardcoding.
    root = make_project(session, lead=root_lead)
    root.project_admin_user_id = root_admin.user_id
    session.flush()
    child = make_project(session, parent=root, lead=child_lead)

    end = datetime.now() + timedelta(days=365)
    root_acct = make_account(session, project=root, resource=make_resource(session))
    child_acct = make_account(session, project=child, resource=make_resource(session))
    root_alloc = make_allocation(session, account=root_acct, amount=50_000.0,
                                 end_date=end)
    child_alloc = make_allocation(session, account=child_acct, amount=10_000.0,
                                  end_date=end)
    session.expire(root)
    session.expire(child)
    return {'root': root, 'child': child, 'end': end,
            'root_code': root.projcode, 'child_code': child.projcode,
            'touched': [root_alloc, child_alloc]}


def _url(pc):
    return f'https://sam.hpc.ucar.edu/admin/project/{pc}/edit?tab=allocations'


def _build(session, tree, action='renewed'):
    return build_renewal_messages(
        session, tree['root'], action=action, new_end=tree['end'],
        touched_allocations=tree['touched'], requested_by='pytest',
        url_builder=_url)


class TestFanOut:

    def test_one_message_per_project_lead_and_admin(self, session, tree):
        messages = _build(session, tree)
        by_addr = {m.recipient.address: m for m in messages}
        # root: lead + admin; leaf child: lead only.
        assert set(by_addr) == {'rootlead@example.edu', 'rootadmin@example.edu',
                                'childlead@example.edu'}
        assert all(m.kind == 'project_renewal' for m in messages)

    def test_subtree_flag_is_per_project(self, session, tree):
        by_code = {}
        for m in _build(session, tree):
            by_code.setdefault(m.projcode, m)
        assert by_code[tree['root_code']].context['has_subtree'] is True
        assert by_code[tree['child_code']].context['has_subtree'] is False

    def test_resources_are_grouped_to_their_own_project(self, session, tree):
        by_code = {m.projcode: m for m in _build(session, tree)}
        root_res = by_code[tree['root_code']].context['resources']
        child_res = by_code[tree['child_code']].context['resources']
        assert [r['amount'] for r in root_res] == ['50,000']
        assert [r['amount'] for r in child_res] == ['10,000']

    def test_manage_url_and_subject_are_personalized(self, session, tree):
        by_code = {m.projcode: m for m in _build(session, tree)}
        kid = tree['child_code']
        assert by_code[kid].context['manage_url'] == _url(kid)
        assert kid in by_code[kid].subject
        assert 'renewed' in by_code[kid].subject


class TestDedupKey:

    def test_keys_are_distinct_per_project_and_address(self, session, tree):
        keys = [m.dedup_key for m in _build(session, tree)]
        assert len(keys) == len(set(keys))

    def test_action_and_period_change_the_key(self):
        end = datetime(2027, 9, 30)
        a = renewal_dedup_key('renewed', 'P1', end, 'x@y.edu')
        assert a != renewal_dedup_key('extended', 'P1', end, 'x@y.edu')
        assert a != renewal_dedup_key('renewed', 'P1', datetime(2028, 9, 30),
                                      'x@y.edu')
        assert a == renewal_dedup_key('renewed', 'P1', end, 'x@y.edu')


class TestEmptyStates:

    def test_a_project_with_no_addressable_lead_is_skipped(self, session):
        # No email on the lead -> get_xras_pending_recipients drops them.
        root = make_project(session, lead=make_user(session))
        messages = build_renewal_messages(
            session, root, action='renewed', new_end=None,
            touched_allocations=[], requested_by='pytest', url_builder=_url)
        assert messages == []


def _seed_notice(session, projcode, when, kind='project_activation'):
    NotificationLog.create(
        session, kind=kind, channel='email', transport='smtp', status='sent',
        recipient='seed@example.edu', requested_by='pytest', projcode=projcode,
        when=when)
    session.flush()


@pytest.fixture
def single(session):
    """One project with a lead-on-file and one live allocation."""
    lead = _with_email(session, make_user(session), 'lead@example.edu')
    proj = make_project(session, lead=lead)
    acct = make_account(session, project=proj, resource=make_resource(session))
    alloc = make_allocation(session, account=acct, amount=1_000.0)
    session.flush()
    return {'project': proj, 'alloc': alloc}


class TestClassifier:

    def test_never_notified_defaults_to_activated(self, session, single):
        (row,) = classify_tree_for_notice(
            session, single['project'], active_at=datetime.now())
        assert row.auto_action == 'activated'

    def test_notified_then_changed_is_adjusted(self, session, single):
        _seed_notice(session, single['project'].projcode,
                     single['alloc'].creation_time - timedelta(hours=1))
        (row,) = classify_tree_for_notice(
            session, single['project'], active_at=datetime.now())
        assert row.auto_action == 'adjusted'

    def test_notified_and_unchanged_is_skip(self, session, single):
        _seed_notice(session, single['project'].projcode,
                     single['alloc'].creation_time + timedelta(hours=1))
        (row,) = classify_tree_for_notice(
            session, single['project'], active_at=datetime.now())
        assert row.auto_action == 'skip'

    def test_no_live_allocations_yields_no_row(self, session):
        lead = _with_email(session, make_user(session), 'l@example.edu')
        proj = make_project(session, lead=lead)   # no allocations
        assert classify_tree_for_notice(
            session, proj, active_at=datetime.now()) == []

    def test_item_for_maps_action_to_kind(self, session, single):
        (row,) = classify_tree_for_notice(
            session, single['project'], active_at=datetime.now())
        assert row.item_for('activated')['kind'] == 'project_activation'
        assert row.item_for('adjusted')['kind'] == 'project_adjustment'
        assert row.item_for('skip') is None


class TestManualBuild:

    def test_activation_messages_carry_the_right_kind_and_subject(
            self, session, single):
        (row,) = classify_tree_for_notice(
            session, single['project'], active_at=datetime.now())
        messages = build_lifecycle_messages(
            session, per_project=[row.item_for('activated')],
            requested_by='pytest', url_builder=_url)
        assert messages and all(m.kind == 'project_activation' for m in messages)
        assert 'is now active' in messages[0].subject
        assert messages[0].dedup_key.startswith('project_activation:')


class TestRealCallerSeam:
    """The builder fed by what Extend really reports, not a hand-built list."""

    @pytest.fixture
    def shared(self, session):
        """Root and child both hold the same resource; a bystander child holds
        none, so Extend never touches it."""
        root = make_project(
            session, lead=_with_email(session, make_user(session), 'r@example.edu'))
        child = make_project(
            session, parent=root,
            lead=_with_email(session, make_user(session), 'c@example.edu'))
        bystander = make_project(
            session, parent=root,
            lead=_with_email(session, make_user(session), 'b@example.edu'))
        resource = make_resource(session)
        start, end = datetime(2026, 10, 1), datetime(2027, 9, 30)
        for proj in (root, child):
            make_allocation(
                session, account=make_account(session, project=proj, resource=resource),
                amount=1_000.0, start_date=start, end_date=end)
        for proj in (root, child, bystander):
            session.expire(proj)
        return {'root': root, 'child': child, 'bystander': bystander,
                'resource': resource}

    def _extend(self, session, shared):
        from sam.manage.extend import extend_project_allocations
        touched = []
        roots = extend_project_allocations(
            session, root_project_id=shared['root'].project_id,
            source_active_at=datetime(2027, 1, 1), new_end=datetime(2028, 9, 30),
            resource_ids=[shared['resource'].resource_id], user_id=1,
            touched=touched)
        return roots, touched

    def test_touched_reports_descendants_the_return_value_omits(self, session, shared):
        roots, touched = self._extend(session, shared)
        assert len(roots) == 1
        assert len(touched) == 2

    def test_child_mail_lists_its_resources_and_bystander_gets_none(self, session, shared):
        _, touched = self._extend(session, shared)
        messages = build_renewal_messages(
            session, shared['root'], action='extended',
            new_end=datetime(2028, 9, 30), touched_allocations=touched,
            requested_by='pytest', url_builder=_url)
        by_code = {m.projcode: m for m in messages}
        assert set(by_code) == {shared['root'].projcode, shared['child'].projcode}
        assert by_code[shared['child'].projcode].context['resources']


class TestFollowups:

    def test_a_renewal_notice_counts_as_contacted(self, session, single):
        _seed_notice(session, single['project'].projcode,
                     single['alloc'].creation_time + timedelta(hours=1),
                     kind='project_renewal')
        (row,) = classify_tree_for_notice(
            session, single['project'], active_at=datetime.now())
        assert row.auto_action == 'skip'

    def test_two_same_day_adjustments_have_distinct_keys(self):
        from sam.queries.lifecycle_notices import lifecycle_dedup_key
        morning, later = datetime(2026, 9, 20, 9, 5), datetime(2026, 9, 20, 14, 30)
        assert (lifecycle_dedup_key('project_adjustment', 'P1', 'adjusted', morning, 'x@y.edu')
                != lifecycle_dedup_key('project_adjustment', 'P1', 'adjusted', later, 'x@y.edu'))

    def test_operator_comment_reaches_every_copy_and_blank_is_none(self, session, tree):
        kw = dict(action='renewed', new_end=tree['end'],
                  touched_allocations=tree['touched'], requested_by='pytest',
                  url_builder=_url)
        noted = build_renewal_messages(session, tree['root'],
                                       operator_comment=' See you at the workshop. ', **kw)
        assert {m.context['operator_comment'] for m in noted} == {'See you at the workshop.'}
        blank = build_renewal_messages(session, tree['root'], operator_comment='  ', **kw)
        assert {m.context['operator_comment'] for m in blank} == {None}


class TestLinks:

    def _messages(self, session, tree, **kw):
        return build_renewal_messages(
            session, tree['root'], action='renewed', new_end=tree['end'],
            touched_allocations=tree['touched'], requested_by='pytest',
            url_builder=_url, **kw)

    def test_resource_rows_link_to_their_own_project_page(self, session, tree):
        msg = next(m for m in self._messages(session, tree, site_url='http://dev:5050/')
                   if m.projcode == tree['child_code'])
        (row,) = msg.context['resources']
        assert row['details_url'].startswith(
            f"http://dev:5050/user/resource-details/{tree['child_code']}?resource=")
        assert ' ' not in row['details_url']

    def test_landing_links_default_to_production(self, session, tree):
        links = self._messages(session, tree)[0].context['links']
        assert links['jobs'] == 'https://sam.hpc.ucar.edu/user/jobs'
        assert set(links) == {'accounts', 'jobs', 'data', 'status'}
