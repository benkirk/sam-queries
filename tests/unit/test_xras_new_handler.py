"""New — 21% of traffic at 30% success, and the only handler that mints a projcode.

The order ``AddProjectAssembler`` marks ``// the order below is important!!`` is the
subject of :class:`TestTheOrderCannotBeRearranged`: the project is created **active**
because allocations cannot be added to an inactive one, allocations precede users
because they create the accounts a membership needs, and the inactivation runs last.
The resulting ``active = 0`` is by design — the success email is the human trigger.

The 70% failure rate is data, not code (a frozen ``user_organization``, unreconciled
ARC identities, unmapped resource keys), and the deliverable is that each of those now
arrives as a reviewable 422 rather than an opaque 500.
:class:`TestTheFailurePaths` covers the ones the corpus actually hit.

See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *New*.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from sam.accounting.allocations import Allocation, AllocationTransaction
from sam.projects.projects import Project
from sam.xras.errors import ActionErrors, XrasActionRejected
from sam.xras.handlers._allocations import clamp_start_to_commission
from sam.xras.handlers._fields import parse_action_begin_date
from sam.xras.handlers.new import handle_new

from xras_helpers import load_fixture, wire_resource
from xras_helpers import committing  # noqa: F401  — pytest resolves it by name

pytestmark = pytest.mark.unit


@pytest.fixture
def mapped_resource(session):
    """A resource carrying an ``xras_resource_repository_key_resource`` row.

    Only 13 such rows exist in production and 11 active resources have none,
    so the unmapped case the tests below exercise is a live failure mode
    rather than a defensive branch.
    """
    from factories import make_xras_key_mapping
    return make_xras_key_mapping(session)


@pytest.fixture(autouse=True)
def _stub_gid_pool(monkeypatch):
    """Hand out a unique GID without touching the pool.

    ⚠️ Not a convenience — a deadlock fix, and the reason is a real property of the
    handler. ``GidAllocation.allocate_next_gid`` takes ``with_for_update()`` on the
    lowest-``startGid`` block with room, and the handler then holds that row lock for
    the rest of the transaction: ``Project.create``, whose ``_ns_place_in_tree`` issues
    a **table-wide** ``UPDATE project SET tree_left = tree_left + 2 WHERE …`` to shift
    siblings. Twelve xdist workers doing that concurrently deadlock reliably
    (``1213 Deadlock found``), which then cascades into ``1305 SAVEPOINT … does not
    exist`` as the rollback unwinds.

    In production this is a non-issue: one webapp process, one action at a time. It is
    only the parallel suite that creates the contention, so the pool is stubbed here and
    its real behaviour stays covered by ``tests/unit/test_gid_allocation.py``. These
    tests are about the handler's ordering, not about GID allocation.
    """
    import itertools

    import sam.xras.handlers.new as handler

    counter = itertools.count(80_000_000 + int(os.environ.get(
        'PYTEST_XDIST_WORKER', 'gw0').removeprefix('gw') or 0) * 100_000)
    monkeypatch.setattr(handler.GidAllocation, 'allocate_next_gid',
                        classmethod(lambda cls, session: next(counter)))


@pytest.fixture
def creatable(session):
    """Everything a New action needs to succeed: a PI with a soft-linked org, an AOI,
    a facility/mnemonic pair with a projcode counter, and a GID pool."""
    from factories import (make_mnemonic_code, make_organization, make_user,
                           make_user_organization)
    from factories._seq import next_seq
    from sam.accounting.allocations import AllocationType
    from sam.projects.areas import AreaOfInterest

    pi = make_user(session)
    # ⚠️ The org name and the mnemonic description must be EQUAL (that is the soft
    # link the mnemonic extractor resolves) and UNIQUE per test.
    # `mnemonic_code_description_uk` is a unique index, so a fixed string here made
    # twelve xdist workers insert the same value concurrently — a reliable
    # `1213 Deadlock found` on duplicate-key gap locks, surfacing as a fixture error
    # rather than a test failure.
    soft_link = f'New Handler Test Section {next_seq("nh")}'
    org = make_organization(session, name=soft_link)
    make_user_organization(session, user=pi, organization=org)
    mnemonic = make_mnemonic_code(session, description=soft_link)

    # 'Small' under UNIV USS — the most common resulting type in production.
    from sam.resources.facilities import Panel
    allocation_type = (session.query(AllocationType)
                       .join(Panel, AllocationType.panel_id == Panel.panel_id)
                       .filter(Panel.panel_name == 'UNIV USS')
                       .filter(AllocationType.allocation_type == 'Small').one())
    aoi = session.get(AreaOfInterest, 1)
    return {'pi': pi, 'mnemonic': mnemonic, 'allocation_type': allocation_type,
            'aoi': aoi, 'org': org}


def action_for(creatable, *resources, **overrides):
    payload = {
        'actionType': 'New',
        'requestNumber': 'NCAR9999',
        'actionBeginDate': '2026-01-01',
        'actionEndDate': '2027-12-31',
        'requestTitle': 'A synthetic New action',
        'requestAbstract': 'Abstract text.',
        'allocationType': 'Small',
        'opportunityName': 'Small Allocation (University)',
        'fos': [{'fosNum': '1', 'isPrimary': True}],
        'grants': [],
        'resources': list(resources),
        'roles': [{'roleType': 'PI', 'username': creatable['pi'].username,
                   'beginDate': '2025-01-01', 'endDate': None}],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The happy path and the order it depends on.
# ---------------------------------------------------------------------------


class TestItCreatesAProject:

    def test_a_clean_action_mints_a_project(self, committing, creatable,
                                            mapped_resource):
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key, '250000')))

        assert result.status == 'processed'
        project = Project.get_by_projcode(session, result.projcode)
        assert project is not None
        assert project.title == 'A synthetic New action'
        assert project.project_lead_user_id == creatable['pi'].user_id
        assert project.area_of_interest_id == 1
        assert project.unix_gid is not None

    def test_the_projcode_is_generated_not_the_request_number(self, committing,
                                                              creatable,
                                                              mapped_resource):
        """``requestNumber`` on a New action is a request *token*. The projcode is
        minted from facility + mnemonic + counter."""
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        assert result.projcode != 'NCAR9999'
        assert creatable['mnemonic'].code in result.projcode

    def test_charging_is_never_exempt(self, committing, creatable, mapped_resource):
        """``getChargeType()`` is a constant ``NONEXEMPT`` in legacy."""
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        assert Project.get_by_projcode(session, result.projcode).charging_exempt is False

    def test_the_allocation_uses_the_actions_own_dates(self, committing, creatable,
                                                       mapped_resource):
        """⚠️ The contrast with Supplement, which derives its create-branch window from
        *today* and the project's history. Same table, two date policies, both
        legacy's."""
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key, '250000')))
        project = Project.get_by_projcode(session, result.projcode)
        allocation = (session.query(Allocation).join(Allocation.account)
                      .filter_by(project_id=project.project_id).one())
        assert allocation.start_date == datetime(2026, 1, 1)
        assert allocation.end_date == datetime(2027, 12, 31, 23, 59, 59)
        assert allocation.amount == pytest.approx(250_000.0)

    def test_the_leads_organization_is_linked(self, committing, creatable,
                                              mapped_resource):
        session = committing
        from sam.core.organizations import ProjectOrganization
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        project = Project.get_by_projcode(session, result.projcode)
        links = (session.query(ProjectOrganization)
                 .filter_by(project_id=project.project_id).all())
        assert [l.organization_id for l in links] == [
            creatable['org'].organization_id]

    def test_the_allocation_row_carries_no_actor(self, committing, creatable,
                                                 mapped_resource):
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        project = Project.get_by_projcode(session, result.projcode)
        allocation = (session.query(Allocation).join(Allocation.account)
                      .filter_by(project_id=project.project_id).one())
        rows = (session.query(AllocationTransaction)
                .filter_by(allocation_id=allocation.allocation_id).all())
        assert rows and all(r.user_id is None for r in rows)


