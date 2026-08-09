"""Repeat posts, and the four wire shapes the corpus has never shown us.

Two different questions, deliberately answered at two different levels:

* **"Can an operator SEE a duplicate?"** is a question about the audit row, so it is
  asked through the route.
* **"What does a duplicate COST?"** is a question about the handler, so it is asked at
  ``dispatch_action`` with factories — the route cannot answer it, because each POST
  gets its own request session and ``committing_route`` deliberately stops any of it
  persisting.

Conflating the two would produce a test that quietly measures neither.
"""

import json

import pytest

from .conftest import post_action as _post

pytestmark = pytest.mark.stress


# ---------------------------------------------------------------------------
# Repeat posts — can the row see it?
# ---------------------------------------------------------------------------

def test_repeat_post_supplement(xras_client, action_log, dispatching, scenario,
                                committing_route, snapshot_project):
    """Three identical posts produce three rows an operator cannot tell apart."""
    import sam.xras.handlers  # noqa: F401

    payload = {'actionType': 'Supplement', 'actionId': 40001, 'requestId': 50001,
               'requestNumber': snapshot_project.projcode,
               'allocationType': 'Small', 'resources': [], 'roles': []}

    for _ in range(3):
        resp = _post(xras_client, payload)
        assert resp.status_code == scenario['http']

    rows = action_log.rows()
    assert len(rows) == 3
    assert {r['status'] for r in rows} == {scenario['expect']}

    # ⚠️ **This assertion is inverted from what it was.** It used to prove `actionId`
    # survived only as bytes inside `raw_payload`, so telling a duplicate from a
    # legitimate second award meant parsing JSON out of a TEXT column. That was the
    # evidence behind the `action_id` verdict; the column landed, so this now proves
    # the duplicate is detectable with a point lookup.
    assert all(r['action_type'] == 'Supplement' for r in rows)
    assert {r['action_id'] for r in rows} == {40001}, (
        'three posts of one action must share one action_id — that is what makes '
        'them a detectable duplicate rather than three indistinguishable rows')
    assert all(r['service'] == 'supplement' for r in rows)


def test_repeat_post_extension(xras_client, action_log, dispatching, scenario,
                               committing_route, snapshot_project):
    """The same question on the 60%-of-traffic path."""
    import sam.xras.handlers  # noqa: F401

    payload = {'actionType': 'Extension', 'actionId': 40002,
               'requestNumber': snapshot_project.projcode,
               'actionEndDate': '2031-12-31', 'resources': [], 'roles': []}

    for _ in range(2):
        assert _post(xras_client, payload).status_code == scenario['http']

    rows = action_log.rows()
    assert len(rows) == 2
    assert {r['status'] for r in rows} == {scenario['expect']}


class TestWhatADoublePostCosts:
    """The blast radius, at the handler level where it is observable.

    ⚠️ These write through ``dispatch_action`` on the **test's** session, so the
    per-test SAVEPOINT rolls them back — unlike the route, whose commits escape it.
    """

    @pytest.fixture
    def mapped(self, session):
        from factories import make_account, make_allocation, make_project, make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource

        resource = make_resource(session)
        key = 960_000 + resource.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=key, resource_id=resource.resource_id))
        project = make_project(session)
        allocation = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=resource))
        session.flush()
        session.refresh(project)
        return project, allocation, key

    def test_a_repeated_supplement_is_additive(self, session, mapped, monkeypatch):
        """250,000 posted three times leaves 750,000 added.

        Correct per-post behaviour and the wrong total — `awardedAmount` is the
        INCREMENT, not the new total, which is the most consequential porting semantic
        in the sprint. This is the number behind the `action_id` verdict.
        """
        from contextlib import contextmanager

        import sam.xras.handlers.base as base
        from sam.xras.dispatch import dispatch_action

        @contextmanager
        def flushing(sess):
            yield sess
            sess.flush()

        monkeypatch.setattr(base, 'management_transaction', flushing)
        project, allocation, key = mapped

        action = {'actionType': 'Supplement', 'requestNumber': project.projcode,
                  'allocationType': 'Small', 'roles': [],
                  'resources': [{'resourceRepositoryKey': key,
                                 'awardedAmount': '250000', 'comments': None}]}
        for _ in range(3):
            dispatch_action(session, action)

        assert allocation.amount == pytest.approx(1_750_000.0)

    def test_a_repeated_extension_is_near_idempotent(self, session, mapped,
                                                     monkeypatch):
        """The asymmetry that matters before an abrupt cutover.

        Extension is 60% of traffic and a double post costs **nothing** — the second
        finds every account already at the requested end and the equal-end-date skip
        writes nothing. Supplement is 15% and a double post costs a full increment.
        """
        from contextlib import contextmanager

        import sam.xras.handlers.base as base
        from sam.accounting.allocations import AllocationTransaction
        from sam.xras.dispatch import dispatch_action

        @contextmanager
        def flushing(sess):
            yield sess
            sess.flush()

        monkeypatch.setattr(base, 'management_transaction', flushing)
        project, allocation, _ = mapped

        action = {'actionType': 'Extension', 'requestNumber': project.projcode,
                  'actionEndDate': '2031-12-31', 'resources': [], 'roles': []}
        dispatch_action(session, action)
        after_first = len(session.query(AllocationTransaction)
                          .filter_by(allocation_id=allocation.allocation_id).all())

        dispatch_action(session, action)
        after_second = len(session.query(AllocationTransaction)
                           .filter_by(allocation_id=allocation.allocation_id).all())

        assert after_second == after_first, (
            'the second Extension wrote a row — the equal-end-date skip did not fire')


