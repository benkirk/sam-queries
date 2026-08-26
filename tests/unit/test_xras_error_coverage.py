"""Every error string, produced by a synthetic payload — or declared unreachable.

Why a coverage matrix and not a pile of fixtures
------------------------------------------------
The eight real payloads reach roughly six of the thirty-four strings. The other
twenty-eight are the operator contract for **failed** requests, which is the majority of
XRAS traffic on the New path (70% failure), and going into an abrupt cutover they were
the largest untested surface in the sprint.

A pile of hand-written fixtures would cover some of them and silently stop covering the
rest as the code moved. So the structure here is a **declaration checked against the
module**: :data:`SCENARIOS` names the builder each test exercises,
:data:`UNREACHABLE` names the ones no handler can emit *and why*, and
:meth:`TestTheMatrixIsComplete.test_every_builder_is_declared` asserts the two together
account for every public builder in :mod:`sam.xras.errors`. Add a string without a
scenario and that test fails; delete a handler branch and its scenario fails.

WARNING: **Synthetic payloads validate handler branches, never the wire contract.** They
encode our reading of the protocol, so they cannot falsify it — real payloads did that
in Sprint A, and only real payloads can. Nothing here is evidence the schema is right.
See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *Follow-on*.
"""

from datetime import datetime

import pytest

from sam.xras import errors as e
from sam.xras.dispatch import dispatch_action
from sam.xras.errors import XrasActionRejected

from xras_helpers import wire_resource
from xras_helpers import committing  # noqa: F401  — pytest resolves it by name

pytestmark = pytest.mark.unit


#: builder name -> the test method that produces it. Checked against the module.
SCENARIOS = {
    'missing_title': 'blank requestTitle on a New',
    'missing_pi_role': 'roles[] carrying no PI',
    'pi_not_in_database': 'a PI username SAM does not hold',
    'pi_not_active': 'a PI whose account is inactive',
    'manager_not_in_database': 'an Allocation Manager SAM does not hold',
    'manager_not_active': 'an Allocation Manager whose account is inactive',
    'ambiguous_role': 'two PI roles both current (defect 1)',
    'username_missing': 'a roster member SAM does not hold',
    'username_inactive': 'a roster member whose account is inactive',
    'no_resource_for_key': 'a resources[].key with no mapping row',
    'awarded_amount_missing': 'a blank awardedAmount',
    'could_not_convert_amount': 'an unparseable awardedAmount',
    'missing_date': 'a blank actionBeginDate / actionEndDate',
    'could_not_convert_date': 'an unparseable actionBeginDate / actionEndDate',
    'extension_end_date_before_existing': 'an Extension that would shrink',
    'update_end_date_before_existing': 'an Update that would shrink',
    'all_end_dates_null_or_past': 'a Supplement create branch with no usable end',
    'cannot_find_contract': 'a grantNumber SAM holds no contract for',
    'ambiguous_contract': 'two contracts sharing a >=6-digit core',
    'mnemonic_external_failed': 'a PI whose institution has no soft link',
    'mnemonic_internal_failed': 'a PI whose organization has no soft link',
    'no_affiliation_for_pi': 'a PI who is not a SAM user at all',
    'no_current_affiliation_for_pi': 'a SAM user with no current institution or organization',
    'no_fos_objects': 'fos: []',
    'aoi_not_in_database': 'a fosNum that is no area_of_interest id',
    'allocation_type_undetermined': 'nothing for the eleven strategies to match',
    'no_allocation_type_for_pair': 'a resolved pair with no allocation_type row',
    'allocation_end_before_commission': 'an end date at/before commissioning',
    'adjustment_would_go_negative': 'a negative Adjustment larger than the balance',
}