class TestTheOrderCannotBeRearranged:
    """``// the order below is important!!``"""

    def test_the_project_ends_up_inactive(self, committing, creatable,
                                          mapped_resource):
        """⚠️ By design, not a bug. ``InactivateNewProject`` runs last, and the
        success email is the human trigger to approve. Production agrees: 21 of 23
        XRAS-created projects have since been activated by hand."""
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        assert Project.get_by_projcode(session, result.projcode).is_active is False

    def test_but_the_allocation_was_still_created(self, committing, creatable,
                                                  mapped_resource):
        """Which is only possible because the project was *active* while it happened —
        ``Project.addAllocation`` throws ``Cannot add allocation to inactive project``.
        The inactivation running last is what makes the middle steps legal."""
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        project = Project.get_by_projcode(session, result.projcode)
        assert not project.is_active
        assert (session.query(Allocation).join(Allocation.account)
                .filter_by(project_id=project.project_id).count() == 1)

    def test_members_are_added_because_allocations_created_the_accounts(
            self, committing, creatable, mapped_resource):
        """``add_user_to_project`` raises when a project has no accounts — the same
        constraint legacy states as "Assignment to project {0} cannot be made until
        project has an account on resource {1}". Allocations must precede users."""
        session = committing
        from factories import make_user
        collaborator = make_user(session)
        payload = action_for(creatable, wire_resource(mapped_resource.xras_key))
        payload['roles'].append({'roleType': 'User',
                                 'username': collaborator.username,
                                 'beginDate': '2025-01-01', 'endDate': None})

        result = handle_new(session, payload)
        project = Project.get_by_projcode(session, result.projcode)
        member_ids = {u.user_id for u in project.users}
        assert collaborator.user_id in member_ids

    def test_no_resources_means_no_accounts_and_no_membership_attempt(
            self, committing, creatable):
        """``new_ncar4232_failed.json`` is an Educational allocation shape. With no
        accounts, ``add_user_to_project`` would *raise* rather than no-op, so the step
        is skipped entirely — a real shape, not a defensive branch."""
        session = committing
        result = handle_new(session, action_for(creatable))
        assert result.status == 'processed'
        project = Project.get_by_projcode(session, result.projcode)
        assert not project.accounts


