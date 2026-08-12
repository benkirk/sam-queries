"""Supplement — and the one semantic that would destroy allocations if read wrong.

``awardedAmount`` is the **increment**, not the new total. ``SUPPLEMENT`` replays as
``addAmount(transaction_amount)``. Reading it as absolute would overwrite a
multi-million-hour allocation with a quarter-million-hour supplement, silently, and
the resulting number would look entirely plausible.
:meth:`TestItIsAdditive.test_the_amount_is_added_not_assigned` is the guard.

The other trap is the asymmetry with Extension: this walks ``resources[]`` and looks
accounts up **unfiltered**, so a supplement lands where an extension would skip.

See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *Supplement*.
"""

import json
from datetime import datetime, timedelta

import pytest

from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
    replay_amount,
)
from sam.xras.errors import ActionErrors, XrasActionRejected
from sam.xras.handlers._allocations import (
    account_for_resource,
    auth_at_panel_meeting,
    new_allocation_end_date,
)
from sam.xras.handlers._fields import (
    resolve_resource,
    resource_comment,
    transaction_amount,
)
from sam.xras.handlers.supplement import handle_supplement

from xras_helpers import load_fixture, txns_for, wire_resource
from xras_helpers import committing  # noqa: F401  — pytest resolves it by name

pytestmark = pytest.mark.unit


def action_for(projcode, *resources, allocation_type='Small'):
    return {'actionType': 'Supplement', 'requestNumber': projcode,
            'allocationType': allocation_type, 'resources': list(resources),
            'roles': []}


@pytest.fixture
def mapped_resource(session):
    """A resource carrying an ``xras_resource_repository_key_resource`` row.

    Only 13 such rows exist in production and 11 active resources have none,
    so the unmapped case the tests below exercise is a live failure mode
    rather than a defensive branch.
    """
    from factories import make_xras_key_mapping
    return make_xras_key_mapping(session)


# ---------------------------------------------------------------------------
# The pieces.
# ---------------------------------------------------------------------------


class TestResolveResource:

    def test_a_mapped_key_resolves(self, session, mapped_resource):
        errs = ActionErrors()
        resolved = resolve_resource(
            session, wire_resource(mapped_resource.xras_key), errs)
        assert not errs
        assert resolved.resource_id == mapped_resource.resource_id

    def test_an_unmapped_key_reports_the_key_variant(self, session):
        """Not the *name* variant — the roster path has its own string, and both can
        fire for one action."""
        errs = ActionErrors()
        assert resolve_resource(session, wire_resource(999_999), errs) is None
        assert list(errs) == [
            'No resource found in SAM corresponding to key 999999']

    def test_two_unmapped_resources_with_the_same_key_report_once(self, session):
        """Legacy calls ``getResourceName`` twice per resource on some paths, so the
        accumulator's dedup is load-bearing here rather than incidental."""
        errs = ActionErrors()
        resolve_resource(session, wire_resource(999_999), errs)
        resolve_resource(session, wire_resource(999_999), errs)
        assert len(errs) == 1


class TestTransactionAmount:

    @pytest.mark.parametrize('raw', [None, '', '  '])
    def test_a_blank_amount_reports_rather_than_raising(self, raw):
        """⚠️ The declared divergence. Legacy's ``> 0`` test unboxes the null ``Float``
        and throws an NPE *inside* assembly, so ``throwExceptionIfErrors`` never runs
        and the operator gets a bare stack-trace class name instead of this string."""
        errs = ActionErrors()
        assert transaction_amount(wire_resource(1, raw), errs) is None
        assert list(errs) == ['Awarded amount missing']

    def test_an_unparseable_amount_keeps_the_double_space(self, errs=None):
        errs = ActionErrors()
        assert transaction_amount(wire_resource(1, '1e9x'), errs) is None
        assert list(errs) == ['Could not convert awarded amount "1e9x"  to float']

    def test_three_resources_missing_an_amount_report_once(self):
        """The accumulator behaviour most likely to be got wrong, in the place it
        actually fires."""
        errs = ActionErrors()
        for _ in range(3):
            transaction_amount(wire_resource(1, ''), errs)
        assert list(errs) == ['Awarded amount missing']

    @pytest.mark.parametrize('raw,expected', [
        ('250000', 250_000.0), ('250000.5', 250_000.5), ('-100', -100.0),
        (' 1000 ', 1000.0),
    ])
    def test_valid_amounts_parse(self, raw, expected):
        errs = ActionErrors()
        assert transaction_amount(wire_resource(1, raw), errs) == expected
        assert not errs