#: builder name -> why no handler can emit it. Each one is a decision, not a gap.
UNREACHABLE = {
    'no_resource_for_name':
        'The ROSTER path\'s resource lookup. Legacy fans the roster out per '
        'resources[] entry and resolves each by NAME; SAM\'s add_user_to_project is '
        'project-scoped and adds a member to every account at once, so there is no '
        'per-resource name lookup to fail. The allocation path\'s key variant '
        '(no_resource_for_key) is the one that fires.',
    'transfer_one_source_only': 'Transfer is deliberately not serviced.',
    'transfer_requires_source': 'Transfer is deliberately not serviced.',
    'transfer_requires_destination': 'Transfer is deliberately not serviced.',
    'transfer_source_has_no_allocation': 'Transfer is deliberately not serviced.',
    'transfer_credit_exceeds_debit': 'Transfer is deliberately not serviced.',
}


def public_builders():
    return {name for name in dir(e)
            if not name.startswith('_')
            and callable(getattr(e, name))
            and getattr(e, name).__module__ == e.__name__
            and name not in ('ActionErrors', 'XrasActionRejected')}


# ---------------------------------------------------------------------------
# Harness.
# ---------------------------------------------------------------------------


@pytest.fixture
def mapped_resource(session):
    """A resource carrying an ``xras_resource_repository_key_resource`` row.

    Only 13 such rows exist in production and 11 active resources have none,
    so the unmapped case the tests below exercise is a live failure mode
    rather than a defensive branch.
    """
    from factories import make_xras_key_mapping
    return make_xras_key_mapping(session)


@pytest.fixture
def linked_pi(session):
    """A PI whose organization resolves to a mnemonic — so New actions can reach
    branches *past* the mnemonic extractor."""
    from factories import (make_mnemonic_code, make_organization, make_user,
                           make_user_organization)
    from factories._seq import next_seq

    pi = make_user(session)
    soft_link = f'Error Coverage Section {next_seq("ec")}'
    make_user_organization(session, user=pi,
                           organization=make_organization(session, name=soft_link))
    make_mnemonic_code(session, description=soft_link)
    return pi


def messages(session, action):
    """Dispatch and return the accumulated 422 list — empty when it succeeded."""
    try:
        dispatch_action(session, action)
        return []
    except XrasActionRejected as exc:
        return exc.messages


def new_action(pi, *resources, **overrides):
    payload = {
        'actionType': 'New', 'requestNumber': 'ECOV0001',
        'actionBeginDate': '2026-01-01', 'actionEndDate': '2027-12-31',
        'requestTitle': 'Coverage probe', 'requestAbstract': None,
        'allocationType': 'Small',
        'opportunityName': 'Small Allocation (University)',
        'fos': [{'fosNum': '1', 'isPrimary': True}],
        'grants': [], 'resources': list(resources),
        'roles': ([{'roleType': 'PI', 'username': pi.username,
                    'beginDate': '2025-01-01', 'endDate': None}]
                  if pi is not None else []),
    }
    payload.update(overrides)
    return payload


def project_with_allocation(session, resource, *, end=datetime(2033, 7, 31),
                            amount=1_000_000.0):
    from factories import make_account, make_allocation, make_project, make_user
    project = make_project(session)
    project.project_lead_user_id = make_user(session).user_id
    allocation = make_allocation(
        session, amount=amount, start_date=datetime(2020, 1, 1), end_date=end,
        account=make_account(session, project=project, resource=resource))
    session.flush()
    session.refresh(project)
    return project, allocation


# ---------------------------------------------------------------------------
# The matrix.
# ---------------------------------------------------------------------------


