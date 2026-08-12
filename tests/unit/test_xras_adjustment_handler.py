"""Adjustment — the handler with no production outcome to diff against.

``AdjustProjectActionService`` has never serviced a single action, for two independent
reasons: it tests ``actionType.equals("Adjust")`` while XRAS sends ``"Adjustment"``
(defect 4), and it carries a copy-pasted ``> 0`` gate that drops the negatives an
adjustment exists for. So everything asserted here is reasoned from the Java rather
than confirmed against behaviour, and the two divergences below are the ones to argue
with:

* negatives are honoured — the point of the handler
* an adjustment that would take an allocation **below zero** is rejected, which legacy
  does not do because legacy never applies one

See ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *Adjustment*.
"""

import json
from datetime import datetime

import pytest

from sam.accounting.allocations import (
    AllocationTransaction,
    AllocationTransactionType,
    replay_amount,
)
from sam.xras.errors import XrasActionRejected
from sam.xras.handlers.adjustment import handle_adjustment

from xras_helpers import load_fixture, txns_for, wire_resource
from xras_helpers import committing  # noqa: F401  — pytest resolves it by name

pytestmark = pytest.mark.unit


def action_for(projcode, *resources, action_type='Adjustment',
               allocation_type='Small'):
    """``allocationType`` defaults to ``'Small'``, which is **not** panel-authorised.

    That default is load-bearing for every test below that does not name one — see
    ``TestTheRowShape.test_auth_at_panel_mtg_is_not_set``. ``'Large'`` resolves through
    ``LargeStrategy`` to the ``CHAP`` type and *is* panel-authorised.
    """
    return {'actionType': action_type, 'requestNumber': projcode,
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


@pytest.fixture
def allocated(session, mapped_resource):
    """A project with a 1,000,000-unit allocation on the mapped resource."""
    from factories import make_account, make_allocation, make_project
    project = make_project(session)
    allocation = make_allocation(
        session, amount=1_000_000.0,
        account=make_account(session, project=project, resource=mapped_resource))
    session.refresh(project)
    return project, allocation


class TestNegativesAreHonoured:
    """The divergence that is the entire purpose of the handler."""

    def test_a_negative_amount_reduces_the_allocation(self, committing, allocated,
                                                      mapped_resource):
        """Legacy's ``> 0`` gate drops this silently — the one thing an adjustment is
        for. Nothing depends on that gate, because nothing has ever run."""
        session = committing
        project, allocation = allocated

        result = handle_adjustment(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '-250000')))

        assert result.status == 'processed'
        assert allocation.amount == pytest.approx(750_000.0)

    def test_a_positive_amount_still_increases_it(self, committing, allocated,
                                                  mapped_resource):
        session = committing
        project, allocation = allocated
        handle_adjustment(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '250000')))
        assert allocation.amount == pytest.approx(1_250_000.0)

    def test_the_transaction_carries_the_signed_delta(self, committing, allocated,
                                                      mapped_resource):
        session = committing
        project, allocation = allocated
        handle_adjustment(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '-250000')))

        row = [t for t in txns_for(session, allocation)
               if t.transaction_type == AllocationTransactionType.ADJUSTMENT][0]
        assert row.transaction_amount == pytest.approx(-250_000.0)

    def test_the_replay_invariant_tracks_the_signed_delta(self, committing, allocated,
                                                          mapped_resource):
        """``replay_amount`` *adds* ``transaction_amount`` on ADJUSTMENT, so a negative
        row must move replay down by exactly the same amount the allocation moved."""
        session = committing
        project, allocation = allocated

        before = replay_amount(txns_for(session, allocation))
        handle_adjustment(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '-250000')))
        after = replay_amount(txns_for(session, allocation))

        assert after - before == pytest.approx(-250_000.0)

    def test_an_adjustment_to_exactly_zero_is_allowed(self, committing, allocated,
                                                      mapped_resource):
        """Reducing an allocation to nothing is legitimate — a project whose award was
        withdrawn. Only *below* zero is refused."""
        session = committing
        project, allocation = allocated
        handle_adjustment(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '-1000000')))
        assert allocation.amount == pytest.approx(0.0)