# ---------------------------------------------------------------------------
# Wire shapes the corpus has never shown us
# ---------------------------------------------------------------------------

def test_unsampled_renewal(xras_client, action_log, dispatching, scenario,
                           committing_route, snapshot_project):
    """``Renewal`` is declared on the wire and has never been sampled.

    It shares the Update path with New-against-an-existing-project. The claim worth
    making is not that it succeeds — a minimal payload legitimately fails validation —
    but that it **reaches a handler and produces a reviewable 422**, rather than a 500
    or a silent park. Legacy's answer to a bad Renewal is an opaque 500.
    """
    import sam.xras.handlers  # noqa: F401

    resp = _post(xras_client, {
        'actionType': 'Renewal', 'requestNumber': snapshot_project.projcode,
        'resources': [], 'roles': []})

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']

    # The reviewable part: real error strings from the Update assembly, not a stack
    # trace and not an empty park.
    errors = json.loads(resp.data)['result']['errors']
    assert 'Missing title' in errors
    assert row['error_messages'] and 'Missing title' in row['error_messages']


def test_unsampled_advance(xras_client, action_log, dispatching, scenario,
                           snapshot_project):
    """``Advance`` is declared on the wire and has **no** legacy service at all.

    So it falls off the end of the selector chain. Parking is correct; the row not
    saying why is the same gap the other three parks have.
    """
    import sam.xras.handlers  # noqa: F401

    resp = _post(xras_client, {
        'actionType': 'Advance', 'requestNumber': snapshot_project.projcode,
        'resources': [], 'roles': []})

    assert resp.status_code == scenario['http']
    row = action_log.one()
    assert row['status'] == scenario['expect']
    assert row['action_type'] == 'Advance'
    assert row['error_messages'] is None


@pytest.mark.parametrize('spelling', ['Co-PI', 'CoPi', 'Co-Investigator'])
def test_copi_role_spelling(session, spelling):
    """The spelling is still unknown, and it turns out not to matter.

    ⚠️ Asked at the roster level rather than through the route, because the answer is
    about ``resolve_roster``'s two readings and nothing about HTTP changes it.

    Membership takes **every** username in ``roles[]`` regardless of ``roleType``, so
    any spelling survives. Role *assignment* matches only ``PI`` and
    ``Allocation Manager``, so an unknown spelling is simply not a candidate — which is
    the safe direction. This closes a question the corpus could not, and it means the
    unanswered ``hdt@ucar.edu`` sample is no longer a cutover risk.
    """
    from factories import make_user
    from sam.xras.errors import ActionErrors
    from sam.xras.roster import resolve_roster

    pi = make_user(session)
    other = make_user(session)
    action = {'actionType': 'New', 'requestNumber': 'X',
              'actionBeginDate': '2026-01-01',
              'roles': [
                  {'roleType': 'PI', 'username': pi.username,
                   'beginDate': '2025-01-01', 'endDate': None},
                  {'roleType': spelling, 'username': other.username,
                   'beginDate': '2025-01-01', 'endDate': None},
              ]}

    errs = ActionErrors()
    roster = resolve_roster(session, action, errs)

    assert list(errs) == [], f'{spelling!r} reported: {list(errs)}'
    assert roster.pi_username == pi.username
    assert other.username in roster.member_usernames, (
        f'{spelling!r} was dropped from the roster — membership must ignore roleType')
    assert roster.admin_username is None
