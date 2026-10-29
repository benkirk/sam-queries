"""``sam.queries.xras_my_requests``: what each request is waiting on, from SAM's rows and the index."""

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from sam.integration.xras import XrasActionLog
from sam.integration.xras_submission import XrasSubmission
from sam.queries.xras_my_requests import (
    action_readout,
    led_projects,
    missing_documents,
    my_requests,
    submission_readout,
)

EULA = dict(eula_sha='a' * 40, eula_accepted_at=datetime(2026, 10, 29, 9, 0))


def _log(status, processed=None):
    return SimpleNamespace(status=status, processed_time=processed)


class TestActionReadout:

    @pytest.mark.parametrize('status, label', [
        ('Submitted', 'In review'), ('Under Review', 'In review'),
        ('Approved', 'Approved, being applied'), ('Rejected', 'Rejected')])
    def test_xras_status_without_a_post(self, status, label):
        assert action_readout(status).label == label

    def test_the_sweep_finding_it_applied_reads_applied(self):
        assert action_readout('Approved', push_state='applied_inferred').label == 'Applied'
        assert action_readout('Approved', push_state='pending').label == \
            'Approved, being applied'

    def test_an_approval_before_the_repoint_was_legacy_sams(self):
        assert action_readout('Approved', entered=date(2025, 1, 1)).label == 'Approved'

    @pytest.mark.parametrize('status, label', [
        ('processed', 'Applied'), ('manual', 'Being reviewed by CISL'),
        ('failed', 'Being reviewed by CISL'), ('received', 'Approved, being applied')])
    def test_the_log_row_decides_once_xras_posted(self, status, label):
        when = datetime(2026, 10, 1)
        out = action_readout('Approved', _log(status, when))
        assert out.label == label
        assert (out.when == when) == (status == 'processed')


class TestSubmissionReadout:

    def _row(self, **kw):
        kw.setdefault('last_error', None)
        return SimpleNamespace(applied_time=None, aborted_time=None,
                               modified_time=datetime(2026, 10, 29), **kw)

    def test_a_stopped_row_names_its_step(self):
        row = self._row(state='populated', last_error=json.dumps({'step': 'submit'}))
        out = submission_readout(row)
        assert (out.label, out.tone) == ('Stopped at submit', 'danger')

    def test_an_open_row_without_an_error_is_not_submitted(self):
        assert submission_readout(self._row(state='created')).label == 'Not submitted'

    def test_submitted_reads_through_the_action(self):
        row = self._row(state='submitted')
        assert submission_readout(row).label == 'In review'
        assert submission_readout(row, _log('processed')).label == 'Applied'

    def test_terminal_states(self):
        assert submission_readout(self._row(state='aborted')).label == 'Discarded'
        assert submission_readout(self._row(state='applied')).label == 'Applied'


class TestMyRequests:

    def test_filed_rows_then_the_index_lines_not_already_shown(self, session):
        filed = XrasSubmission.create(session, action_type='Extension', created_by='pi9',
                                      xa_user='pi9', payload={}, request_number='UHSS0001',
                                      action_id=880_001, **EULA)
        filed.advance('created')
        filed.advance('populated')
        filed.advance('submitted')
        session.add(XrasActionLog(received_time=datetime.now(), remote_actor='t',
                                  raw_payload='{}', status='processed', action_id=880_001,
                                  processed_time=datetime(2026, 10, 30)))
        session.flush()
        roster = [{'username': 'PI9'}]
        index = [
            {'request_number': 'UHSS0001', 'roster': roster,
             'actions': [{'action_id': 880_001, 'action_type': 'Extension',
                          'action_status': 'Approved'}]},
            {'request_number': 'UHSS0002', 'roster': roster,
             'actions': [{'action_id': 5, 'action_type': 'New', 'action_status': 'Approved',
                          'entry_date': date(2026, 9, 1)},
                         {'action_id': 7, 'action_type': 'Supplement',
                          'action_status': 'Submitted', 'entry_date': date(2026, 10, 2)}]},
            {'request_number': 'UHSS0003', 'roster': [{'username': 'someone'}],
             'actions': [{'action_id': 9, 'action_status': 'Submitted'}]},
        ]
        rows = my_requests(session, 'pi9', index)
        assert [(r['kind'], r['request_number'], r['readout'].label) for r in rows] == [
            ('filed', 'UHSS0001', 'Applied'), ('xras', 'UHSS0002', 'In review')]
        assert rows[1]['action_type'] == 'Supplement'

    def test_an_open_row_sorts_first(self, session):
        old = XrasSubmission.create(session, action_type='Extension', created_by='pi9',
                                    xa_user='pi9', payload={}, **EULA)
        old.advance('aborted')
        XrasSubmission.create(session, action_type='Supplement', created_by='op',
                              xa_user='pi9', payload={}, **EULA)
        rows = my_requests(session, 'pi9', None)
        assert [r['readout'].label for r in rows] == ['Not submitted', 'Discarded']


class TestLedProjects:

    def test_lead_or_admin_and_active(self, session, active_project):
        user = active_project.lead
        found = led_projects(session, user)
        assert active_project in found
        assert all(p.is_active for p in found)
        assert all(user.user_id in (p.project_lead_user_id, p.project_admin_user_id)
                   for p in found)


class TestMissingDocuments:

    def _row(self, documents, steps):
        return SimpleNamespace(payload_dict={'documents': documents}, steps=steps)

    def test_a_row_without_documents_needs_none(self, session):
        assert missing_documents(session, self._row([], {'mint': 1})) == []

    def test_a_document_never_sent_is_needed_again(self, session):
        letter = [{'filename': 'letter.pdf', 'document_type': 'Advisor_Letter'}]
        assert missing_documents(session, self._row(letter, {'mint': 1, 'attributes': 2})) == \
            letter

    @pytest.mark.parametrize('status, needed', [
        ('rejected', True), ('verified', False), ('unverified', False), ('error', False)])
    def test_only_a_refused_upload_is_needed_again(self, session, status, needed):
        """XRAS stored nothing on a refusal; an unknown outcome may have landed, so the
        flow will not re-send it and the page must not ask for it."""
        from sam.integration.xras import XrasRemediationEvent
        event = XrasRemediationEvent.create(session, operation='upload_document',
                                            created_by='pi9', xa_user='pi9', status=status)
        letter = [{'filename': 'letter.pdf', 'document_type': 'Advisor_Letter'}]
        row = self._row(letter, {'document:letter.pdf': event.xras_remediation_event_id})
        assert missing_documents(session, row) == (letter if needed else [])
