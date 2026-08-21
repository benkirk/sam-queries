"""The ``xras_remediation_event`` audit table.

Runs against the raw test session, so rows are built directly and rolled back by
the per-test SAVEPOINT.

What is worth testing on an audit table is not that columns persist — it is the
handful of rules that make the row *trustworthy* after the fact:

* the vocabularies are validated at the only enforcement point there is (the
  DDL declares bare ``VARCHAR``s deliberately — an ENUM change is a DBA ticket);
* the two identity columns stay distinct, because attributing an operator's
  decision to the PI SAM impersonated would be a false record;
* a scheduled task can never write one;
* the row opens *before* the write and is closed afterwards, so a row that is
  still ``attempted`` means a write went out and SAM never learned how it ended.
"""

from datetime import datetime, timedelta

import pytest
from factories import make_xras_remediation_event

from sam.integration.xras import (
    XRAS_REMEDIATION_OPERATIONS,
    XRAS_REMEDIATION_STATUSES,
    XrasRemediationEvent,
)

pytestmark = pytest.mark.unit


class TestTheVocabularies:
    """Bare VARCHARs, so ``create()`` is the only thing standing in the way."""

    def test_the_operations_are_the_five_proven_verbs(self):
        assert set(XRAS_REMEDIATION_OPERATIONS) == {
            'merge_person', 'withdraw_action', 'submit_action',
            'add_role', 'remove_role'}

    def test_an_unknown_operation_is_refused(self, session):
        with pytest.raises(ValueError, match='unknown'):
            make_xras_remediation_event(session, operation='unreconcile')

    def test_an_unknown_status_is_refused(self, session):
        with pytest.raises(ValueError, match='unknown'):
            make_xras_remediation_event(session, status='probably-fine')

    def test_complete_validates_the_status_too(self, session):
        event = make_xras_remediation_event(session)
        with pytest.raises(ValueError, match='unknown'):
            XrasRemediationEvent.complete(
                session, event.xras_remediation_event_id, status='done')

    def test_unverified_is_not_the_same_as_rejected(self):
        """Both exist because 'we could not tell' is not 'nothing happened'."""
        assert 'unverified' in XRAS_REMEDIATION_STATUSES
        assert 'rejected' in XRAS_REMEDIATION_STATUSES


class TestNoTaskMayWriteOne:
    """Nothing under ``src/scheduling/`` may write to XRAS.

    Asserted here because this is the table a task would have to write a row to
    in order to do it — the closest thing to a chokepoint the design has.
    """

    def test_a_task_operator_is_refused(self, session):
        with pytest.raises(ValueError, match='scheduled task'):
            make_xras_remediation_event(session, by='task:xras_sweep')

    def test_a_human_operator_is_fine(self, session):
        assert make_xras_remediation_event(session, by='benkirk').created_by \
            == 'benkirk'


class TestTheTwoIdentities:
    """``created_by`` is who clicked; ``xa_user`` is who SAM impersonated."""

    def test_they_are_recorded_separately(self, session):
        event = make_xras_remediation_event(
            session, operation='withdraw_action', by='benkirk',
            xa_user='some-pi', request_number='EXAM0001', action_id=30578)
        assert event.created_by == 'benkirk'
        assert event.xa_user == 'some-pi'

    def test_a_user_agnostic_op_records_no_impersonation(self, session):
        """Merge authorizes on the key alone — there is no one to name."""
        event = make_xras_remediation_event(session, operation='merge_person',
                                            username='p-user-x',
                                            target_username='real')
        assert event.xa_user is None

    def test_the_operator_is_truncated_to_username_width(self, session):
        event = make_xras_remediation_event(session, by='x' * 80)
        assert len(event.created_by) == 35


class TestTheTwoPhaseWrite:
    """Open before dispatch, close after. The gap is the point."""

    def test_a_new_row_is_attempted_and_incomplete(self, session):
        event = make_xras_remediation_event(session)
        assert event.status == 'attempted'
        assert event.completed_time is None

    def test_completion_stamps_the_outcome(self, session):
        event = make_xras_remediation_event(session)
        XrasRemediationEvent.complete(
            session, event.xras_remediation_event_id, status='verified',
            http_status=200, outcome_reason='source no longer resolves',
            after_state={'source': None})
        session.refresh(event)
        assert event.status == 'verified'
        assert event.http_status == 200
        assert event.completed_time is not None
        assert event.after_state == '{"source": null}'

    def test_a_missing_row_is_none_not_an_exception(self, session):
        """The write's own result must not be masked by a bookkeeping failure."""
        assert XrasRemediationEvent.complete(
            session, 999_999_999, status='verified') is None

    def test_an_over_long_reason_is_truncated_not_rejected(self, session):
        event = make_xras_remediation_event(session)
        XrasRemediationEvent.complete(
            session, event.xras_remediation_event_id, status='error',
            outcome_reason='x' * 400)
        session.refresh(event)
        assert len(event.outcome_reason) == 255

    def test_the_role_id_can_arrive_only_at_completion(self, session):
        """XRAS assigns it in the response — it cannot be known beforehand."""
        event = make_xras_remediation_event(session, operation='add_role',
                                            request_number='EXAM0001',
                                            role_type='User')
        assert event.role_id is None
        XrasRemediationEvent.complete(session, event.xras_remediation_event_id,
                                      status='verified', role_id=580030)
        session.refresh(event)
        assert event.role_id == 580030


class TestTheCaptures:

    def test_a_dict_capture_is_serialized(self, session):
        event = make_xras_remediation_event(
            session, before_state={'source': {'residenceCountry': 'Canada'}})
        assert '"residenceCountry": "Canada"' in event.before_state

    def test_a_string_capture_is_left_alone(self, session):
        assert make_xras_remediation_event(
            session, before_state='{"already":"json"}').before_state \
            == '{"already":"json"}'

    def test_an_unserializable_value_does_not_lose_the_row(self, session):
        """Losing an audit row to a TypeError is far worse than storing a str."""
        event = make_xras_remediation_event(
            session, before_state={'when': datetime(2026, 8, 21, 9, 0)})
        assert '2026-08-21' in event.before_state


class TestNoForeignKeys:
    """Every identifier belongs to XRAS — an FK would block the merge case."""

    def test_the_table_declares_none(self):
        assert not list(XrasRemediationEvent.__table__.foreign_keys)

    def test_a_merge_of_a_username_sam_never_heard_of_is_recordable(self, session):
        """The placeholder is *deleted* by the operation this row records."""
        event = make_xras_remediation_event(
            session, operation='merge_person',
            username='nobody-user-zzzzz', target_username='also-nobody')
        assert event.xras_remediation_event_id is not None


class TestBackDating:

    def test_the_factory_can_backdate_for_ordering_tests(self, session):
        when = datetime.now() - timedelta(days=3)
        event = make_xras_remediation_event(session, when=when)
        assert event.creation_time == when
