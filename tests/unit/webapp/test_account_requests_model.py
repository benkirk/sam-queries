"""The ``account_request`` and ``account_request_event`` tables.

Bare VARCHARs, so ``create()`` and the transition methods are the only
enforcement there is. What is worth pinning: the purpose invariants, the
lower-cased email (the one match key), that an unverified row is invisible to
the queue in Python AND in SQL, and that a scheduled task may write here --
the opposite of ``XrasRemediationEvent``.
"""

from datetime import date, datetime, timedelta

import pytest
from factories import (
    make_account_request,
    make_account_request_event,
    make_project,
    make_user,
)

from sam.core.account_requests import (
    ACCOUNT_REQUEST_PURPOSES,
    ACCOUNT_REQUEST_STATES,
    CREATED_BY_SWEEP,
    AccountRequest,
    AccountRequestEvent,
)



class TestTheVocabularies:
    def test_fulfilled_is_not_a_state(self):
        assert 'fulfilled' not in ACCOUNT_REQUEST_STATES
        assert 'requested' not in ACCOUNT_REQUEST_STATES

    def test_the_vocabularies_fit_their_columns(self):
        assert max(map(len, ACCOUNT_REQUEST_STATES)) <= 16
        assert max(map(len, ACCOUNT_REQUEST_PURPOSES)) <= 16

    def test_an_unknown_purpose_is_refused(self, session):
        with pytest.raises(ValueError, match='unknown'):
            make_account_request(session, purpose='reactivation')


class TestTheInvariants:
    def test_enrollment_needs_a_project(self, session):
        with pytest.raises(ValueError, match='project_id'):
            AccountRequest.create(session, email='a@x.edu', first_name='A',
                                  last_name='B', purpose='enrollment',
                                  created_by='benkirk')

    def test_submission_needs_a_placeholder(self, session):
        with pytest.raises(ValueError, match='xras_username'):
            AccountRequest.create(session, email='a@x.edu', first_name='A',
                                  last_name='B', purpose='submission',
                                  created_by='benkirk')

    def test_standalone_carries_no_project(self, session):
        project = make_project(session)
        with pytest.raises(ValueError, match='standalone'):
            make_account_request(session, purpose='standalone', project=project)

    def test_email_is_lower_cased_and_stripped(self, session):
        row = make_account_request(session, email='  Jane.Doe@Example.EDU ')
        assert row.email == 'jane.doe@example.edu'

    def test_the_placeholder_is_stored_lower_cased(self, session):
        """A match key looked up with a plain IN, on either backend."""
        row = make_account_request(session, purpose='submission',
                                   xras_username=' Jane-User-ABC123 ')
        assert row.xras_username == 'jane-user-abc123'

    def test_every_row_carries_both_stamps_from_the_clock(self, session):
        when = datetime(2026, 9, 14, 8, 0)
        row = make_account_request(session, clock=when)
        assert (row.creation_time, row.modified_time) == (when, when)
        assert row.verify_sent_count == 0 and row.source_ip is None

    def test_source_ip_is_kept_when_given(self, session):
        row = make_account_request(session, source_ip='2001:db8::1')
        assert row.source_ip == '2001:db8::1'

    def test_a_non_address_is_refused(self, session):
        with pytest.raises(ValueError, match='email'):
            make_account_request(session, email='not-an-address')

    def test_names_are_required(self, session):
        with pytest.raises(ValueError, match='last_name'):
            make_account_request(session, last_name='   ')

    def test_a_scheduled_task_may_create_a_row(self, session):
        """The sweep derives submission rows -- unlike the XRAS remediation log."""
        row = make_account_request(session, purpose='submission', by=CREATED_BY_SWEEP)
        assert row.created_by == CREATED_BY_SWEEP


class TestVisibilityToTheQueue:
    def test_a_vouched_row_is_open_at_once(self, session):
        row = make_account_request(session, verified_by='benkirk')
        assert row.is_open and row.is_verified
        assert row.verified_by == 'benkirk'

    def test_an_unverified_row_is_invisible_in_python_and_sql(self, session):
        row = make_account_request(session, verified_by=None)
        assert not row.is_open
        found = session.query(AccountRequest).filter(
            AccountRequest.is_open,
            AccountRequest.account_request_id == row.account_request_id).count()
        assert found == 0

    def test_mark_verified_clears_the_code(self, session):
        row = make_account_request(session, verified_by=None)
        row.set_verification('a' * 64, datetime.now() + timedelta(hours=1))
        row.mark_verified('self')
        assert row.verify_code_hash is None and row.verify_expires_at is None
        assert row.verified_by == 'self' and row.is_open
        found = session.query(AccountRequest).filter(
            AccountRequest.is_open,
            AccountRequest.account_request_id == row.account_request_id).count()
        assert found == 1

    def test_every_verification_mail_is_counted(self, session):
        row = make_account_request(session, verified_by=None)
        expires = datetime.now() + timedelta(hours=1)
        row.set_verification('a' * 64, expires)
        row.set_verification('b' * 64, expires)
        assert row.verify_sent_count == 2