class TestTheBelowZeroGuard:
    """⚠️ A guard legacy does not have. ``verifyValidateState`` checks only the end
    date, so nothing in legacy stops an allocation going negative — it has simply never
    applied one."""

    def test_an_over_large_negative_is_rejected(self, committing, allocated,
                                                mapped_resource):
        session = committing
        project, allocation = allocated

        with pytest.raises(XrasActionRejected) as exc:
            handle_adjustment(session, action_for(
                project.projcode,
                wire_resource(mapped_resource.xras_key, '-1000001')))

        assert exc.value.messages == [
            f'Adjustment of -1,000,001.00 for {mapped_resource.resource_name} would '
            f'take the allocation below zero (currently 1,000,000.00)']

    def test_the_message_does_not_round_the_two_numbers_together(self, committing,
                                                                 allocated,
                                                                 mapped_resource):
        """⚠️ The reason this string uses ``,.2f`` and not ``g``. Six significant
        digits rendered -1,000,001 as ``-1e+06``, so the message claimed a number
        *equal* to the balance would take it below zero. An operator has to be able to
        see which number is which."""
        session = committing
        project, _ = allocated
        with pytest.raises(XrasActionRejected) as exc:
            handle_adjustment(session, action_for(
                project.projcode,
                wire_resource(mapped_resource.xras_key, '-1000001')))
        message = exc.value.messages[0]
        assert 'e+' not in message
        assert '-1,000,001.00' in message and '1,000,000.00' in message

    def test_nothing_is_written_when_it_is_rejected(self, committing, allocated,
                                                    mapped_resource):
        """It can only reject, never corrupt — and a rejected Adjustment goes to a
        human, which is where 100% of them go today."""
        session = committing
        project, allocation = allocated

        with pytest.raises(XrasActionRejected):
            handle_adjustment(session, action_for(
                project.projcode,
                wire_resource(mapped_resource.xras_key, '-2000000')))

        assert allocation.amount == pytest.approx(1_000_000.0)
        assert not [t for t in txns_for(session, allocation)
                    if t.transaction_type == AllocationTransactionType.ADJUSTMENT]

    def test_one_bad_resource_aborts_the_whole_action(self, committing, allocated,
                                                      mapped_resource, session):
        session = committing
        project, allocation = allocated
        from factories import make_account, make_allocation, make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource

        second = make_resource(session)
        second_key = 920_000 + second.resource_id
        session.add(XrasResourceRepositoryKeyResource(
            resource_repository_key=second_key, resource_id=second.resource_id))
        other = make_allocation(
            session, amount=500_000.0,
            account=make_account(session, project=project, resource=second))
        session.flush()
        session.refresh(project)

        with pytest.raises(XrasActionRejected):
            handle_adjustment(session, action_for(
                project.projcode,
                wire_resource(mapped_resource.xras_key, '-100000'),
                wire_resource(second_key, '-9000000'),
            ))

        assert allocation.amount == pytest.approx(1_000_000.0)
        assert other.amount == pytest.approx(500_000.0)

    def test_a_zero_amount_is_a_logged_no_op(self, committing, allocated,
                                             mapped_resource, caplog):
        session = committing
        project, allocation = allocated
        with caplog.at_level('WARNING'):
            result = handle_adjustment(session, action_for(
                project.projcode, wire_resource(mapped_resource.xras_key, '0')))
        assert result.status == 'processed'
        assert allocation.amount == pytest.approx(1_000_000.0)
        assert 'zero' in caplog.text