class TestTheMatrixIsComplete:

    def test_every_builder_is_declared(self):
        """The gate. A new string without a scenario or an explicit reason fails here,
        which is the only way this file stays honest as the code moves."""
        declared = set(SCENARIOS) | set(UNREACHABLE)
        undeclared = sorted(public_builders() - declared)
        assert undeclared == [], (
            f'these error strings are neither exercised nor declared unreachable: '
            f'{undeclared}')

    def test_no_builder_is_declared_twice(self):
        assert set(SCENARIOS).isdisjoint(UNREACHABLE)

    def test_no_declaration_is_stale(self):
        stale = sorted((set(SCENARIOS) | set(UNREACHABLE)) - public_builders())
        assert stale == [], f'declared but no longer exists: {stale}'

    def test_every_unreachable_entry_gives_a_reason(self):
        assert all(len(reason) > 30 for reason in UNREACHABLE.values())

    def test_the_only_unreachable_strings_are_transfer_and_the_roster_resource(self):
        """Stated as a fact so that a *new* unreachable string has to argue for
        itself rather than joining a list."""
        assert set(UNREACHABLE) == {
            'no_resource_for_name',
            'transfer_one_source_only', 'transfer_requires_source',
            'transfer_requires_destination', 'transfer_source_has_no_allocation',
            'transfer_credit_exceeds_debit',
        }


# ---------------------------------------------------------------------------
# Identity.
# ---------------------------------------------------------------------------


class TestIdentityStrings:

    def test_missing_title(self, committing, linked_pi, mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key), requestTitle=''))
        assert e.missing_title() in out

    def test_missing_pi_role(self, committing, mapped_resource):
        out = messages(committing, new_action(
            None, wire_resource(mapped_resource.xras_key)))
        assert e.missing_pi_role() in out

    def test_pi_not_in_database(self, committing, mapped_resource):
        action = new_action(None, wire_resource(mapped_resource.xras_key))
        action['roles'] = [{'roleType': 'PI', 'username': 'ecov_ghost',
                            'beginDate': '2025-01-01', 'endDate': None}]
        assert e.pi_not_in_database('ecov_ghost') in messages(committing, action)

    def test_pi_not_active(self, committing, session, mapped_resource):
        from factories import make_user
        pi = make_user(session, active=False)
        out = messages(committing, new_action(
            pi, wire_resource(mapped_resource.xras_key)))
        assert e.pi_not_active(pi.username) in out
        assert out[out.index(e.pi_not_active(pi.username))].endswith(': ')

    def test_manager_not_in_database(self, committing, linked_pi, mapped_resource):
        action = new_action(linked_pi, wire_resource(mapped_resource.xras_key))
        action['roles'].append({'roleType': 'Allocation Manager',
                                'username': 'ecov_ghost_am',
                                'beginDate': '2025-01-01', 'endDate': None})
        out = messages(committing, action)
        assert e.manager_not_in_database('ecov_ghost_am') in out

    def test_manager_not_active(self, committing, session, linked_pi,
                                mapped_resource):
        from factories import make_user
        manager = make_user(session, active=False)
        action = new_action(linked_pi, wire_resource(mapped_resource.xras_key))
        action['roles'].append({'roleType': 'Allocation Manager',
                                'username': manager.username,
                                'beginDate': '2025-01-01', 'endDate': None})
        out = messages(committing, action)
        assert e.manager_not_active(manager.username) in out

    def test_ambiguous_role(self, committing, session, linked_pi, mapped_resource):
        """Defect 1: legacy takes the first in array order and never reports."""
        from factories import make_user
        second = make_user(session)
        action = new_action(linked_pi, wire_resource(mapped_resource.xras_key))
        action['roles'].append({'roleType': 'PI', 'username': second.username,
                                'beginDate': '2025-01-01', 'endDate': None})
        out = messages(committing, action)
        assert any(m.startswith('Multiple PI roles are in range') for m in out)

    def test_username_missing(self, committing, linked_pi, mapped_resource):
        """The roster path's own wording, distinct from the PI variant."""
        action = new_action(linked_pi, wire_resource(mapped_resource.xras_key))
        action['roles'].append({'roleType': 'Co-PI', 'username': 'ecov_ghost_member',
                                'beginDate': '2025-01-01', 'endDate': None})
        assert e.username_missing('ecov_ghost_member') in messages(committing, action)

    def test_username_inactive(self, committing, session, linked_pi,
                               mapped_resource):
        from factories import make_user
        member = make_user(session, active=False)
        action = new_action(linked_pi, wire_resource(mapped_resource.xras_key))
        action['roles'].append({'roleType': 'User', 'username': member.username,
                                'beginDate': '2025-01-01', 'endDate': None})
        assert e.username_inactive(member.username) in messages(committing, action)

    def test_no_affiliation_for_pi(self, committing, mapped_resource):
        """Fires from the mnemonic extractor when the PI is not a SAM user at all —
        alongside, not instead of, the roster's ``PI %s is not in database``."""
        action = new_action(None, wire_resource(mapped_resource.xras_key))
        action['roles'] = [{'roleType': 'PI', 'username': 'ecov_nobody',
                            'beginDate': '2025-01-01', 'endDate': None}]
        out = messages(committing, action)
        assert e.no_affiliation_for_pi('ecov_nobody') in out
        assert e.pi_not_in_database('ecov_nobody') in out


