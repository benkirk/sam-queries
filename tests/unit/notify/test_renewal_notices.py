"""`sam.queries.renewal_notices` — the manual renew/extend fan-out builder."""

from datetime import datetime, timedelta

import pytest
from factories.core import make_user
from factories.projects import make_account, make_allocation, make_project
from factories.resources import make_resource

from sam.core.users import EmailAddress
from sam.queries.renewal_notices import build_renewal_messages, renewal_dedup_key


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
