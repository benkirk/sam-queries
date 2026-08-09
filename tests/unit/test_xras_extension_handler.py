"""Extension — the highest-volume handler, and the one whose inputs are almost nothing.

Two facts drive every test below, and both are counter-intuitive enough that a
reasonable implementation gets them wrong:

1. **``resources[]`` is ignored.** Legacy walks the project's *accounts*. Both corpus
   Extensions send ``resources: []`` and both extended real allocations, so a handler
   that iterated the array would be a no-op on 100% of observed Extension traffic —
   returning ``processed`` having written nothing.
2. **The roster and the title are never validated.** ``ExtendProjectAssembler``
   composes one factory. The corpus makes this look otherwise: both Extensions carry a
   populated ``roles[]`` that nothing reads.

UFSU0023 is the regression oracle for the failure path — a real production 422 with a
known-exact string.

See ``docs/plans/XRAS_SPRINT_C.md`` § *Extension*.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sam.accounting.allocations import (
    Allocation,
    AllocationTransaction,
    AllocationTransactionType,
    replay_amount,
)
from sam.xras.errors import ActionErrors, XrasActionRejected
from sam.xras.handlers._allocations import (
    account_is_active,
    effective_end_date,
    latest_allocation,
)
from sam.xras.handlers._fields import parse_action_end_date
from sam.xras.handlers.extension import EXTENSION_COMMENT, handle_extension

# noqa: F401 shim — Stage 4A. The body moved to tests/xras_helpers.py; this
# re-export keeps the suite passing UNEDITED, which is the proof the move was
# pure. Commit 4B repoints the imports and deletes every one of these.
from xras_helpers import FIXTURE_DIR, committing, load_fixture, txns_for  # noqa: F401

pytestmark = pytest.mark.unit





def action_for(projcode, end_date='2030-06-30', **extra):
    payload = {'actionType': 'Extension', 'requestNumber': projcode,
               'actionEndDate': end_date, 'resources': [], 'roles': []}
    payload.update(extra)
    return payload






# ---------------------------------------------------------------------------
# The date.
# ---------------------------------------------------------------------------


class TestParseActionEndDate:

    @pytest.mark.parametrize('raw', [None, '', '   '])
    def test_a_blank_date_reports_missing(self, raw):
        errs = ActionErrors()
        assert parse_action_end_date({'actionEndDate': raw}, errs) is None
        assert list(errs) == ['Missing end date for allocation(s)']

    @pytest.mark.parametrize('raw', ['2030-13-01', 'next tuesday', '06/30/2030'])
    def test_an_unparseable_date_reports_conversion(self, raw):
        """A *separate* string from the missing one. § 3.4 collapses the two into one
        slashed line, which is one of its seven errors."""
        errs = ActionErrors()
        assert parse_action_end_date({'actionEndDate': raw}, errs) is None
        assert list(errs) == ['Could not convert end date for allocation(s)']

    def test_a_valid_date_lands_at_end_of_day(self):
        """``getDateAtEndOfDay`` in legacy, the 23:59:59 convention in SAM — the two
        agree, which is why ``normalize_end_date`` can do the work."""
        errs = ActionErrors()
        result = parse_action_end_date({'actionEndDate': '2030-06-30'}, errs)
        assert not errs
        assert result == datetime(2030, 6, 30, 23, 59, 59)

    def test_every_corpus_extension_parses(self):
        for name in ('extension_ucub0166_ok.json', 'extension_ufsu0023_failed.json'):
            errs = ActionErrors()
            assert parse_action_end_date(load_fixture(name), errs) is not None
            assert not errs


# ---------------------------------------------------------------------------
# The account and allocation predicates.
# ---------------------------------------------------------------------------


class TestAccountIsActive:
    """``project.isActive() && resource.isCommissioned(now) && !creationTime.after(now)``
    — emphatically **not** ``Account.is_active``, which means "not deleted"."""

    def test_a_normal_account_is_active(self, session):
        from factories import make_account
        assert account_is_active(make_account(session), datetime.now())

    def test_an_inactive_project_disqualifies_its_accounts(self, session):
        from factories import make_account, make_project
        project = make_project(session, active=False)
        account = make_account(session, project=project)
        assert not account_is_active(account, datetime.now())

    def test_a_decommissioned_resource_disqualifies_its_account(self, session):
        from factories import make_account, make_resource
        resource = make_resource(session)
        resource.decommission_date = datetime.now() - timedelta(days=1)
        session.flush()
        account = make_account(session, resource=resource)
        assert not account_is_active(account, datetime.now())

    def test_a_resource_not_yet_commissioned_disqualifies_its_account(self, session):
        from factories import make_account, make_resource
        resource = make_resource(
            session, commission_date=datetime.now() + timedelta(days=30))
        account = make_account(session, resource=resource)
        assert not account_is_active(account, datetime.now())

    def test_a_recently_created_account_is_not_excluded_by_clock_skew(self, session):
        """⚠️ The conjunct legacy has and this port drops, and the reason why.

        ``account.creation_time`` comes from ``server_default=CURRENT_TIMESTAMP``, so
        it resolves in the **MySQL server's** timezone — UTC in the dev and CI
        containers — while ``now`` is naive-Mountain. Measured against this very
        container: ``NOW()`` is six hours ahead of ``datetime.now()``. Honouring
        ``!creationTime.after(now)`` would therefore skip every account created in the
        last six hours, so an Extension posted shortly after a New would report
        ``processed`` having written nothing.

        This test is the guard: a freshly-made account, whose ``creation_time`` is
        *already* in the future by the app clock, must still be extendable.
        """
        from factories import make_account
        account = make_account(session)

        # ⚠️ The skew is a property of the *environment*, not of the code, so it
        # cannot be asserted unconditionally. `account.creation_time` comes from
        # MySQL's `NOW()` (UTC in these containers); `datetime.now()` is the app
        # clock. On a naive-Mountain laptop those differ by six hours and the
        # account looks future-dated; in CI both are UTC and they agree to the
        # second. Asserting the skew exists passed on every developer machine and
        # failed on the runner — by 0.28 seconds.
        #
        # What the handler must do is the same either way: extend an account
        # created moments ago. That is the assertion below, and it holds in both
        # environments — which is the whole point of not porting the conjunct.
        if account.creation_time > datetime.now():
            skew = account.creation_time - datetime.now()
            assert skew > timedelta(minutes=1), (
                'creation_time is ahead of the app clock by less than a minute, '
                'which is neither the documented multi-hour timezone skew nor '
                'agreement — look at the container TZ before trusting this test')

        assert account_is_active(account, datetime.now())

    def test_the_soft_delete_check_is_ours_not_legacys(self, session):
        """Declared divergence: legacy has no equivalent. Unobservable in production
        (zero deleted accounts out of 17,989), but extending a deleted account would
        be wrong regardless."""
        from factories import make_account
        account = make_account(session)
        account.deleted = True
        session.flush()
        assert not account_is_active(account, datetime.now())


class TestEffectiveEndDate:
    """``Allocation.getEndDate()`` clamps by the resource's decommission date, and both
    the shrink test and the latest-allocation search go through it."""

    def test_with_no_decommission_it_is_the_stored_end(self, session):
        from factories import make_allocation
        allocation = make_allocation(session, end_date=datetime(2030, 6, 30))
        assert effective_end_date(allocation) == allocation.end_date

    def test_a_nearer_decommission_wins(self, session):
        from factories import make_account, make_allocation, make_resource
        resource = make_resource(session)
        account = make_account(session, resource=resource)
        allocation = make_allocation(session, account=account,
                                     end_date=datetime(2030, 6, 30))
        resource.decommission_date = datetime(2028, 1, 31)
        session.flush()
        assert effective_end_date(allocation) == resource.decommission_date

    def test_a_further_decommission_does_not(self, session):
        from factories import make_account, make_allocation, make_resource
        resource = make_resource(session)
        account = make_account(session, resource=resource)
        allocation = make_allocation(session, account=account,
                                     end_date=datetime(2030, 6, 30))
        resource.decommission_date = datetime(2032, 1, 31)
        session.flush()
        assert effective_end_date(allocation) == allocation.end_date

    def test_a_null_stored_end_reads_through_as_the_decommission_date(self, session):
        """The arm that surprises: an open-ended allocation on a resource with a
        scheduled decommission is *not* open-ended to legacy."""
        from factories import make_account, make_allocation, make_resource
        resource = make_resource(session)
        account = make_account(session, resource=resource)
        allocation = make_allocation(session, account=account)
        # The factory substitutes a default for end_date=None, so null it explicitly.
        allocation.end_date = None
        resource.decommission_date = datetime(2028, 1, 31)
        session.flush()
        assert effective_end_date(allocation) == resource.decommission_date


class TestLatestAllocation:

    def test_the_maximum_end_date_wins(self, session):
        from factories import make_account, make_allocation
        account = make_account(session)
        make_allocation(session, account=account, end_date=datetime(2027, 1, 31))
        latest = make_allocation(session, account=account,
                                 end_date=datetime(2030, 6, 30))
        make_allocation(session, account=account, end_date=datetime(2028, 1, 31))
        session.refresh(account)
        assert latest_allocation(account) is latest

    def test_a_null_effective_end_short_circuits_the_whole_search(self, session):
        """Legacy returns from inside the loop, so an open-ended allocation wins
        regardless of position — and regardless of a later, larger end date."""
        from factories import make_account, make_allocation
        account = make_account(session)
        open_ended = make_allocation(session, account=account)
        open_ended.end_date = None                 # the factory defaults a real date
        make_allocation(session, account=account, end_date=datetime(2099, 1, 31))
        session.flush()
        session.refresh(account)
        assert latest_allocation(account) is open_ended

    def test_an_account_with_no_allocations_yields_none(self, session):
        from factories import make_account
        assert latest_allocation(make_account(session)) is None


# ---------------------------------------------------------------------------
# The handler.
# ---------------------------------------------------------------------------


class TestHandleExtension:

    def test_it_extends_the_latest_allocation_of_every_active_account(
            self, committing):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        first = make_allocation(session, account=make_account(session, project=project),
                                end_date=datetime(2027, 1, 31))
        second = make_allocation(session,
                                 account=make_account(session, project=project),
                                 end_date=datetime(2028, 6, 30))

        result = handle_extension(session, action_for(project.projcode, '2030-06-30'))

        assert result.status == 'processed'
        assert result.projcode == project.projcode
        assert first.end_date == datetime(2030, 6, 30, 23, 59, 59)
        assert second.end_date == datetime(2030, 6, 30, 23, 59, 59)

    def test_the_resources_array_is_ignored_entirely(self, committing):
        """The corpus proves it: both Extensions send ``resources: []`` and both
        extended real allocations. A handler that iterated the array would write
        nothing here and still report success."""
        session = committing
        from factories import make_allocation, make_project, make_account
        project = make_project(session)
        allocation = make_allocation(session,
                                     account=make_account(session, project=project),
                                     end_date=datetime(2027, 1, 31))

        handle_extension(session, action_for(project.projcode, '2030-06-30',
                                             resources=[]))
        assert allocation.end_date == datetime(2030, 6, 30, 23, 59, 59)

    def test_an_inactive_projects_accounts_are_skipped(self, committing):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session, active=False)
        allocation = make_allocation(session,
                                     account=make_account(session, project=project),
                                     end_date=datetime(2027, 1, 31))

        result = handle_extension(session, action_for(project.projcode, '2030-06-30'))
        assert result.status == 'processed'
        assert allocation.end_date == datetime(2027, 1, 31, 23, 59, 59)

    def test_only_the_latest_allocation_per_account_moves(self, committing):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        account = make_account(session, project=project)
        older = make_allocation(session, account=account,
                                end_date=datetime(2026, 1, 31))
        latest = make_allocation(session, account=account,
                                 end_date=datetime(2027, 1, 31))

        handle_extension(session, action_for(project.projcode, '2030-06-30'))
        assert latest.end_date == datetime(2030, 6, 30, 23, 59, 59)
        assert older.end_date == datetime(2026, 1, 31, 23, 59, 59)


class TestTheShrinkRejection:
    """UFSU0023's failure mode, and the string an XRAS admin reads."""

    def test_a_shrink_is_rejected_with_the_existing_end_date_interpolated(
            self, committing):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        make_allocation(session, account=make_account(session, project=project),
                        end_date=datetime(2033, 7, 31))

        with pytest.raises(XrasActionRejected) as exc:
            handle_extension(session, action_for(project.projcode, '2027-09-30'))

        assert exc.value.messages == [
            'Action end date is before existing allocation end date (2033-07-31)']

    def test_it_reproduces_ufsu0023s_production_string_exactly(self, committing):
        """The regression oracle. UFSU0023 posted ``actionEndDate: 2027-09-30`` against
        an allocation ending 2033-07-31 and legacy answered with this line."""
        session = committing
        from factories import make_account, make_allocation, make_project
        data = load_fixture('extension_ufsu0023_failed.json')
        assert data['actionEndDate'] == '2027-09-30'

        project = make_project(session, projcode='ZZFS0023')
        make_allocation(session, account=make_account(session, project=project),
                        end_date=datetime(2033, 7, 31))
        data = dict(data, requestNumber='ZZFS0023')

        with pytest.raises(XrasActionRejected) as exc:
            handle_extension(session, data)
        assert exc.value.messages == [
            'Action end date is before existing allocation end date (2033-07-31)']

    def test_nothing_is_written_when_one_account_of_several_would_shrink(
            self, committing):
        """Legacy drops the bad allocation and carries on assembling, then aborts
        everything at ``throwExceptionIfErrors``. One bad account kills the extension —
        the good ones must not be half-applied."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        extendable = make_allocation(
            session, account=make_account(session, project=project),
            end_date=datetime(2027, 1, 31))
        make_allocation(session, account=make_account(session, project=project),
                        end_date=datetime(2033, 7, 31))

        with pytest.raises(XrasActionRejected):
            handle_extension(session, action_for(project.projcode, '2030-06-30'))

        assert extendable.end_date == datetime(2027, 1, 31, 23, 59, 59)
        assert not [t for t in txns_for(session, extendable)
                    if t.transaction_type == AllocationTransactionType.EXTENSION]

    def test_two_accounts_shrinking_to_the_same_date_report_once(self, committing):
        """The accumulator's dedup, in the place it actually fires."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        for _ in range(3):
            make_allocation(session, account=make_account(session, project=project),
                            end_date=datetime(2033, 7, 31))

        with pytest.raises(XrasActionRejected) as exc:
            handle_extension(session, action_for(project.projcode, '2027-09-30'))
        assert len(exc.value.messages) == 1

    def test_an_equal_end_date_is_not_a_shrink(self, committing):
        """``getEndDate().before(...)`` is **strictly** before, so equal passes and
        emits a no-op extend. A candidate explanation for the two successful posts in
        § 1.2 that mutated nothing."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, account=make_account(session, project=project),
            end_date=datetime(2030, 6, 30))

        result = handle_extension(session, action_for(project.projcode, '2030-06-30'))
        assert result.status == 'processed'
        assert allocation.end_date == datetime(2030, 6, 30, 23, 59, 59)
        assert not [t for t in txns_for(session, allocation)
                    if t.transaction_type == AllocationTransactionType.EXTENSION]

    def test_a_missing_date_rejects_before_touching_any_account(self, committing):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, account=make_account(session, project=project),
            end_date=datetime(2027, 1, 31))

        with pytest.raises(XrasActionRejected) as exc:
            handle_extension(session, action_for(project.projcode, None))
        assert exc.value.messages == ['Missing end date for allocation(s)']
        assert allocation.end_date == datetime(2027, 1, 31, 23, 59, 59)


class TestTheRowsItWrites:
    """Legacy's row shape, measured against 1,553 production rows."""

    def test_the_comment_is_the_leaked_java_class_name(self, committing):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, account=make_account(session, project=project),
            end_date=datetime(2027, 1, 31))

        handle_extension(session, action_for(project.projcode, '2030-06-30'))
        row = [t for t in txns_for(session, allocation)
               if t.transaction_type == AllocationTransactionType.EXTENSION][0]
        assert row.transaction_comment == 'XrasAction Extension Request'
        assert EXTENSION_COMMENT == 'XrasAction Extension Request'

    def test_the_amount_and_start_columns_are_null(self, committing):
        """All 1,553 production ``XrasAction Extension Request`` rows carry
        ``transaction_amount``, ``requested_amount`` and ``alloc_start_date`` NULL,
        with only ``alloc_end_date`` set. ``Allocation.extend_allocation`` writes a
        different shape, which is why this handler does not use it."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, account=make_account(session, project=project),
            end_date=datetime(2027, 1, 31))

        handle_extension(session, action_for(project.projcode, '2030-06-30'))
        row = [t for t in txns_for(session, allocation)
               if t.transaction_type == AllocationTransactionType.EXTENSION][0]
        assert row.transaction_amount is None
        assert row.requested_amount is None
        assert row.alloc_start_date is None
        assert row.alloc_end_date == datetime(2030, 6, 30, 23, 59, 59)

    def test_the_actor_is_null(self, committing):
        """The integration-actor convention — 25,048 production rows and counting."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, account=make_account(session, project=project),
            end_date=datetime(2027, 1, 31))

        handle_extension(session, action_for(project.projcode, '2030-06-30'))
        row = [t for t in txns_for(session, allocation)
               if t.transaction_type == AllocationTransactionType.EXTENSION][0]
        assert row.user_id is None

    def test_the_replay_invariant_is_untouched(self, committing):
        """The house invariant: every ``allocation_transaction`` write must keep
        ``replay(history) == amount``.

        An Extension changes no amount, so the correct assertion is that replay is
        **unchanged** — before equals after. ``replay_amount`` ignores
        ``transaction_amount`` on EXTENSION entirely, which is what makes nulling the
        column safe; this proves it rather than citing it. (Asserting equality with
        ``allocation.amount`` instead would test the *factory*, which does not seed a
        NEW row, not the handler.)
        """
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        allocation = make_allocation(
            session, account=make_account(session, project=project),
            amount=250_000.0, end_date=datetime(2027, 1, 31))

        before = replay_amount(txns_for(session, allocation))
        handle_extension(session, action_for(project.projcode, '2030-06-30'))
        after = replay_amount(txns_for(session, allocation))

        assert after == pytest.approx(before)
        assert [t for t in txns_for(session, allocation)
                if t.transaction_type == AllocationTransactionType.EXTENSION], (
            'the extension must actually have written a row, or this proves nothing')