class TestTheRowShape:
    """Adjustment's row differs from Supplement's in two ways, both from the Java."""

    def _row(self, session, project, allocation, mapped_resource, **kw):
        handle_adjustment(session, action_for(
            project.projcode,
            wire_resource(mapped_resource.xras_key, '250000', **kw)))
        return [t for t in txns_for(session, allocation)
                if t.transaction_type == AllocationTransactionType.ADJUSTMENT][0]

    def test_the_type_is_adjustment_not_supplement(self, committing, allocated,
                                                   mapped_resource):
        project, allocation = allocated
        row = self._row(committing, project, allocation, mapped_resource)
        assert row.transaction_type == 'ADJUSTMENT'

    def test_auth_at_panel_mtg_is_not_set(self, committing, allocated,
                                          mapped_resource):
        """``buildAdjustAllocationCommand`` never calls ``.authAtPanelMeeting(...)``,
        where the supplement one does. The difference is the Java's, not a slip."""
        project, allocation = allocated
        row = self._row(committing, project, allocation, mapped_resource)
        assert not row.auth_at_panel_mtg

    def test_auth_at_panel_mtg_is_null_not_zero(self, committing, allocated,
                                                mapped_resource):
        """⚠️ ``is None``, deliberately — ``not row.auth_at_panel_mtg`` above cannot
        tell NULL from 0 and this is the difference that matters.

        ``log_integration_transaction`` sets the column only ``if auth_at_panel_mtg
        is not None``, so Adjustment gets NULL by **passing nothing**. Legacy writes
        NULL here; a caller that passed ``False`` to be explicit would write 0 and
        the two are different bytes on an audit row.

        This is the invariant a shared helper between ``supplement_allocation`` and
        ``adjust_allocation`` is most likely to break, and nothing pinned it before.
        """
        project, allocation = allocated
        row = self._row(committing, project, allocation, mapped_resource)
        assert row.auth_at_panel_mtg is None

    def test_the_informational_columns_are_null(self, committing, allocated,
                                                mapped_resource):
        project, allocation = allocated
        row = self._row(committing, project, allocation, mapped_resource)
        assert row.requested_amount is None
        assert row.alloc_start_date is None
        assert row.alloc_end_date is None
        assert row.user_id is None

    def test_the_comment_comes_from_the_wire(self, committing, allocated,
                                             mapped_resource):
        project, allocation = allocated
        row = self._row(committing, project, allocation, mapped_resource,
                        comments='Correcting a double-charge')
        assert row.transaction_comment == 'Correcting a double-charge'