class TestResourceComment:

    def test_a_blank_comment_becomes_none(self):
        assert resource_comment(wire_resource(1, comments='   ')) is None
        assert resource_comment(wire_resource(1, comments=None)) is None

    def test_a_comment_is_normalized_like_a_username(self):
        assert resource_comment(wire_resource(1, comments='  Café award ')) == 'Cafe award'


class TestAuthAtPanelMeeting:
    """⚠️ The branch is inverted: a payload *with* an ``allocationType`` runs the
    strategy chain; one *without* reads the existing project's stored type."""

    @pytest.mark.parametrize('allocation_type,expected', [
        ('Large', True),        # → CHAP
        ('CSL', True),
        ('Small', False),
        ('Data Analysis', False),
    ])
    def test_the_wire_type_runs_the_strategy_chain(self, session, allocation_type,
                                                   expected):
        action = action_for('NOSUCH', allocation_type=allocation_type)
        assert auth_at_panel_meeting(session, action) is expected

    def test_with_no_wire_type_the_projects_stored_type_decides(self, session):
        from factories import make_project
        from sam.accounting.allocations import AllocationType
        chap = (session.query(AllocationType)
                .filter(AllocationType.allocation_type == 'CHAP').first())
        project = make_project(session)
        project.allocation_type_id = chap.allocation_type_id
        session.flush()

        action = action_for(project.projcode, allocation_type=None)
        assert auth_at_panel_meeting(session, action) is True

    def test_a_non_panel_stored_type_is_false(self, session):
        from factories import make_project
        project = make_project(session)
        action = action_for(project.projcode, allocation_type=None)
        assert auth_at_panel_meeting(session, action) is False


class TestAccountLookupIsUnfiltered:
    """⚠️ The asymmetry with Extension. ``Project.getAccount(name)`` scans **all**
    accounts, so a supplement lands where an extension would skip."""

    def test_a_decommissioned_resources_account_is_still_found(self, session):
        from factories import make_account, make_project, make_resource
        project = make_project(session)
        resource = make_resource(session)
        account = make_account(session, project=project, resource=resource)
        resource.decommission_date = datetime.now() - timedelta(days=1)
        session.flush()
        session.refresh(project)

        assert account_for_resource(project, resource) is account

    def test_an_inactive_projects_account_is_still_found(self, session):
        from factories import make_account, make_project, make_resource
        project = make_project(session, active=False)
        resource = make_resource(session)
        account = make_account(session, project=project, resource=resource)
        session.refresh(project)
        assert account_for_resource(project, resource) is account

    def test_the_match_is_on_resource_name_case_insensitively(self, session):
        """``Account.isForResource`` uses ``equalsIgnoreCase``, so this is a name join,
        not an id join. Matched case-insensitively to agree with legacy."""
        from factories import make_account, make_project, make_resource
        project = make_project(session)
        resource = make_resource(session, resource_name='MixedCaseRes')
        make_account(session, project=project, resource=resource)
        session.refresh(project)

        # A differently-cased *query* must still hit. Not persisted — see below for
        # why a second row could not exist anyway.
        class _Probe:
            resource_name = 'MIXEDCASERES'

        assert account_for_resource(project, _Probe()) is not None

    def test_the_name_join_cannot_be_ambiguous_because_the_index_folds_case(
            self, session):
        """The reassuring half of the previous test, and the reason a name join is
        safe here at all.

        A name join *could* pick the wrong account if two resources differed only by
        case — but ``resources.resources_name_unique_idx`` is a unique index on a
        case-insensitive collation, so the database refuses to create the second row.
        Asserted rather than assumed, because the whole argument for keeping legacy's
        name join rests on it.
        """
        from sqlalchemy.exc import IntegrityError
        from factories import make_resource
        make_resource(session, resource_name='CollisionProbe')
        with pytest.raises(IntegrityError):
            make_resource(session, resource_name='collisionprobe')
        session.rollback()

    def test_no_account_for_the_resource_yields_none(self, session):
        from factories import make_project, make_resource
        assert account_for_resource(make_project(session),
                                    make_resource(session)) is None


