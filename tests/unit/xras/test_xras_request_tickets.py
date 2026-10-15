"""The refused post's 422 lines and ticket state (docs/plans/XRAS_REQUEST_TICKETS.md § 6)."""
from datetime import datetime, timedelta

import pytest

from sam import ExternalTicket
from sam.notify.models import NotificationLog
from sam.queries import xras_request_tickets as tickets
from sam.queries.xras_requests import message_kind, person_username
from sam.xras import errors

PLACEHOLDER = 'jane-user-1a2b'


@pytest.mark.parametrize('message,kind', [
    (errors.pi_not_in_database('jdoe'), 'person'),
    (errors.pi_not_active('jdoe'), 'person'),
    (errors.manager_not_in_database('jdoe'), 'person'),
    (errors.manager_not_active('jdoe'), 'person'),
    (errors.username_missing('jdoe'), 'person'),
    (errors.username_inactive('jdoe'), 'person'),
    (errors.no_affiliation_for_pi('jdoe'), 'person'),
    (errors.missing_pi_role(), 'person'),
    (errors.username_missing(PLACEHOLDER), 'placeholder'),
    (errors.pi_not_in_database(PLACEHOLDER), 'placeholder'),
    (errors.mnemonic_internal_failed('jdoe', 'CISL'), 'mnemonic'),
    (errors.no_current_affiliation_for_pi('jdoe'), 'mnemonic'),
    (errors.cannot_find_contract('AGS-1', '000001'), 'contract'),
    (errors.ambiguous_contract('AGS-1', '000001', ['A', 'B']), 'contract'),
    (errors.missing_title(), None),
])
def test_every_blocker_string_has_its_kind(message, kind):
    assert message_kind(message) == kind


def test_the_person_blocker_names_its_username():
    assert person_username(errors.manager_not_active('jdoe')) == 'jdoe'
    assert person_username(errors.missing_pi_role()) is None
    assert person_username(errors.missing_title()) is None


def test_one_pointer_per_kind_in_a_fixed_order_after_the_verbatim_blockers():
    messages = [errors.username_missing('jdoe'), errors.username_inactive('ann'),
                errors.cannot_find_contract('AGS-1', '000001')]
    lines = tickets.reply_lines(messages)
    assert lines[:3] == messages
    assert [line.split(':')[0] for line in lines[3:]] == ['To fix a contract',
                                                          'To fix a person']


def test_no_blocker_kind_means_no_pointer():
    assert tickets.reply_lines([errors.missing_title()]) == [errors.missing_title()]


class TestTicketState:
    RID = 990001

    def _ledger(self, session, status, when):
        NotificationLog.create(session, kind=tickets.LEDGER_KIND, channel='ticket',
                               transport='dry-run', status=status, recipient='dry-run',
                               requested_by='task:xras_request_tickets',
                               entity_type=tickets.ENTITY_TYPE, entity_id=self.RID,
                               dedup_key=f'xras_ticket:{self.RID}:x', when=when)

    def test_pending_until_the_task_files(self, session):
        state = tickets.request_ticket_state(session, self.RID)
        assert state == tickets.TicketState('pending')
        assert tickets.state_line(state).endswith(
            'still refused in about 15 minutes, a ticket opens with NUSD.')

    def test_a_failed_last_attempt_says_so(self, session):
        now = datetime.now()
        self._ledger(session, 'sent', now - timedelta(hours=1))
        self._ledger(session, 'failed', now)
        assert tickets.request_ticket_state(session, self.RID).status == 'failed'

    def test_a_linked_ticket_is_named(self, session):
        self._ledger(session, 'failed', datetime.now() - timedelta(hours=1))
        ExternalTicket.create(session, provider='dry-run', ticket_key='DRY-SAM-XR-990001',
                              entity_type=tickets.ENTITY_TYPE, entity_id=self.RID,
                              origin='created', requested_by='task:xras_request_tickets')
        state = tickets.request_ticket_state(session, self.RID)
        assert tickets.state_line(state) == (
            'This action cannot yet succeed, see DRY-SAM-XR-990001.')


def test_the_ticket_names_only_the_people_on_the_request_at_its_action_date(session):
    """new_ncar4214_ok.json carries two PI roles that ended before the action began
    (one a placeholder); the ticket must name neither, as the handler does not."""
    import json

    from _paths import REPO_ROOT
    from sam.core.account_requests import AccountRequest

    payload = json.loads((REPO_ROOT / 'tests' / 'fixtures' / 'xras' / 'actions'
                          / 'new_ncar4214_ok.json').read_text())
    for username in ('placeholder34-user-00034', 'user_00000035'):
        AccountRequest.create(session, email=f'{username}@example.invalid', first_name='A',
                              last_name='B', purpose='submission',
                              created_by='task:xras_sweep', xras_username=username)
    episode = tickets.Episode(
        request_id=1, request_number='NCAR4214', action_type='New',
        first_refused=datetime(2026, 7, 30, 9), refusals=1, latest_log_id=1,
        latest_refused=datetime(2026, 7, 30, 9),
        messages=(errors.username_missing('user_00000035'),), payload=payload)
    context = tickets.ticket_context(session, episode)
    assert context['pi'] == 'Given33 Surname33 (user_00000033)'
    assert [r['email'] for r in context['queued']] == ['user_00000035@example.invalid']