class TestTheAllocationSubtree:

    def test_children_are_extended_too(self, committing):
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        parent = make_allocation(session,
                                 account=make_account(session, project=project),
                                 end_date=datetime(2027, 1, 31))
        child_project = make_project(session)
        child = make_allocation(
            session, account=make_account(session, project=child_project),
            end_date=datetime(2027, 1, 31), parent=parent)

        handle_extension(session, action_for(project.projcode, '2030-06-30'))
        session.refresh(child)
        assert child.end_date == datetime(2030, 6, 30, 23, 59, 59)

    def test_a_child_row_is_not_marked_propagated(self, committing):
        """Counter-intuitive, and measured: **zero** of the 1,553 production rows have
        ``propagated`` set. Legacy's ``doExtend`` never touches the flag, so a child
        node's row looks exactly like its parent's."""
        session = committing
        from factories import make_account, make_allocation, make_project
        project = make_project(session)
        parent = make_allocation(session,
                                 account=make_account(session, project=project),
                                 end_date=datetime(2027, 1, 31))
        child = make_allocation(
            session, account=make_account(session, project=make_project(session)),
            end_date=datetime(2027, 1, 31), parent=parent)

        handle_extension(session, action_for(project.projcode, '2030-06-30'))
        rows = [t for t in txns_for(session, child)
                if t.transaction_type == AllocationTransactionType.EXTENSION]
        assert rows and all(not r.propagated for r in rows)

    def test_a_shrink_anywhere_in_the_subtree_aborts_before_writing(self, committing):
        """``validateNewEndDate`` walks the whole subtree *before* ``extend`` walks it
        again. A half-extended tree is worse than an unextended one."""
        session = committing
        from factories import make_account, make_allocation, make_project
        from sam.manage.extend import extend_account_allocation
        project = make_project(session)
        parent = make_allocation(session,
                                 account=make_account(session, project=project),
                                 end_date=datetime(2027, 1, 31))
        make_allocation(
            session, account=make_account(session, project=make_project(session)),
            end_date=datetime(2033, 7, 31), parent=parent)

        with pytest.raises(ValueError, match='after the requested'):
            extend_account_allocation(session, parent,
                                      new_end=datetime(2030, 6, 30, 23, 59, 59),
                                      comment='x')
        assert parent.end_date == datetime(2027, 1, 31, 23, 59, 59)

    def test_an_inheriting_allocation_is_detached_with_an_audit_row(self, committing):
        """⚠️ Declared divergence. Legacy's ``disinherit()`` severs the parent link in
        memory and writes nothing — production holds **zero** DETACH rows against 2,390
        inheriting allocations. SAM's audit trail is the product."""
        session = committing
        from factories import make_account, make_allocation, make_project
        from sam.manage.extend import extend_account_allocation
        parent = make_allocation(session, end_date=datetime(2027, 1, 31))
        child = make_allocation(
            session, account=make_account(session, project=make_project(session)),
            end_date=datetime(2027, 1, 31), parent=parent)

        extend_account_allocation(session, child,
                                  new_end=datetime(2030, 6, 30, 23, 59, 59),
                                  comment='x')

        assert child.parent_allocation_id is None
        # ⚠️ DETACH is an *intent*, not a column value: LEGACY_TYPE_MAP writes it as
        # transaction_type='ADJUSTMENT' with a '[DETACH]' comment tag, because legacy
        # SAM's Java enum throws on anything outside its five strings.
        detaches = [t for t in txns_for(session, child)
                    if (t.transaction_comment or '').startswith('[DETACH]')]
        assert len(detaches) == 1
        assert detaches[0].transaction_type == 'ADJUSTMENT'
        assert detaches[0].user_id is None
        # And it must not disturb the amount: the tagged ADJUSTMENT carries 0.0.
        assert detaches[0].transaction_amount == 0.0


class TestTheRegistration:

    def test_the_handler_is_bound_to_the_extend_service(self):
        import sam.xras.handlers  # noqa: F401
        from sam.xras.dispatch import registered_services
        assert 'extend' in registered_services()

    def test_a_corpus_extension_reaches_it_through_the_dispatcher(self, committing):
        """End to end from the wire shape, with the real selector."""
        session = committing
        from factories import make_account, make_allocation, make_project
        from sam.xras.dispatch import dispatch_action
        import sam.xras.handlers  # noqa: F401

        data = load_fixture('extension_ucub0166_ok.json')
        project = make_project(session, projcode='ZZUB0166')
        make_allocation(session, account=make_account(session, project=project),
                        end_date=datetime(2025, 1, 31))

        result = dispatch_action(session, dict(data, requestNumber='ZZUB0166'))
        assert result.status == 'processed'
        assert result.service == 'extend'