class TestCommissionDateClamping:
    """New behaviour with no precedent in this repo, so it is isolated in the handler
    rather than pushed into ``create_allocation``."""

    def test_an_early_start_is_clamped_forward_silently(self, session):
        from factories import make_resource
        resource = make_resource(session, commission_date=datetime(2026, 6, 1))
        assert clamp_start_to_commission(
            resource, datetime(2026, 1, 1)) == datetime(2026, 6, 1)

    def test_a_start_after_commissioning_is_untouched(self, session):
        from factories import make_resource
        resource = make_resource(session, commission_date=datetime(2026, 1, 1))
        assert clamp_start_to_commission(
            resource, datetime(2026, 6, 1)) == datetime(2026, 6, 1)

    def test_no_commission_date_means_no_clamping(self, session):
        from factories import make_resource
        resource = make_resource(session)
        assert clamp_start_to_commission(
            resource, datetime(2026, 1, 1)) == datetime(2026, 1, 1)

    def test_the_clamp_is_applied_to_the_created_allocation(self, committing,
                                                            creatable, session):
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        resource = make_resource(session, commission_date=datetime(2026, 6, 1))
        key = 940_000 + resource.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=key, resource_id=resource.resource_id))
        session.flush()

        result = handle_new(committing, action_for(creatable,
                                                   wire_resource(key, '100000')))
        project = Project.get_by_projcode(session, result.projcode)
        allocation = (session.query(Allocation).join(Allocation.account)
                      .filter_by(project_id=project.project_id).one())
        assert allocation.start_date == datetime(2026, 6, 1)

    def test_an_end_at_or_before_commissioning_reports_instead_of_500ing(
            self, committing, creatable, session):
        """Legacy raises ``IllegalStateException`` here, which is not observer-reported
        and becomes a 500 with no diagnostic. Same refusal, one an operator can act on
        — and the string keeps its missing space before ``(``."""
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        resource = make_resource(session, commission_date=datetime(2030, 1, 1))
        key = 950_000 + resource.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=key, resource_id=resource.resource_id))
        session.flush()

        with pytest.raises(XrasActionRejected) as exc:
            handle_new(committing, action_for(creatable, wire_resource(key)))
        assert exc.value.messages == [
            f'End date of allocation (2027-12-31) must be after commission date '
            f'of resource({resource.resource_name}).']