class TestSharedWithSupplement:
    """The pieces imported rather than copied. Spot-checked, not re-tested in full."""

    def test_an_unmapped_key_reports_the_same_string(self, committing, allocated):
        session = committing
        project, _ = allocated
        with pytest.raises(XrasActionRejected) as exc:
            handle_adjustment(session, action_for(
                project.projcode, wire_resource(999_997, '100')))
        assert exc.value.messages == [
            'No resource found in SAM corresponding to key 999997']

    def test_a_blank_amount_reports_rather_than_raising(self, committing, allocated,
                                                        mapped_resource):
        session = committing
        project, _ = allocated
        with pytest.raises(XrasActionRejected) as exc:
            handle_adjustment(session, action_for(
                project.projcode, wire_resource(mapped_resource.xras_key, '')))
        assert exc.value.messages == ['Awarded amount missing']

    def test_the_create_branch_still_applies(self, committing, mapped_resource,
                                             session):
        """A resource with no allocation is created, exactly as Supplement does."""
        from factories import make_contract, make_project, make_project_contract
        from sam.accounting.allocations import Allocation
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2031, 6, 30)))
        session.refresh(project)

        handle_adjustment(committing, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '400000')))

        created = (session.query(Allocation).join(Allocation.account)
                   .filter_by(project_id=project.project_id).one())
        assert created.amount == pytest.approx(400_000.0)

    def test_a_panel_authorised_create_sets_the_flag(self, committing,
                                                     mapped_resource, session):
        """The create branch is Supplement's, so it must mark the CREATE row too.

        ⚠️ Do not read this against ``test_auth_at_panel_mtg_is_not_set``, which is
        about a *different command*. Legacy's adjust factory is a near-verbatim copy of
        the supplement one: ``buildAdjustAllocationCommand`` never calls
        ``.authAtPanelMeeting(...)`` — hence the ADJUSTMENT row's bare flag — but
        ``buildAddAllocationCommand``, which the copy also carries, does.

        The bug this pins: ``auth`` was computed, threaded through the creations tuple
        and unpacked, and then never applied. It was invisible because every other
        Adjustment test uses the default ``allocationType='Small'``, which is not
        panel-authorised, so the flag was ``False`` either way.
        """
        from factories import make_contract, make_project, make_project_contract
        from sam.accounting.allocations import Allocation
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2031, 6, 30)))
        session.refresh(project)

        handle_adjustment(committing, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '400000'),
            allocation_type='Large'))

        created = (session.query(Allocation).join(Allocation.account)
                   .filter_by(project_id=project.project_id).one())
        row = [t for t in txns_for(session, created)
               if t.transaction_type == AllocationTransactionType.NEW][0]
        assert row.auth_at_panel_mtg is True

    def test_a_non_panel_type_leaves_the_created_row_unmarked(
            self, committing, mapped_resource, session):
        """The other half of the pair — the flag tracks the type, not the branch."""
        from factories import make_contract, make_project, make_project_contract
        from sam.accounting.allocations import Allocation
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2031, 6, 30)))
        session.refresh(project)

        handle_adjustment(committing, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '400000')))

        created = (session.query(Allocation).join(Allocation.account)
                   .filter_by(project_id=project.project_id).one())
        row = [t for t in txns_for(session, created)
               if t.transaction_type == AllocationTransactionType.NEW][0]
        assert not row.auth_at_panel_mtg

    def test_a_negative_create_is_rejected_rather_than_attempted(
            self, committing, mapped_resource, session):
        """There is nothing to create with a negative amount. Legacy would build the
        command and fail downstream on ``Allocation.create``'s ``amount > 0``
        validation; reporting is the legible version of the same refusal."""
        from factories import make_contract, make_project, make_project_contract
        project = make_project(session)
        make_project_contract(session, project=project, contract=make_contract(
            session, end_date=datetime(2031, 6, 30)))
        session.refresh(project)

        with pytest.raises(XrasActionRejected) as exc:
            handle_adjustment(committing, action_for(
                project.projcode, wire_resource(mapped_resource.xras_key, '-100')))
        assert 'below zero' in exc.value.messages[0]


class TestBothSpellingsReachIt:

    def test_the_handler_is_bound_to_the_adjust_service(self):
        import sam.xras.handlers  # noqa: F401
        from sam.xras.dispatch import registered_services
        assert 'adjust' in registered_services()

    @pytest.mark.parametrize('spelling', ['Adjust', 'Adjustment'])
    def test_the_dispatcher_routes_both(self, committing, allocated, mapped_resource,
                                        spelling):
        """Defect 4: legacy compares ``"Adjust"`` against a wire that sends
        ``"Adjustment"``, so its handler has never fired. Accepting both is what makes
        this reachable at all."""
        session = committing
        from sam.xras.dispatch import dispatch_action
        import sam.xras.handlers  # noqa: F401
        project, allocation = allocated

        result = dispatch_action(session, action_for(
            project.projcode, wire_resource(mapped_resource.xras_key, '1000'),
            action_type=spelling))
        assert result.status == 'processed'
        assert result.service == 'adjust'

    def test_the_corpus_adjustment_uses_the_spelling_legacy_cannot_match(self):
        data = load_fixture('adjustment_uwis0064_manual.json')
        assert data['actionType'] == 'Adjustment'
