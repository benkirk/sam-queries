"""The account-request message builders, unit-tested away from Flask.

The admin Send button and the weekly task both call `build_queue_summary`;
the public form and any resend both call `build_verify_message`. Each is
built once so the dedup key, the subject and the payload cannot drift --
and the sample contexts the template editor previews against are pinned to
the real builders' key sets.
"""

from datetime import date, datetime

import pytest
from factories import (
    make_account_request,
    make_account_request_event,
    make_project,
    make_user,
)

from sam.notify.samples import sample_context
from sam.queries.account_notices import (
    ACCOUNT_KIND_SUBJECTS,
    build_queue_summary,
    build_verify_message,
    queue_summary_context,
)

pytestmark = pytest.mark.unit

OCC = datetime(2026, 9, 14, 8, 0)


def _dotted(mapping, prefix=''):
    names = set()
    for key, value in mapping.items():
        names.add(prefix + key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            names |= _dotted(value[0], prefix + key + '.')
    return names


class TestQueueSummary:
    def test_the_context_matches_the_sample_shape(self, session):
        project = make_project(session)
        sponsor = make_user(session)
        event = make_account_request_event(session, project=project,
                                           accounts_needed_by=date(2026, 10, 5))
        rows = [
            make_account_request(session, purpose='enrollment', event=event,
                                 sponsor=sponsor, comment='first line\nsecond',
                                 when=datetime(2026, 9, 11)),
            make_account_request(session, purpose='submission',
                                 when=datetime(2026, 8, 8)).mark_requested(OCC),
        ]
        context = queue_summary_context(session, rows, occurrence=OCC,
                                        queue_url='https://sam/x')
        assert _dotted(context) == _dotted(sample_context('account_queue_summary'))
        assert context['total'] == 2
        assert context['new_count'] == 1 and context['waiting_count'] == 1
        assert context['oldest_days'] == 37
        assert context['by_purpose'] == [{'purpose': 'enrollment', 'count': 1},
                                         {'purpose': 'submission', 'count': 1}]
        event_out, = context['events']
        assert event_out['event_code'] == event.event_code
        assert event_out['project_code'] == project.projcode
        assert event_out['deadline'] == '2026-10-05' and event_out['count'] == 1
        first, second = context['rows']
        assert first['event_code'] == event.event_code, 'event group first'
        assert first['sponsor'] == sponsor.display_name
        assert first['note'] == 'first line', 'one line only'
        assert second['event_code'] == '' and second['sponsor'] == ''
        assert second['waiting_days'] == 37

    def test_the_message_is_keyed_on_the_day_and_the_address(self, session):
        rows = [make_account_request(session)]
        message = build_queue_summary(session, rows, recipient=' nusd@example.edu ',
                                      occurrence=OCC, requested_by='task:account_queue_digest')
        assert message.kind == 'account_queue_summary'
        assert message.recipient.address == 'nusd@example.edu'
        assert message.recipient.role == 'operator'
        assert message.dedup_key == 'account_queue_summary:2026-09-14:nusd@example.edu'
        assert message.subject == 'NCAR HPC account requests: 1 waiting, 1 new'
        assert message.requested_by == 'task:account_queue_digest'

    def test_an_empty_queue_still_builds(self, session):
        context = queue_summary_context(session, [], occurrence=OCC)
        assert context['total'] == 0 and context['oldest_days'] == 0
        assert context['rows'] == [] and context['events'] == []


class TestVerifyMessage:
    def test_the_context_carries_nothing_the_visitor_typed(self, session):
        row = make_account_request(session, first_name='Call', last_name='Me-Now',
                                   verified_by=None, organization='FREE MONEY')
        row.set_verification('a' * 64, datetime(2026, 9, 16, 12, 0))
        message = build_verify_message(row, verify_url='https://sam/register/verify/t',
                                       code='123456', expires_hours=48,
                                       event_name='WRF Tutorial')
        assert set(message.context) == set(sample_context('account_verify'))
        rendered_values = ' '.join(str(v) for v in message.context.values())
        assert 'Call' not in rendered_values and 'FREE' not in rendered_values
        assert message.context['event_name'] == 'WRF Tutorial'

    def test_the_key_changes_with_every_issue(self, session):
        row = make_account_request(session, verified_by=None)
        kwargs = dict(verify_url='u', code='000000', expires_hours=1)
        first = build_verify_message(row, **kwargs).dedup_key
        row.set_verification('b' * 64, datetime(2026, 9, 16, 13, 0))
        second = build_verify_message(row, **kwargs).dedup_key
        assert first != second
        assert second == f'account_verify:{row.account_request_id}:2026-09-16T13:00:00'

    def test_the_addressee_and_provenance(self, session):
        row = make_account_request(session, email='who@example.edu', verified_by=None)
        message = build_verify_message(row, verify_url='u', code='1', expires_hours=1)
        assert message.recipient.address == 'who@example.edu'
        assert message.recipient.role == 'user'
        assert message.entity == ('account_request', row.account_request_id)
        assert message.requested_by == 'self'
        assert message.subject == ACCOUNT_KIND_SUBJECTS['account_verify']
