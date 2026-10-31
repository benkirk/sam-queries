"""``XrasSubmission``: the row behind every XRAS action SAM files (XRAS_SUBMISSION.md § 4.2)."""

from datetime import datetime

import pytest

from sam.integration.xras_submission import (
    UNKNOWN_STEP,
    XrasSubmission,
    documents_to_reattach,
    open_submission_for,
    submission_by_action_id,
)

_EULA = dict(eula_sha='a' * 40, eula_accepted_at=datetime(2026, 10, 29, 9, 0))
_LINE = 98_700_001


def _open(session, *, action_type='Extension', by='xsub-pi', **keys):
    keys.setdefault('request_id', _LINE)
    return XrasSubmission.create(session, action_type=action_type, created_by=by,
                                 xa_user=by, payload={'comments': 'more time'},
                                 **_EULA, **keys)


class TestCreate:

    def test_a_new_row_is_a_draft_with_its_agreement(self, session):
        row = _open(session, project_id=1)
        assert row.state == 'draft' and row.is_active
        assert row.eula_sha == 'a' * 40 and row.payload_dict == {'comments': 'more time'}
        assert row.steps == {} and row.creation_time is not None

    def test_the_agreement_is_required(self, session):
        with pytest.raises(ValueError, match='agreement'):
            XrasSubmission.create(session, action_type='Supplement', created_by='u',
                                  xa_user='u', payload={}, eula_sha='', eula_accepted_at=None)

    @pytest.mark.parametrize('kwargs', [{'action_type': 'Transfer'},
                                        {'action_type': 'Extension', 'projcode': 'X'}])
    def test_an_unknown_type_or_column_is_refused(self, session, kwargs):
        action_type = kwargs.pop('action_type')
        with pytest.raises(ValueError):
            XrasSubmission.create(session, action_type=action_type, created_by='u',
                                  xa_user='u', payload={}, **_EULA, **kwargs)


class TestLifecycle:

    def test_the_happy_path_stamps_each_time(self, session):
        row = _open(session)
        for state in ('created', 'populated', 'submitted'):
            row.advance(state)
        assert row.submitted_time is not None and not row.is_active
        row.mark_applied(project_id=4242)
        assert (row.state, row.project_id) == ('applied', 4242)
        assert row.applied_time is not None

    def test_an_unsubmitted_operator_row_may_still_be_applied(self, session):
        row = _open(session).advance('created').advance('populated')
        row.mark_applied(project_id=7)
        assert row.state == 'applied' and row.submitted_time is None

    @pytest.mark.parametrize('path', [('submitted',), ('created', 'draft'),
                                      ('aborted', 'created'), ('applied',)])
    def test_an_illegal_step_is_refused(self, session, path):
        row = _open(session)
        with pytest.raises(ValueError):
            for state in path:
                row.advance(state)

    def test_a_stop_keeps_the_state_and_the_next_step_clears_it(self, session):
        row = _open(session).advance('created')
        row.stop(['Supporting Grant PI Name is required'])
        assert row.state == 'created' and 'PI Name' in row.last_error
        row.advance('populated')
        assert row.last_error is None

    def test_abort_ends_an_open_row(self, session):
        row = _open(session).advance('created').advance('aborted')
        assert row.aborted_time is not None and not row.is_active

    def test_steps_and_keys_accumulate(self, session):
        row = _open(session, request_id=None)
        row.set_keys(request_id=_LINE, action_id=98_700_101, snapshot={'rules': {}})
        row.record_step('mint', 11).record_step('dates', 12)
        assert row.steps == {'mint': 11, 'dates': 12}
        assert row.snapshot_dict == {'rules': {}} and row.action_id == 98_700_101
        row.set_keys(request_number='NCAR9999')
        assert (row.request_id, row.request_number) == (_LINE, 'NCAR9999')


class TestStepStatuses:
    """One reader for the flow and the page: a step with no readable audit row is unknown."""

    def test_a_recorded_event_reads_its_status_and_a_lost_one_is_unknown(self, session):
        from sam.integration.xras import XrasRemediationEvent
        from sam.integration.xras_submission import step_statuses
        event = XrasRemediationEvent.create(session, operation='add_action', created_by='p',
                                            xa_user='p', status='verified')
        statuses = step_statuses(session, {'mint': event.xras_remediation_event_id,
                                           'dates': None, 'grant:1': 1})
        assert statuses == {'mint': 'verified', 'dates': UNKNOWN_STEP, 'grant:1': UNKNOWN_STEP}
        assert step_statuses(session, {}) == {}

    @pytest.mark.parametrize('status, needed', [
        (None, True), ('rejected', True), ('verified', False), ('unverified', False),
        ('error', False), (UNKNOWN_STEP, False)])
    def test_only_a_never_sent_or_refused_document_is_reattached(self, status, needed):
        statuses = {} if status is None else {'document:letter.pdf': status}
        assert documents_to_reattach(statuses, ['letter.pdf']) == (
            ['letter.pdf'] if needed else [])


class TestLookups:

    def test_by_action_id(self, session):
        row = _open(session, action_id=98_700_102)
        assert submission_by_action_id(session, 98_700_102) is row
        assert submission_by_action_id(session, 98_700_103) is None

    def test_the_second_open_guard_is_per_actor_line_and_type(self, session):
        row = _open(session)
        assert open_submission_for(session, request_id=_LINE, action_type='Extension',
                                   created_by='xsub-pi') is row
        assert open_submission_for(session, request_id=_LINE, action_type='Supplement',
                                   created_by='xsub-pi') is None
        assert open_submission_for(session, request_id=_LINE, action_type='Extension',
                                   created_by='xsub-other') is None
        row.advance('aborted')
        assert open_submission_for(session, request_id=_LINE, action_type='Extension',
                                   created_by='xsub-pi') is None
