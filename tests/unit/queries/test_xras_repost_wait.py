"""The re-post wait: refused XRAS posts linked to the post that applied them."""
from datetime import datetime, timedelta

import pytest

from factories import (make_account_request, make_contract, make_external_ticket,
                       make_notification_log, make_user, make_xras_action,
                       make_xras_remediation_event, next_int, next_seq)
from sam.queries import xras_repost_wait as rw
from sam.queries.xras_request_tickets import ENTITY_TYPE, LEDGER_KIND
from sam.xras import errors

# Far from any committed audit row another test may leave behind.
T0 = datetime(2031, 3, 2, 9, 0)
SINCE, UNTIL = T0 - timedelta(days=1), T0 + timedelta(days=30)


def h(hours):
    return T0 + timedelta(hours=hours)


def rid():
    return 7_000_000 + next_int('repost_rid')


def post(session, when, status, *, request_id, action_id, action_type='New',
         errors_=None, projcode=None, **kw):
    row = make_xras_action(
        session, status=status, action_type=action_type,
        http_status=422 if status in ('failed', 'duplicate') else 200,
        request_number=next_seq('NCAR'), errors=errors_, received_time=when,
        projcode_result=projcode, action_id=action_id, **kw)
    row.request_id = request_id
    session.flush()
    return row


def episodes(session):
    return rw.repost_wait_episodes(session, since=SINCE, until=UNTIL)


def one(session, request_id):
    found = [e for e in episodes(session) if e.request_id == request_id]
    assert len(found) == 1, found
    return found[0]


def test_placeholder_merge_splits_the_wait_into_both_phases(session):
    r, placeholder = rid(), next_seq('jane-user-')
    post(session, h(0), 'failed', request_id=r, action_id=r,
         errors_=[errors.pi_not_in_database(placeholder)])
    make_xras_remediation_event(session, status='verified', username=placeholder,
                                target_username='jane', when=h(5))
    ticket = make_external_ticket(session, entity_type=ENTITY_TYPE, entity_id=r,
                                  origin='created', when=h(0.5))
    make_notification_log(session, kind=LEDGER_KIND, channel='ticket',
                          entity_type=ENTITY_TYPE, entity_id=r,
                          dedup_key=f'xras_ticket:{r}:merged:{placeholder}', when=h(5.25))
    post(session, h(29), 'processed', request_id=r, action_id=r, projcode='UXYZ0001')

    e = one(session, r)
    assert (e.refusals, e.linked_by, e.projcode) == (1, 'action_id', 'UXYZ0001')
    assert e.blockers[0].kind == 'placeholder' and e.blockers[0].source == 'merge_person'
    assert e.cleared_at == h(5)
    ph = e.phases(UNTIL)
    assert (ph['a'], ph['b'], ph['c']) == (timedelta(hours=5), timedelta(hours=24),
                                           timedelta(hours=29))
    assert (e.ticket_key, e.ticket_opened, e.repost_asked) == (ticket.ticket_key, h(0.5), h(5.25))


def test_account_request_merge_also_clears_a_placeholder(session):
    r, placeholder = rid(), next_seq('ann-user-')
    post(session, h(0), 'failed', request_id=r, action_id=r,
         errors_=[errors.username_missing(placeholder)])
    request = make_account_request(session, purpose='submission', xras_username=placeholder)
    request.merged_at = h(3)
    session.flush()
    post(session, h(4), 'processed', request_id=r, action_id=r)
    e = one(session, r)
    assert (e.cleared_at, e.blockers[0].source) == (h(3), 'account_request.merged_at')


def test_several_refusals_then_a_new_action_id_links_by_request(session):
    r, grant = rid(), next_seq('AGS-')
    core = f'{900000 + next_int("repost_core")}'
    lines = [errors.cannot_find_contract(grant, core)]
    for n, hour in enumerate((0, 2, 6)):
        post(session, h(hour), 'failed', request_id=r, action_id=r * 10 + n,
             action_type='Renewal', errors_=lines)
    contract = make_contract(session, contract_number=grant)
    contract.creation_time = h(7)
    session.flush()
    post(session, h(10), 'processed', request_id=r, action_id=r * 10 + 9,
         action_type='Renewal')

    e = one(session, r)
    assert (e.refusals, e.last_refused, e.linked_by) == (3, h(6), 'request_id')
    assert (e.blockers[0].kind, e.blockers[0].source) == ('contract', 'contract.creation_time')
    assert e.phases(UNTIL)['b'] == timedelta(hours=3)


def test_still_waiting_without_a_clearing_signal_reports_its_age(session):
    r = rid()
    post(session, h(0), 'failed', request_id=r, action_id=r,
         errors_=[errors.mnemonic_internal_failed('jdoe', 'CISL')])
    e = one(session, r)
    assert e.applied_at is None and e.cleared_at is None
    assert e.phases(UNTIL)['age'] == UNTIL - h(0)