# ---------------------------------------------------------------------------
# Mnemonic.
# ---------------------------------------------------------------------------


class TestMnemonicStrings:

    def test_mnemonic_internal_failed(self, committing, session, mapped_resource):
        """**24% of all legacy XRAS failures** carry this one."""
        from factories import make_organization, make_user, make_user_organization
        pi = make_user(session)
        make_user_organization(session, user=pi,
                               organization=make_organization(session))
        out = messages(committing, new_action(
            pi, wire_resource(mapped_resource.xras_key)))
        assert e.mnemonic_internal_failed() in out

    def test_no_current_affiliation_for_pi(self, committing, session, mapped_resource):
        """The affiliation rows were end-dated upstream (NCAR4262's class)."""
        from factories import make_user
        pi = make_user(session)
        out = messages(committing, new_action(
            pi, wire_resource(mapped_resource.xras_key)))
        assert e.no_current_affiliation_for_pi(pi.username) in out
        assert e.mnemonic_internal_failed() not in out

    def test_mnemonic_external_failed(self, committing, session, mapped_resource):
        from factories import make_institution, make_user, make_user_institution
        pi = make_user(session)
        make_user_institution(session, user=pi,
                              institution=make_institution(session))
        out = messages(committing, new_action(
            pi, wire_resource(mapped_resource.xras_key)))
        assert e.mnemonic_external_failed() in out


# ---------------------------------------------------------------------------
# Area of interest and allocation type.
# ---------------------------------------------------------------------------


class TestClassificationStrings:

    def test_no_fos_objects(self, committing, linked_pi, mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key), fos=[]))
        assert e.no_fos_objects() in out

    def test_aoi_not_in_database(self, committing, linked_pi, mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key),
            fos=[{'fosNum': '99999', 'isPrimary': True}]))
        assert e.aoi_not_in_database('99999') in out

    def test_allocation_type_undetermined(self, committing, linked_pi,
                                          mapped_resource):
        """All eleven strategies decline: no ``allocationType``, and an
        ``opportunityName`` matching no marker.

        WARNING: The title matters as much as the opportunity name, which is easy to forget.
        ``ExternalStrategy`` full-matches ``(.* )?External( .*)?`` against
        ``requestTitle`` too, so a title merely *mentioning* the word "External"
        resolves the whole action to ``External Projects`` — this test originally
        failed for exactly that reason, with a title reading "Nothing matching CSL or
        External here".
        """
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key),
            allocationType=None, opportunityName='Something Unremarkable',
            requestTitle='A title matching no strategy at all'))
        assert e.allocation_type_undetermined() in out

    def test_no_allocation_type_for_pair(self, committing, linked_pi,
                                         mapped_resource, monkeypatch):
        """All twelve pairs the chain can produce exist in the lookup tables, so this
        is only reachable by removing one — which is exactly the drift it guards
        against. Forced rather than seeded, because deleting an ``allocation_type`` row
        would cascade into other suites."""
        import sam.xras.extractors as ex
        from sam.xras.extractors import SelectionParms
        monkeypatch.setattr(ex, 'select_allocation_type_parms',
                            lambda _: SelectionParms('No Such Panel', 'No Such Type'))
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key)))
        assert e.no_allocation_type_for_pair('No Such Panel', 'No Such Type') in out