class TestTheFailurePaths:
    """The measured causes of the 70% failure rate, each now a reviewable 422."""

    def test_an_unresolvable_mnemonic_reports_the_24_percent_string(
            self, committing, session, mapped_resource):
        """The single largest failure class, caused by a ``user_organization`` table
        frozen since 2026-07-09."""
        from factories import (make_organization, make_user,
                               make_user_organization)
        pi = make_user(session)
        make_user_organization(session, user=pi, organization=make_organization(
            session))          # a generated, unique name with no mnemonic behind it
        payload = action_for({'pi': pi}, wire_resource(mapped_resource.xras_key))

        with pytest.raises(XrasActionRejected) as exc:
            handle_new(committing, payload)
        assert ('Could not determine Mnemonic code for internal PI via organization'
                in exc.value.messages)

    def test_an_unknown_pi_reports_and_creates_nothing(self, committing, session,
                                                       creatable, mapped_resource):
        """The ARC placeholder-identity class — 55% of failures. ``isReconciled`` is
        ``true`` on these, which is why it must not be trusted."""
        payload = action_for(creatable, wire_resource(mapped_resource.xras_key))
        payload['roles'] = [{'roleType': 'PI', 'username': 'placeholder07-user-00007',
                             'beginDate': '2025-01-01', 'endDate': None}]
        before = session.query(Project).count()

        with pytest.raises(XrasActionRejected) as exc:
            handle_new(committing, payload)
        assert 'PI placeholder07-user-00007 is not in database' in exc.value.messages
        assert session.query(Project).count() == before

    def test_an_unmapped_resource_key_reports(self, committing, creatable):
        """11 active SAM resources have no mapping row, so this is live."""
        with pytest.raises(XrasActionRejected) as exc:
            handle_new(committing, action_for(creatable, wire_resource(999_996)))
        assert ('No resource found in SAM corresponding to key 999996'
                in exc.value.messages)

    def test_problems_accumulate_into_one_422(self, committing, session,
                                              mapped_resource):
        """The point of assemble-then-check-once: an operator fixes a request in one
        pass rather than five."""
        from factories import make_user
        pi = make_user(session)                       # no organization → no mnemonic
        payload = action_for({'pi': pi}, wire_resource(999_995))
        payload['requestTitle'] = ''
        payload['fos'] = []

        with pytest.raises(XrasActionRejected) as exc:
            handle_new(committing, payload)
        assert set(exc.value.messages) >= {
            'Missing title',
            'No FieldOfScience (fos) objects',
            'No resource found in SAM corresponding to key 999995',
            'Could not determine Mnemonic code for internal PI via organization',
        }

    def test_nothing_is_written_and_no_projcode_is_consumed(self, committing, session,
                                                            creatable):
        """Both the projcode counter and the GID pool are drawn **inside** the
        transaction, after ``raise_if_any()`` — a rejected action consumes neither."""
        from sam.projects.projects import next_projcode
        facility_id = creatable['allocation_type'].panel.facility_id
        before = next_projcode(session, facility_id=facility_id,
                               mnemonic_code_id=creatable['mnemonic'].mnemonic_code_id,
                               allocate=False)

        payload = action_for(creatable)
        payload['requestTitle'] = ''
        with pytest.raises(XrasActionRejected):
            handle_new(committing, payload)

        after = next_projcode(session, facility_id=facility_id,
                              mnemonic_code_id=creatable['mnemonic'].mnemonic_code_id,
                              allocate=False)
        assert before == after


class TestDates:

    @pytest.mark.parametrize('raw', [None, '', '  '])
    def test_a_blank_begin_date_reports_missing(self, raw):
        errs = ActionErrors()
        assert parse_action_begin_date({'actionBeginDate': raw}, errs) is None
        assert list(errs) == ['Missing begin date for allocation(s)']

    def test_an_unparseable_begin_date_reports_conversion(self):
        errs = ActionErrors()
        assert parse_action_begin_date({'actionBeginDate': '01/01/2026'}, errs) is None
        assert list(errs) == ['Could not convert begin date for allocation(s)']

    def test_a_begin_date_is_not_moved_to_end_of_day(self):
        """Unlike the end date, which legacy runs through ``getDateAtEndOfDay``."""
        errs = ActionErrors()
        assert parse_action_begin_date(
            {'actionBeginDate': '2026-01-01'}, errs) == datetime(2026, 1, 1)