def test_cleared_but_not_reposted_is_counted_apart(session):
    r, name = rid(), next_seq('rpw')
    post(session, h(0), 'failed', request_id=r, action_id=r, action_type='Supplement',
         errors_=[errors.username_missing(name)])
    make_user(session, username=name, creation_time=h(1))
    e = one(session, r)
    assert (e.cleared_at, e.blockers[0].source) == (h(1), 'users.creation_time')

    report = rw.repost_wait_report(session, since=SINCE, until=UNTIL)
    assert report['waiting']['cleared_not_reposted'] >= 1
    assert report['by_action_type']['Supplement']['waiting'] >= 1


def test_a_signal_outside_the_episode_does_not_clear_it(session):
    r, name = rid(), next_seq('rpw')
    make_user(session, username=name, creation_time=h(-2))      # existed already
    post(session, h(0), 'failed', request_id=r, action_id=r,
         errors_=[errors.username_missing(name)])
    post(session, h(1), 'processed', request_id=r, action_id=r)
    e = one(session, r)
    assert e.cleared_at is None and e.phases(UNTIL)['c'] == timedelta(hours=1)


def test_a_refusal_after_the_apply_opens_a_second_episode(session):
    r = rid()
    lines = [errors.mnemonic_internal_failed('jdoe', 'CISL')]
    post(session, h(0), 'failed', request_id=r, action_id=r, errors_=lines)
    post(session, h(2), 'processed', request_id=r, action_id=r)
    post(session, h(5), 'failed', request_id=r, action_id=r + 1, errors_=lines)
    found = [e for e in episodes(session) if e.request_id == r]
    assert [(e.first_refused, e.applied_at) for e in found] == [(h(0), h(2)), (h(5), None)]


def test_an_episode_begun_before_the_window_is_not_cut_short(session):
    r = rid()
    lines = [errors.mnemonic_internal_failed('jdoe', 'CISL')]
    post(session, SINCE - timedelta(days=1), 'failed', request_id=r, action_id=r, errors_=lines)
    post(session, h(0), 'failed', request_id=r, action_id=r, errors_=lines)
    post(session, h(10), 'processed', request_id=r, action_id=r)
    assert [e for e in episodes(session) if e.request_id == r] == []
    found = [e for e in rw.repost_wait_episodes(session, since=SINCE - timedelta(days=2),
                                                until=UNTIL) if e.request_id == r]
    assert [(e.first_refused, e.refusals) for e in found] == [(SINCE - timedelta(days=1), 2)]


def test_rechecks_and_duplicates_are_not_refusals(session):
    r = rid()
    first = post(session, h(0), 'failed', request_id=r, action_id=r,
                 errors_=[errors.missing_title()])
    post(session, h(1), 'failed', request_id=r, action_id=r,
         source_action_id=first.xras_action_log_id, errors_=[errors.missing_title()])
    post(session, h(2), 'duplicate', request_id=r, action_id=r)
    e = one(session, r)
    assert e.refusals == 1 and e.kinds == ['other']


def test_report_groups_by_type_and_blocker(session):
    r1, r2 = rid(), rid()
    ph = next_seq('bob-user-')
    post(session, h(0), 'failed', request_id=r1, action_id=r1,
         errors_=[errors.pi_not_in_database(ph), errors.missing_title()])
    post(session, h(3), 'processed', request_id=r1, action_id=r1)
    post(session, h(0), 'failed', request_id=r2, action_id=r2, action_type='Renewal',
         errors_=[errors.pi_not_in_database(next_seq('cy-user-'))])

    report = rw.repost_wait_report(session, since=SINCE, until=UNTIL, include_episodes=True)
    assert report['by_blocker']['placeholder']['episodes'] >= 2
    assert report['by_blocker']['other']['applied'] >= 1
    assert report['by_action_type']['New']['c']['n'] >= 1
    assert report['waiting']['count'] >= 1
    assert {e['request_id'] for e in report['episodes']} >= {r1, r2}


@pytest.mark.parametrize('hours,expected', [
    ([], {'n': 0, 'median_h': None, 'p90_h': None, 'max_h': None}),
    ([1], {'n': 1, 'median_h': 1.0, 'p90_h': 1.0, 'max_h': 1.0}),
    ([4, 1, 3, 2], {'n': 4, 'median_h': 2.5, 'p90_h': 4.0, 'max_h': 4.0}),
    (list(range(1, 11)), {'n': 10, 'median_h': 5.5, 'p90_h': 9.0, 'max_h': 10.0}),
])
def test_wait_stats(hours, expected):
    assert rw.wait_stats(timedelta(hours=x) for x in hours) == expected


def test_the_runner_prints_a_table_and_json(session, capsys):
    import importlib.util
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / 'scripts' / 'xras_repost_wait.py'
    spec = importlib.util.spec_from_file_location('xras_repost_wait_script', path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    r = rid()
    post(session, h(0), 'failed', request_id=r, action_id=r, errors_=[errors.missing_title()])
    report = rw.repost_wait_report(session, since=SINCE, until=UNTIL, include_episodes=True)
    script.print_table(report, episodes=True)
    out = capsys.readouterr().out
    assert 'By action type' in out and 'Still waiting:' in out
    assert json.loads(json.dumps(report, default=str))['waiting']['count'] >= 1