class TestQueueTransitions:
    def test_claim_and_unclaim(self, session):
        row = make_account_request(session)
        row.claim('operator1')
        assert (row.state, row.assignee) == ('claimed', 'operator1')
        row.unclaim()
        assert (row.state, row.assignee) == ('submitted', None)

    def test_dismiss_requires_a_reason_and_records_who(self, session):
        row = make_account_request(session).claim('operator1')
        with pytest.raises(ValueError, match='reason'):
            row.dismiss('operator1', '   ')
        row.dismiss('operator1', 'duplicate of #12')
        assert row.state == 'dismissed'
        assert row.closed_by == 'operator1'
        assert row.closed_reason == 'duplicate of #12'
        assert row.closed_at is not None
        assert row.assignee is None, 'a closed row is nobody\'s'
        assert not row.is_open

    def test_reject_is_a_distinct_closure(self, session):
        row = make_account_request(session).reject('operator1', 'not eligible')
        assert row.state == 'rejected'
        with pytest.raises(ValueError, match='cannot reject a rejected'):
            row.reject('operator1', 'again')
        with pytest.raises(ValueError, match='required to dismiss'):
            make_account_request(session).dismiss('operator1', '  ')

    def test_the_rejection_notice_is_stamped_and_a_reopen_clears_it(self, session):
        row = make_account_request(session).reject('operator1', 'not eligible')
        assert row.closure_notified_at is None
        row.mark_closure_notified(datetime(2026, 9, 17, 10, 0))
        assert row.closure_notified_at == datetime(2026, 9, 17, 10, 0)
        row.reopen()
        assert row.closure_notified_at is None and row.closed_reason is None

    def test_a_closed_row_cannot_be_claimed(self, session):
        row = make_account_request(session).dismiss('operator1', 'dup')
        with pytest.raises(ValueError, match='dismissed'):
            row.claim('operator2')

    def test_reopen_restores_the_queue_state(self, session):
        row = make_account_request(session).dismiss('operator1', 'dup')
        row.reopen()
        assert row.state == 'submitted'
        assert (row.closed_by, row.closed_at, row.closed_reason) == (None, None, None)
        with pytest.raises(ValueError, match='already open'):
            row.reopen()

    def test_requested_at_survives_a_claim(self, session):
        """The digest stamp is orthogonal to the state, by design."""
        when = datetime(2026, 9, 14, 8, 0)
        row = make_account_request(session).mark_requested(when).claim('op')
        assert row.requested_at == when and row.state == 'claimed'

    def test_requested_at_is_when_first_told(self, session):
        """Later digests are in notification_log; the lead time starts here."""
        first = datetime(2026, 9, 14, 8, 0)
        row = make_account_request(session).mark_requested(first)
        row.mark_requested(first + timedelta(days=7))
        assert row.requested_at == first


class TestFulfillment:
    def test_fulfill_stamps_the_mirror(self, session):
        user = make_user(session)
        row = make_account_request(session).record_fulfill_error('earlier')
        row.fulfill(user, when=datetime(2026, 9, 15, 9, 0))
        assert row.user_id == user.user_id
        assert row.upid == user.upid
        assert row.fulfilled_at == datetime(2026, 9, 15, 9, 0)
        assert row.fulfill_error is None and row.is_fulfilled

    def test_a_fulfill_error_is_one_line(self, session):
        row = make_account_request(session)
        row.record_fulfill_error('x' * 300)
        assert len(row.fulfill_error) == 255