class TestNewAllocationEndDate:
    """The create branch's window: contract end, then allocation end, then nothing."""

    def test_the_latest_contract_end_wins(self, session):
        from factories import make_contract, make_project, make_project_contract
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2029, 6, 30)))
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2031, 6, 30)))
        session.refresh(project)

        assert new_allocation_end_date(project, datetime.now()) == datetime(
            2031, 6, 30, 23, 59, 59)

    def test_a_past_contract_end_falls_through_to_allocations(self, session):
        from factories import (make_account, make_allocation, make_contract,
                               make_project, make_project_contract)
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2020, 1, 31)))
        make_allocation(session, account=make_account(session, project=project),
                        end_date=datetime(2030, 6, 30))
        session.refresh(project)

        assert new_allocation_end_date(project, datetime.now()) == datetime(
            2030, 6, 30, 23, 59, 59)

    def test_everything_past_yields_none(self, session):
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        make_allocation(session, account=make_account(session, project=project),
                        start_date=datetime(2019, 1, 1),
                        end_date=datetime(2020, 1, 31))
        session.refresh(project)
        assert new_allocation_end_date(project, datetime.now()) is None

    def test_a_bare_project_yields_none(self, session):
        from factories import make_project
        assert new_allocation_end_date(make_project(session), datetime.now()) is None


# ---------------------------------------------------------------------------
# The handler.
# ---------------------------------------------------------------------------


class TestItIsAdditive:

    def test_the_amount_is_added_not_assigned(self, committing, mapped_resource):
        """⚠️ **The test this file exists for.** ``update_allocation`` sets ``amount``;
        a supplement must add to it. Getting this wrong would silently shrink a
        4,000,000-hour allocation to 250,000 and look entirely plausible."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=4_000_000.0,
            account=make_account(session, project=project,
                                 resource=mapped_resource))
        session.refresh(project)

        handle_supplement(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '250000')))

        assert allocation.amount == pytest.approx(4_250_000.0)

    def test_the_transaction_carries_the_increment_not_the_total(
            self, committing, mapped_resource):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=4_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        session.refresh(project)

        handle_supplement(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '250000')))

        row = [t for t in txns_for(session, allocation)
               if t.transaction_type == AllocationTransactionType.SUPPLEMENT][0]
        assert row.transaction_amount == pytest.approx(250_000.0)

    def test_the_replay_invariant_holds_after_supplementing(self, committing,
                                                            mapped_resource):
        """``replay_amount`` *adds* ``transaction_amount`` on SUPPLEMENT, so the
        increment convention and the replay convention have to agree — asserted as a
        delta, since the factory seeds no NEW row."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        session.refresh(project)

        before = replay_amount(txns_for(session, allocation))
        handle_supplement(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '250000')))
        after = replay_amount(txns_for(session, allocation))

        assert after - before == pytest.approx(250_000.0)

    def test_two_supplements_accumulate(self, committing, mapped_resource):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        session.refresh(project)

        payload = action_for(project.projcode,
                             wire_resource(mapped_resource.xras_key, '100000'))
        handle_supplement(session, payload)
        handle_supplement(session, payload)
        assert allocation.amount == pytest.approx(1_200_000.0)