class TestContracts:

    def test_a_resolvable_grant_is_linked(self, committing, session, creatable,
                                          mapped_resource):
        from factories import make_contract
        contract = make_contract(session, contract_number='AGS-9990101')
        payload = action_for(creatable, wire_resource(mapped_resource.xras_key))
        payload['grants'] = [{'grantNumber': 'AGS-9990101'}]

        result = handle_new(committing, payload)
        project = Project.get_by_projcode(session, result.projcode)
        assert [pc.contract_id for pc in project.contracts] == [contract.contract_id]

    def test_an_empty_grants_array_is_not_an_error(self, committing, creatable,
                                                   mapped_resource):
        """⚠️ ``new_ncar4232_failed.json`` is an Educational allocation with
        ``grants: []`` — its failure was the mnemonic, not the missing contract. A
        project with no contract is legitimate."""
        session = committing
        result = handle_new(session, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        assert not Project.get_by_projcode(session, result.projcode).contracts

    def test_an_unresolvable_grant_reports(self, committing, creatable,
                                           mapped_resource):
        payload = action_for(creatable, wire_resource(mapped_resource.xras_key))
        payload['grants'] = [{'grantNumber': 'NSF-9990102'}]
        with pytest.raises(XrasActionRejected) as exc:
            handle_new(committing, payload)
        assert ('Cannot find contract for grant number "NSF-9990102" ("9990102")'
                in exc.value.messages)


class TestPanelAuthorisation:
    """New marks its CREATE rows when the resolved type is panel-authorised.

    ⚠️ **This was untested until the plan records landed**, on the handler with the
    highest production failure rate. Every other New test uses the default
    ``allocationType='Small'``, which is not panel-authorised, so the flag was
    ``False`` either way and nothing could tell a correct implementation from one
    that never set it — exactly the blind spot that hid the Adjustment bug for a
    sprint.

    It caught a real ordering hazard immediately: ``PlannedCreate`` captures the flag
    at construction, so computing ``panel_authorised`` *after* ``_plan_allocations()``
    — which is where it used to sit, harmlessly, because the old loop read it at
    execute time — would have stamped every row with the ``False`` from ``__init__``.
    """

    def _created_new_row(self, session, project_id):
        from sam.accounting.allocations import AllocationTransactionType
        created = (session.query(Allocation).join(Allocation.account)
                   .filter_by(project_id=project_id).one())
        return (session.query(AllocationTransaction)
                .filter(AllocationTransaction.allocation_id == created.allocation_id)
                .filter(AllocationTransaction.transaction_type
                        == AllocationTransactionType.NEW)
                .one())

    def test_a_panel_authorised_type_marks_the_created_row(
            self, committing, creatable, mapped_resource, session):
        result = handle_new(committing, action_for(
            creatable, wire_resource(mapped_resource.xras_key),
            allocationType='Large'))

        project = Project.get_by_projcode(session, result.projcode)
        assert self._created_new_row(session, project.project_id).auth_at_panel_mtg

    def test_a_non_panel_type_leaves_it_unmarked(
            self, committing, creatable, mapped_resource, session):
        """The other half of the pair — the flag tracks the type, not the branch."""
        result = handle_new(committing, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))

        project = Project.get_by_projcode(session, result.projcode)
        assert not self._created_new_row(session, project.project_id).auth_at_panel_mtg


class TestTheRosterIsNotFetchedTwice:
    """A regression guard on the double fetch, not a micro-optimisation.

    ``resolve_roster`` looks every username up in order to validate it, and this
    handler used to throw those rows away and query again from the usernames — a
    byte-identical block shared with ``update.py``. The cost scales with the roster:
    a ten-member action paid twenty ``SELECT``s for ten rows.

    Counting calls rather than asserting a timing, because the failure mode is
    silent — it looks exactly like working code.
    """

    def test_each_username_is_looked_up_exactly_once(
            self, committing, creatable, mapped_resource, monkeypatch):
        from sam.core.users import User
        from sam.xras.handlers.new import NewHandler

        calls = []
        original = User.get_by_username.__func__

        def counting(cls, session, username):
            calls.append(username)
            return original(cls, session, username)

        monkeypatch.setattr(User, 'get_by_username', classmethod(counting))

        handler = NewHandler(committing, action_for(
            creatable, wire_resource(mapped_resource.xras_key)))
        handler.assemble()

        assert calls, 'the roster resolved no users at all — test is vacuous'
        assert len(calls) == len(set(calls)), (
            f'each username should be fetched once; got {calls}')


class TestTheRegistration:

    def test_the_handler_is_bound_to_the_add_service(self):
        import sam.xras.handlers  # noqa: F401
        from sam.xras.dispatch import registered_services
        assert 'add' in registered_services()

    def test_a_corpus_new_action_reaches_it_through_the_dispatcher(
            self, committing, creatable, mapped_resource):
        """NCAR4253's shape, with referents substituted — the corpus usernames were
        scrubbed independently of this database and resolve to no rows."""
        session = committing
        from sam.xras.dispatch import dispatch_action
        import sam.xras.handlers  # noqa: F401

        data = load_fixture('new_ncar4253_ok.json')
        payload = action_for(creatable, wire_resource(mapped_resource.xras_key),
                             requestNumber=data['requestNumber'],
                             requestTitle=data['requestTitle'],
                             allocationType=data['allocationType'],
                             opportunityName=data['opportunityName'])

        result = dispatch_action(session, payload)
        assert result.status == 'processed'
        assert result.service == 'add'