# ---------------------------------------------------------------------------
# Resources, amounts and dates.
# ---------------------------------------------------------------------------


class TestResourceAndAmountStrings:

    def test_no_resource_for_key(self, committing, linked_pi):
        out = messages(committing, new_action(linked_pi, wire_resource(777_777)))
        assert e.no_resource_for_key('777777') in out

    def test_awarded_amount_missing(self, committing, linked_pi, mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key, '')))
        assert e.awarded_amount_missing() in out

    def test_three_resources_missing_an_amount_still_report_once(
            self, committing, session, linked_pi):
        """The accumulator's dedup, in the place most likely to be got wrong — and the
        one behavior only a synthetic payload can demonstrate."""
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        resources = []
        for _ in range(3):
            r = make_resource(session)
            key = 310_000 + r.resource_id
            session.add(XrasResourceRepositoryKeyResource(
                resource_repository_key=key, resource_id=r.resource_id))
            resources.append(key)
        session.flush()

        out = messages(committing, new_action(
            linked_pi, *[wire_resource(k, '') for k in resources]))
        assert out.count(e.awarded_amount_missing()) == 1

    def test_could_not_convert_amount(self, committing, linked_pi, mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key, '1e9x')))
        assert e.could_not_convert_amount('1e9x') in out
        assert '"  to float' in out[out.index(e.could_not_convert_amount('1e9x'))]

    def test_allocation_end_before_commission(self, committing, session, linked_pi):
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        resource = make_resource(session, commission_date=datetime(2030, 1, 1))
        key = 320_000 + resource.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=key, resource_id=resource.resource_id))
        session.flush()

        out = messages(committing, new_action(linked_pi, wire_resource(key)))
        assert e.allocation_end_before_commission(
            '2027-12-31', resource.resource_name) in out


class TestDateStrings:

    def test_missing_begin_date(self, committing, linked_pi, mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key),
            actionBeginDate=''))
        assert e.missing_date('begin') in out

    def test_missing_end_date(self, committing, mapped_resource):
        project, _ = project_with_allocation(committing, mapped_resource)
        out = messages(committing, {
            'actionType': 'Extension', 'requestNumber': project.projcode,
            'actionEndDate': None, 'resources': [], 'roles': []})
        assert e.missing_date('end') in out

    def test_could_not_convert_begin_date(self, committing, linked_pi,
                                          mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key),
            actionBeginDate='01/01/2026'))
        assert e.could_not_convert_date('begin') in out

    def test_could_not_convert_end_date(self, committing, mapped_resource):
        project, _ = project_with_allocation(committing, mapped_resource)
        out = messages(committing, {
            'actionType': 'Extension', 'requestNumber': project.projcode,
            'actionEndDate': 'next tuesday', 'resources': [], 'roles': []})
        assert e.could_not_convert_date('end') in out

    def test_the_two_shrink_strings_are_different_and_both_reachable(
            self, committing, mapped_resource, session):
        """The pair an operator uses to tell which path rejected them."""
        project, _ = project_with_allocation(committing, mapped_resource)
        extension = messages(committing, {
            'actionType': 'Extension', 'requestNumber': project.projcode,
            'actionEndDate': '2027-09-30', 'resources': [], 'roles': []})
        assert e.extension_end_date_before_existing('2033-07-31') in extension

        project2, _ = project_with_allocation(committing, mapped_resource)
        update = messages(committing, {
            'actionType': 'New', 'requestNumber': project2.projcode,
            'actionBeginDate': '2026-01-01', 'actionEndDate': '2027-09-30',
            'requestTitle': 'probe', 'allocationType': 'Small',
            'opportunityName': 'Small Allocation (University)',
            'fos': [{'fosNum': '1', 'isPrimary': True}], 'grants': [],
            'resources': [wire_resource(mapped_resource.xras_key)],
            'roles': [{'roleType': 'PI',
                       'username': project2.lead.username if project2.lead else 'x',
                       'beginDate': '2025-01-01', 'endDate': None}]})
        assert e.update_end_date_before_existing(
            mapped_resource.resource_name) in update