class TestTheRowsItWrites:
    """Measured against the 3,203 integration-written SUPPLEMENT rows in production."""

    def _supplement_row(self, session, committing, mapped_resource, **kw):
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        session.refresh(project)
        handle_supplement(session, action_for(
            project.projcode,
            wire_resource(mapped_resource.xras_key, '250000', **kw)))
        return [t for t in txns_for(session, allocation)
                if t.transaction_type == AllocationTransactionType.SUPPLEMENT][0]

    def test_the_date_columns_are_null(self, committing, mapped_resource):
        row = self._supplement_row(committing, committing, mapped_resource)
        assert row.alloc_start_date is None
        assert row.alloc_end_date is None

    def test_requested_amount_is_null(self, committing, mapped_resource):
        """``buildSupplementAllocationCommand`` never calls ``.requestedAmount(...)``.
        2,752 of the 3,203 production rows agree."""
        row = self._supplement_row(committing, committing, mapped_resource)
        assert row.requested_amount is None

    def test_the_actor_is_null(self, committing, mapped_resource):
        row = self._supplement_row(committing, committing, mapped_resource)
        assert row.user_id is None

    def test_the_comment_comes_from_the_wire(self, committing, mapped_resource):
        row = self._supplement_row(committing, committing, mapped_resource,
                                   comments='Supplemental award 2026')
        assert row.transaction_comment == 'Supplemental award 2026'

    def test_a_panel_authorised_type_sets_the_flag(self, committing, mapped_resource):
        """Set on 1,264 of the 3,203 production rows, so it is not vestigial."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        session.refresh(project)

        handle_supplement(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '250000'),
            allocation_type='Large'))
        row = [t for t in txns_for(session, allocation)
               if t.transaction_type == AllocationTransactionType.SUPPLEMENT][0]
        assert row.auth_at_panel_mtg is True


class TestTheCreateBranch:

    def test_a_resource_with_no_allocation_gets_one(self, committing, mapped_resource):
        session = committing
        from factories import make_contract, make_project, make_project_contract
        from sam.accounting.allocations import Allocation
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2031, 6, 30)))
        session.refresh(project)

        handle_supplement(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '750000')))

        created = (session.query(Allocation)
                   .join(Allocation.account)
                   .filter_by(project_id=project.project_id).all())
        assert len(created) == 1
        assert created[0].amount == pytest.approx(750_000.0)

    def test_it_uses_today_and_the_contract_end_not_the_actions_dates(
            self, committing, mapped_resource):
        """⚠️ Kept bug-for-bug: the create branch never reads ``actionBeginDate`` or
        ``actionEndDate``. A Supplement that creates an allocation gets dates XRAS did
        not ask for."""
        session = committing
        from factories import make_contract, make_project, make_project_contract
        from sam.accounting.allocations import Allocation
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2031, 6, 30)))
        session.refresh(project)

        payload = action_for(project.projcode,
                             wire_resource(mapped_resource.xras_key, '750000'))
        payload['actionBeginDate'] = '2020-01-01'
        payload['actionEndDate'] = '2020-12-31'
        handle_supplement(session, payload)

        created = (session.query(Allocation).join(Allocation.account)
                   .filter_by(project_id=project.project_id).one())
        assert created.start_date.date() == datetime.now().date()
        assert created.end_date == datetime(2031, 6, 30, 23, 59, 59)

    def test_no_usable_end_date_reports_and_writes_nothing(self, committing,
                                                           mapped_resource):
        session = committing
        from factories import make_project
        from sam.accounting.allocations import Allocation
        project = make_project(session)

        with pytest.raises(XrasActionRejected) as exc:
            handle_supplement(session, action_for(
                project.projcode, wire_resource(mapped_resource.xras_key, '750000')))

        assert exc.value.messages == [
            f'All contract and allocation end dates are null or past for '
            f'project [{project.projcode}]']
        assert not (session.query(Allocation).join(Allocation.account)
                    .filter_by(project_id=project.project_id).all())


class TestRejectionAndAccumulation:

    def test_an_unmapped_key_aborts_the_whole_action(self, committing,
                                                     mapped_resource):
        """One bad resource kills the supplement — the good one must not be applied."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        session.refresh(project)

        with pytest.raises(XrasActionRejected):
            handle_supplement(session, action_for(
                project.projcode,
                wire_resource(mapped_resource.xras_key, '250000'),
                wire_resource(999_999, '100000')))

        assert allocation.amount == pytest.approx(1_000_000.0)

    def test_problems_across_resources_accumulate_into_one_422(self, committing):
        session = committing
        from factories import make_project
        project = make_project(session)

        with pytest.raises(XrasActionRejected) as exc:
            handle_supplement(session, action_for(
                project.projcode,
                wire_resource(999_998, '100000'),
                wire_resource(999_999, ''),
            ))
        assert set(exc.value.messages) == {
            'No resource found in SAM corresponding to key 999998',
            'No resource found in SAM corresponding to key 999999',
        }

    def test_a_non_positive_amount_is_dropped_silently_as_legacy_does(
            self, committing, mapped_resource, caplog):
        """Legacy's ``> 0`` gate returns null with no report, so the action succeeds
        having done nothing. Logged rather than reported, so triage can see it."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        session.refresh(project)

        with caplog.at_level('WARNING'):
            result = handle_supplement(session, action_for(
                project.projcode,
                wire_resource(mapped_resource.xras_key, '-500')))

        assert result.status == 'processed'
        assert allocation.amount == pytest.approx(1_000_000.0)
        assert 'non-positive' in caplog.text

    def test_an_empty_resources_array_is_a_no_op_success(self, committing):
        session = committing
        from factories import make_project
        project = make_project(session)
        result = handle_supplement(session, action_for(project.projcode))
        assert result.status == 'processed'


class TestTheSubtree:

    def test_children_receive_the_same_increment(self, committing, mapped_resource):
        """``Allocation.supplement`` walks the tree, so every inheriting child gets the
        increment and its own row."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        parent = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        child = make_allocation(
            session, amount=200_000.0, parent=parent,
            account=make_account(session, project=make_project(session),
                                 resource=mapped_resource))
        session.refresh(project)

        handle_supplement(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '250000')))

        assert parent.amount == pytest.approx(1_250_000.0)
        assert child.amount == pytest.approx(450_000.0)

    def test_child_rows_are_not_marked_propagated(self, committing, mapped_resource):
        """Zero of the 3,203 production rows carry the flag."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        parent = make_allocation(
            session, amount=1_000_000.0,
            account=make_account(session, project=project, resource=mapped_resource))
        child = make_allocation(
            session, amount=200_000.0, parent=parent,
            account=make_account(session, project=make_project(session),
                                 resource=mapped_resource))
        session.refresh(project)

        handle_supplement(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '250000')))
        rows = [t for t in txns_for(session, child)
                if t.transaction_type == AllocationTransactionType.SUPPLEMENT]
        assert rows and all(not r.propagated for r in rows)


class TestTheRegistration:

    def test_the_handler_is_bound(self):
        import sam.xras.handlers  # noqa: F401
        from sam.xras.dispatch import registered_services
        assert {'extend', 'supplement'} <= registered_services()

    def test_a_corpus_supplement_reaches_it_through_the_dispatcher(
            self, committing, mapped_resource):
        session = committing
        from factories import make_account, make_allocation, make_project
        from sam.xras.dispatch import dispatch_action
        import sam.xras.handlers  # noqa: F401

        data = load_fixture('supplement_ubrn0027_ok.json')
        project = make_project(session, projcode='ZZRN0027')
        make_allocation(session, amount=500_000.0,
                        account=make_account(session, project=project,
                                             resource=mapped_resource))
        session.refresh(project)

        payload = dict(data, requestNumber='ZZRN0027',
                       resources=[wire_resource(mapped_resource.xras_key, '90000')])
        result = dispatch_action(session, payload)
        assert result.status == 'processed'
        assert result.service == 'supplement'