class TestEvents:
    def test_the_code_is_normalized(self, session):
        event = make_account_request_event(session, event_code=' wrf-tutorial-2026-10 ')
        assert event.event_code == 'WRF-TUTORIAL-2026-10'

    @pytest.mark.parametrize('bad', ['ab', '-lead', 'has space', 'x' * 33, ''])
    def test_a_bad_code_is_refused(self, bad):
        with pytest.raises(ValueError, match='event code'):
            AccountRequestEvent.normalize_code(bad)

    def test_the_window_and_the_switch_are_both_honored(self, session):
        now = datetime(2026, 10, 1, 12, 0)
        event = make_account_request_event(
            session, opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=1))
        assert event.is_open_at(now)
        assert not event.is_open_at(now - timedelta(days=2))
        assert not event.is_open_at(now + timedelta(days=1)), 'closes_at is exclusive'
        event.close()
        assert not event.is_open_at(now)
        event.reopen()
        assert event.is_open_at(now)

    def test_no_window_means_always_open_while_active(self, session):
        event = make_account_request_event(session)
        assert event.is_open_at(datetime(2000, 1, 1))

    def test_a_backwards_window_is_refused(self, session):
        now = datetime.now()
        with pytest.raises(ValueError, match='closes_at'):
            make_account_request_event(session, opens_at=now, closes_at=now - timedelta(days=1))

    def test_listed_is_opt_in_and_update_can_clear_it(self, session):
        event = make_account_request_event(session)
        assert event.listed is False
        event.update(listed=True)
        assert event.listed is True
        event.update(name='Renamed')
        assert event.listed is True, 'an update that does not mention it leaves it'
        event.update(listed=False)
        assert event.listed is False

    def test_invite_only_is_opt_in_and_round_trips(self, session):
        event = make_account_request_event(session)
        assert event.invite_only is False
        event.update(invite_only=True)
        event.update(name='Renamed')
        assert event.invite_only is True, 'an update that does not mention it leaves it'
        event.update(invite_only=False)
        assert event.invite_only is False
        assert make_account_request_event(session, invite_only=True).invite_only is True

    def test_update_can_clear_the_sponsor(self, session):
        sponsor = make_user(session)
        event = make_account_request_event(session, extra_sponsor=sponsor)
        assert event.extra_sponsor_user_id == sponsor.user_id
        event.update(extra_sponsor_user_id=None, accounts_needed_by=date(2027, 1, 1))
        assert event.extra_sponsor_user_id is None
        assert event.accounts_needed_by == date(2027, 1, 1)

    def test_instructions_round_trip_and_clear(self, session):
        event = make_account_request_event(session)
        assert event.instructions is None and event.modified_time is not None
        event.update(instructions='  Bring a laptop.\nLog in with the code. ')
        assert event.instructions == 'Bring a laptop.\nLog in with the code.'
        event.update(instructions='')
        assert event.instructions is None

    def test_a_request_points_at_its_event(self, session):
        event = make_account_request_event(session)
        row = make_account_request(session, purpose='enrollment', event=event)
        assert row.event_id == event.account_request_event_id
        assert row.project_id == event.project_id


class TestTheInvitationLink:
    SENT = datetime(2026, 9, 24, 9, 0)

    def test_mark_invite_sent_stamps_and_restamps(self, session):
        row = make_account_request(session)
        assert row.invite_state is None
        row.mark_invite_sent(self.SENT)
        assert row.invite_sent_at == self.SENT and row.invite_state == 'awaiting'
        row.mark_invite_sent(self.SENT + timedelta(hours=1))
        assert row.invite_sent_at == self.SENT + timedelta(hours=1), 'a resend re-stamps'

    def test_complete_invite_updates_the_row_in_place(self, session):
        row = make_account_request(session, first_name='Ada', last_name='Lovelace',
                                   comment='sponsor note').mark_invite_sent(self.SENT)
        row_id, before = row.account_request_id, session.query(AccountRequest).count()
        accepted = self.SENT + timedelta(minutes=5)
        row.complete_invite(
            fields={'first_name': ' Augusta ', 'phone': '+1 303 555 0100',
                    'residence_country': 'United Kingdom', 'academic_status': 'Faculty',
                    'orcid': '0000-0002-1825-0097', 'desired_username': 'ada',
                    'email': 'hijack@example.edu'},
            eula_sha='5c2cca1c8b5180f791670276d5bb55832ba6a2e2', accepted_at=accepted,
            source_ip='192.0.2.9', clock=accepted + timedelta(minutes=1))
        assert session.query(AccountRequest).count() == before, 'updates, never inserts'
        assert row.account_request_id == row_id
        assert row.first_name == 'Augusta' and row.last_name == 'Lovelace'
        assert row.phone == '+1 303 555 0100' and row.residence_country == 'United Kingdom'
        assert row.academic_status == 'Faculty' and row.orcid == '0000-0002-1825-0097'
        assert row.desired_username == 'ada'
        assert row.email != 'hijack@example.edu', 'the vouched address is not a form field'
        assert row.comment == 'sponsor note'
        assert row.completed_at == accepted + timedelta(minutes=1)
        assert row.eula_sha.startswith('5c2cca1') and row.eula_accepted_at == accepted
        assert row.source_ip == '192.0.2.9' and row.invite_state == 'completed'

    def test_a_closed_or_fulfilled_row_cannot_be_completed(self, session):
        kwargs = dict(fields={}, eula_sha='x', accepted_at=self.SENT)
        closed = make_account_request(session).dismiss('op', 'dup')
        with pytest.raises(ValueError, match='complete'):
            closed.complete_invite(**kwargs)
        fulfilled = make_account_request(session).fulfill(make_user(session))
        with pytest.raises(ValueError, match='fulfilled'):
            fulfilled.complete_invite(**kwargs)

    def test_a_blanked_name_is_refused(self, session):
        row = make_account_request(session)
        with pytest.raises(ValueError, match='first_name'):
            row.complete_invite(fields={'first_name': '  '}, eula_sha='x',
                                accepted_at=self.SENT)

    def test_create_records_an_acceptance_only_with_its_time(self, session):
        stamped = make_account_request(session, eula_sha='abc', eula_accepted_at=self.SENT)
        assert stamped.eula_sha == 'abc' and stamped.eula_accepted_at == self.SENT
        bare = make_account_request(session, eula_sha='abc')
        assert bare.eula_sha is None and bare.eula_accepted_at is None
