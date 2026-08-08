"""Update — ``New``/``Renewal`` against a project that already exists.

The per-resource decision is the widest in the sprint: one resource can emit **three**
commands (extend, then supplement-or-adjust), or one (add), or an error. The branch
table in the handler's docstring is what these tests walk.

Three legacy bugs live here, and this port treats them differently:

* it silently **re-activates** an inactive project → not ported, warned
* it **never updates** the lead or admin (a guard that always passes, plus missing
  braces) → fixed
* the ``UNDO AUTO/DEFAULT`` compensating adjustment → not ported, warned (defect 5)

See ``docs/plans/XRAS_SPRINT_C.md`` § *Update*.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from sam.accounting.allocations import (
    Allocation,
    AllocationTransaction,
    AllocationTransactionType,
)
from sam.projects.projects import Project
from sam.xras.errors import XrasActionRejected
from sam.xras.handlers.update import (
    CONTINGENT_RESOURCE_COMMENT,
    handle_update,
    is_allocation_overlapping,
)

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).parent.parent / 'fixtures' / 'xras' / 'actions'


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture
def committing(session, monkeypatch):
    """Flush instead of commit — see the Extension handler tests for why this exists."""
    from contextlib import contextmanager

    import sam.xras.handlers.base as base

    @contextmanager
    def flushing(sess):
        yield sess
        sess.flush()

    monkeypatch.setattr(base, 'management_transaction', flushing)
    return session


@pytest.fixture
def mapped_resource(session):
    from factories import make_resource
    from sam.integration.xras import XrasResourceRepositoryKeyResource

    resource = make_resource(session)
    key = 960_000 + resource.resource_id
    session.add(XrasResourceRepositoryKeyResource(
        resource_repository_key=key, resource_id=resource.resource_id))
    session.flush()
    resource.xras_key = key
    return resource


@pytest.fixture
def existing(session, mapped_resource):
    """An existing project with a PI and one allocation running 2026 → 2027."""
    from factories import (make_account, make_allocation, make_project, make_user,
                           make_user_organization)
    pi = make_user(session)
    project = make_project(session)
    project.project_lead_user_id = pi.user_id
    allocation = make_allocation(
        session, amount=1_000_000.0,
        start_date=datetime(2026, 1, 1), end_date=datetime(2027, 12, 31),
        account=make_account(session, project=project, resource=mapped_resource))
    session.flush()
    session.refresh(project)
    return {'project': project, 'pi': pi, 'allocation': allocation}


def wire_resource(key, amount='250000', comments=None):
    return {'key': key, 'awardedAmount': amount, 'comments': comments}


def action_for(existing, *resources, **overrides):
    payload = {
        'actionType': 'New',
        'requestNumber': existing['project'].projcode,
        'actionBeginDate': '2026-01-01',
        'actionEndDate': '2027-12-31',
        'requestTitle': 'An updated title',
        'requestAbstract': 'Updated abstract.',
        'allocationType': 'Small',
        'opportunityName': 'Small Allocation (University)',
        'fos': [{'fosNum': '1', 'isPrimary': True}],
        'grants': [],
        'resources': list(resources),
        'roles': [{'roleType': 'PI', 'username': existing['pi'].username,
                   'beginDate': '2025-01-01', 'endDate': None}],
    }
    payload.update(overrides)
    return payload


def txns_for(session, allocation):
    return (session.query(AllocationTransaction)
            .filter(AllocationTransaction.allocation_id == allocation.allocation_id)
            .all())


def rows_of(session, allocation, kind):
    return [t for t in txns_for(session, allocation) if t.transaction_type == kind]


# ---------------------------------------------------------------------------
# The overlap test.
# ---------------------------------------------------------------------------


class TestOverlap:

    def test_overlapping_windows_overlap(self, session):
        from factories import make_allocation
        allocation = make_allocation(session, start_date=datetime(2026, 1, 1),
                                     end_date=datetime(2027, 12, 31))
        assert is_allocation_overlapping(allocation, datetime(2027, 1, 1),
                                         datetime(2028, 12, 31))

    def test_a_disjoint_window_does_not(self, session):
        from factories import make_allocation
        allocation = make_allocation(session, start_date=datetime(2020, 1, 1),
                                     end_date=datetime(2021, 12, 31))
        assert not is_allocation_overlapping(allocation, datetime(2026, 1, 1),
                                             datetime(2027, 12, 31))

    @pytest.mark.parametrize('start,end', [
        (None, datetime(2027, 12, 31)), (datetime(2026, 1, 1), None), (None, None)])
    def test_a_null_action_date_means_no_overlap(self, session, start, end):
        """⚠️ Which routes the resource to ADD — and legacy then dereferences the same
        null on the commission clamp and throws. Unreachable here because assembly
        reports the missing date first, but the guard stays explicit."""
        from factories import make_allocation
        allocation = make_allocation(session)
        assert not is_allocation_overlapping(allocation, start, end)


# ---------------------------------------------------------------------------
# The per-resource branch table.
# ---------------------------------------------------------------------------


class TestTheAddBranch:

    def test_a_resource_with_no_allocation_gets_one(self, committing, existing,
                                                    session):
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        second = make_resource(session)
        key = 970_000 + second.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=key, resource_id=second.resource_id))
        session.flush()

        handle_update(committing, action_for(existing, wire_resource(key, '500000')))

        created = (session.query(Allocation).join(Allocation.account)
                   .filter_by(project_id=existing['project'].project_id,
                              resource_id=second.resource_id).one())
        assert created.amount == pytest.approx(500_000.0)
        assert created.start_date == datetime(2026, 1, 1)

    def test_a_non_overlapping_allocation_gets_a_second_one(self, committing,
                                                            existing, session,
                                                            mapped_resource):
        """The allocation exists but its window is disjoint from the action's, so this
        is an ADD rather than a supplement — the same resource ends up with two."""
        existing['allocation'].start_date = datetime(2020, 1, 1)
        existing['allocation'].end_date = datetime(2021, 12, 31)
        session.flush()

        handle_update(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '300000')))

        allocations = (session.query(Allocation).join(Allocation.account)
                       .filter_by(project_id=existing['project'].project_id).all())
        assert len(allocations) == 2


class TestTheShrinkError:

    def test_the_update_string_is_not_the_extension_string(self, committing,
                                                           existing,
                                                           mapped_resource):
        """⚠️ Update interpolates a **resource name** and omits "is"; Extension
        interpolates a **date** and includes it. Which one an operator sees is how they
        tell which path rejected them."""
        with pytest.raises(XrasActionRejected) as exc:
            handle_update(committing, action_for(
                existing, wire_resource(mapped_resource.xras_key),
                actionEndDate='2026-06-30'))

        assert exc.value.messages == [
            f'Action end date before existing allocation end date '
            f'for {mapped_resource.resource_name}']
        assert ' is before ' not in exc.value.messages[0]

    def test_nothing_is_written(self, committing, existing, mapped_resource,
                                session):
        with pytest.raises(XrasActionRejected):
            handle_update(committing, action_for(
                existing, wire_resource(mapped_resource.xras_key),
                actionEndDate='2026-06-30'))
        assert existing['allocation'].amount == pytest.approx(1_000_000.0)
        assert existing['project'].title != 'An updated title'


class TestTheThreeCommandPath:
    """One resource, two allocation commands: extend then supplement."""

    def test_a_later_end_date_extends_and_supplements(self, committing, existing,
                                                      mapped_resource, session):
        handle_update(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '250000'),
            actionEndDate='2029-12-31'))

        allocation = existing['allocation']
        assert allocation.end_date == datetime(2029, 12, 31, 23, 59, 59)
        assert allocation.amount == pytest.approx(1_250_000.0)
        assert rows_of(session, allocation, AllocationTransactionType.EXTENSION)
        assert rows_of(session, allocation, AllocationTransactionType.SUPPLEMENT)

    def test_a_negative_amount_extends_and_adjusts(self, committing, existing,
                                                   mapped_resource, session):
        handle_update(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '-250000'),
            actionEndDate='2029-12-31'))

        allocation = existing['allocation']
        assert allocation.end_date == datetime(2029, 12, 31, 23, 59, 59)
        assert allocation.amount == pytest.approx(750_000.0)
        assert rows_of(session, allocation, AllocationTransactionType.ADJUSTMENT)

    def test_a_zero_amount_extends_only(self, committing, existing, mapped_resource,
                                        session):
        """``> 0`` supplements, ``< 0`` adjusts — zero does neither."""
        handle_update(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '0'),
            actionEndDate='2029-12-31'))

        allocation = existing['allocation']
        assert allocation.end_date == datetime(2029, 12, 31, 23, 59, 59)
        assert allocation.amount == pytest.approx(1_000_000.0)

    def test_an_equal_end_date_supplements_without_extending(self, committing,
                                                             existing,
                                                             mapped_resource,
                                                             session):
        handle_update(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '250000')))
        allocation = existing['allocation']
        assert allocation.amount == pytest.approx(1_250_000.0)
        assert not rows_of(session, allocation, AllocationTransactionType.EXTENSION)

    def test_the_extend_carries_the_resource_comment_not_the_extension_one(
            self, committing, existing, mapped_resource, session):
        """⚠️ Update-driven extends do **not** say ``XrasAction Extension Request``."""
        handle_update(committing, action_for(
            existing,
            wire_resource(mapped_resource.xras_key, '250000',
                          comments='Renewal award FY27'),
            actionEndDate='2029-12-31'))

        rows = rows_of(session, existing['allocation'],
                       AllocationTransactionType.EXTENSION)
        assert [r.transaction_comment for r in rows] == ['Renewal award FY27']


class TestTheContingentResourceShortCircuit:
    """⚠️ Ported, unlike the undo — this comparison uses ``.name()`` on both sides."""

    def test_the_date_moves_but_the_amount_does_not(self, committing, existing,
                                                    mapped_resource, session):
        handle_update(committing, action_for(
            existing,
            wire_resource(mapped_resource.xras_key, '250000',
                          comments=CONTINGENT_RESOURCE_COMMENT),
            actionEndDate='2029-12-31'))

        allocation = existing['allocation']
        assert allocation.end_date == datetime(2029, 12, 31, 23, 59, 59)
        assert allocation.amount == pytest.approx(1_000_000.0)
        assert not rows_of(session, allocation, AllocationTransactionType.SUPPLEMENT)

    def test_the_marker_is_the_name_spelling_not_the_value_one(self):
        """The undo is broken precisely because its two sides use different spellings;
        this one works because both use ``.name()``."""
        assert CONTINGENT_RESOURCE_COMMENT == 'AUTO_DEFAULT_ALLOCATION_TRANSACTION'
        assert CONTINGENT_RESOURCE_COMMENT != 'AUTO/DEFAULT'


class TestLegacyDefectFive:
    """The ``UNDO AUTO/DEFAULT`` adjustment that has never once executed."""

    def test_an_auto_default_allocation_is_warned_about_not_compensated(
            self, committing, existing, mapped_resource, session, caplog):
        """Legacy would write a compensating adjustment here. Its writers use
        ``.name()`` and its detector compares ``.getValue()``, so they never match —
        production holds **zero** UNDO rows of either spelling. Detected and warned;
        no compensating row is written."""
        allocation = existing['allocation']
        session.add(AllocationTransaction(
            allocation_id=allocation.allocation_id,
            transaction_type=AllocationTransactionType.ADJUSTMENT,
            transaction_amount=allocation.amount,
            transaction_comment='AUTO/DEFAULT',
            creation_time=datetime.now()))
        session.flush()

        with caplog.at_level('WARNING'):
            handle_update(committing, action_for(
                existing, wire_resource(mapped_resource.xras_key, '250000'),
                actionEndDate='2029-12-31'))

        assert 'AUTO/DEFAULT' in caplog.text
        assert 'defect 5' in caplog.text
        # The supplement still happened; only the undo is absent.
        assert allocation.amount == pytest.approx(1_250_000.0)
        assert not [t for t in txns_for(session, allocation)
                    if (t.transaction_comment or '').startswith('UNDO')]


class TestTheProjectBugsWeFix:

    def test_an_inactive_project_is_not_re_activated(self, committing, existing,
                                                     mapped_resource, caplog):
        """⚠️ Legacy hardcodes ``getActive()`` to true and, unlike the Add path, runs
        no ``InactivateNewProject`` afterwards. An XRAS project is inactive because a
        human has not approved it; approving it as a side effect of a Supplement is
        wrong."""
        project = existing['project']
        project.active = False
        committing.flush()

        with caplog.at_level('WARNING'):
            handle_update(committing, action_for(
                existing, wire_resource(mapped_resource.xras_key, '250000')))

        assert project.is_active is False
        assert 're-activate' in caplog.text

    def test_an_active_project_stays_active(self, committing, existing,
                                            mapped_resource):
        handle_update(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '250000')))
        assert existing['project'].is_active is True

    def test_the_lead_is_actually_updated(self, committing, existing,
                                          mapped_resource, session):
        """Legacy never moves it: the guard compares the fetched user's username
        against the lookup key (always equal), and ``setLeadUser`` is missing braces so
        only its first statement is guarded. Plainly a bug."""
        from factories import make_user
        new_pi = make_user(session)
        payload = action_for(existing, wire_resource(mapped_resource.xras_key,
                                                     '250000'))
        payload['roles'] = [{'roleType': 'PI', 'username': new_pi.username,
                             'beginDate': '2025-01-01', 'endDate': None}]

        handle_update(committing, payload)
        assert existing['project'].project_lead_user_id == new_pi.user_id

    def test_the_admin_is_actually_updated(self, committing, existing,
                                           mapped_resource, session):
        from factories import make_user
        manager = make_user(session)
        payload = action_for(existing, wire_resource(mapped_resource.xras_key,
                                                     '250000'))
        payload['roles'].append({'roleType': 'Allocation Manager',
                                 'username': manager.username,
                                 'beginDate': '2025-01-01', 'endDate': None})

        handle_update(committing, payload)
        assert existing['project'].project_admin_user_id == manager.user_id

    def test_the_title_and_abstract_move(self, committing, existing,
                                         mapped_resource):
        handle_update(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '250000')))
        assert existing['project'].title == 'An updated title'
        assert existing['project'].abstract == 'Updated abstract.'


class TestContracts:

    def test_a_new_grant_is_linked(self, committing, existing, mapped_resource,
                                   session):
        from factories import make_contract
        contract = make_contract(session, contract_number='AGS-9990201')
        payload = action_for(existing, wire_resource(mapped_resource.xras_key,
                                                     '250000'))
        payload['grants'] = [{'grantNumber': 'AGS-9990201'}]

        handle_update(committing, payload)
        session.refresh(existing['project'])          # the relationship was loaded pre-write
        assert contract.contract_id in {pc.contract_id
                                        for pc in existing['project'].contracts}

    def test_an_already_linked_contract_is_not_duplicated(self, committing, existing,
                                                          mapped_resource, session):
        from factories import make_contract, make_project_contract
        contract = make_contract(session, contract_number='AGS-9990202')
        make_project_contract(session, project=existing['project'],
                              contract=contract)
        session.refresh(existing['project'])
        payload = action_for(existing, wire_resource(mapped_resource.xras_key,
                                                     '250000'))
        payload['grants'] = [{'grantNumber': 'AGS-9990202'}]

        handle_update(committing, payload)
        session.refresh(existing['project'])
        links = [pc for pc in existing['project'].contracts
                 if pc.contract_id == contract.contract_id]
        assert len(links) == 1


class TestTheRegistration:

    def test_the_handler_is_bound_to_the_update_service(self):
        import sam.xras.handlers  # noqa: F401
        from sam.xras.dispatch import registered_services
        assert 'update' in registered_services()

    def test_a_new_action_against_an_existing_project_reaches_update(
            self, committing, existing, mapped_resource):
        """⚠️ ``actionType: 'New'`` — the UWIS0071 shape. Only the database
        distinguishes this from an Add."""
        from sam.xras.dispatch import dispatch_action
        import sam.xras.handlers  # noqa: F401

        result = dispatch_action(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '250000')))
        assert result.status == 'processed'
        assert result.service == 'update'

    def test_a_renewal_reaches_it_too(self, committing, existing, mapped_resource):
        from sam.xras.dispatch import dispatch_action
        import sam.xras.handlers  # noqa: F401

        result = dispatch_action(committing, action_for(
            existing, wire_resource(mapped_resource.xras_key, '250000'),
            actionType='Renewal'))
        assert result.service == 'update'