class TestAllocationLifecycleStrings:

    def test_all_end_dates_null_or_past(self, committing, session, mapped_resource):
        """A Supplement whose resource has no allocation, on a project whose contracts
        and allocations have all expired."""
        from factories import make_project
        project = make_project(session)
        out = messages(committing, {
            'actionType': 'Supplement', 'requestNumber': project.projcode,
            'allocationType': 'Small',
            'resources': [wire_resource(mapped_resource.xras_key, '250000')],
            'roles': []})
        assert e.all_end_dates_null_or_past(project.projcode) in out

    def test_adjustment_would_go_negative(self, committing, mapped_resource):
        project, _ = project_with_allocation(committing, mapped_resource,
                                             amount=100_000.0)
        out = messages(committing, {
            'actionType': 'Adjustment', 'requestNumber': project.projcode,
            'allocationType': 'Small',
            'resources': [wire_resource(mapped_resource.xras_key, '-500000')],
            'roles': []})
        assert any(m.startswith('Adjustment of -500,000.00') for m in out)


class TestContractStrings:

    def test_cannot_find_contract(self, committing, linked_pi, mapped_resource):
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key),
            grants=[{'grantNumber': 'NSF-8880001'}]))
        assert e.cannot_find_contract('NSF-8880001', '8880001') in out

    def test_ambiguous_contract(self, committing, session, linked_pi,
                                mapped_resource):
        """The production collision shape — ``1049089`` and ``PLR-1049089`` both exist.
        Legacy raises ``NonUniqueResultException`` here and 500s with no diagnostic."""
        from factories import make_contract
        make_contract(session, contract_number='8880002')
        make_contract(session, contract_number='PLR-8880002')
        out = messages(committing, new_action(
            linked_pi, wire_resource(mapped_resource.xras_key),
            grants=[{'grantNumber': 'NSF-8880002'}]))
        assert any(m.startswith('Ambiguous contract for grant number') for m in out)


class TestAccumulationAcrossCategories:
    """The whole point of assemble -> check once: an operator fixes a request in one
    pass rather than five."""

    def test_seven_distinct_problems_arrive_in_one_422(self, committing, session):
        from factories import make_organization, make_user, make_user_organization
        pi = make_user(session)
        make_user_organization(session, user=pi,
                               organization=make_organization(session))

        action = new_action(pi, wire_resource(777_778, ''), requestTitle='', fos=[],
                            grants=[{'grantNumber': 'NSF-8880003'}])
        action['roles'].append({'roleType': 'User', 'username': 'ecov_ghost_2',
                                'beginDate': '2025-01-01', 'endDate': None})

        out = messages(committing, action)
        assert {
            e.missing_title(),
            e.no_fos_objects(),
            e.mnemonic_internal_failed(),
            e.no_resource_for_key('777778'),
            e.awarded_amount_missing(),
            e.cannot_find_contract('NSF-8880003', '8880003'),
            e.username_missing('ecov_ghost_2'),
        } <= set(out)

    def test_nothing_is_written_when_anything_is_reported(self, committing, session):
        from factories import make_project
        before = session.query(make_project(session).__class__).count()
        action = new_action(None, wire_resource(777_779))
        messages(committing, action)
        assert session.query(make_project(session).__class__).count() == before + 1
