"""xras_sweep creates the NSF contracts approved, unapplied XRAS work cites (Ben 2026-11-07)."""

import logging
from datetime import date
from types import SimpleNamespace

import pytest

import sam.integration.awards as awards
from sam.integration.awards.base import AwardRecord, PersonRef
from sam.integration.xras import XrasRequestOverride
from sam.projects.contracts import Contract
from scheduling.tasks import xras_sweep
from factories import make_contract, make_contract_source, make_user
from factories._seq import next_seq


def _ctx():
    return SimpleNamespace(logger=logging.getLogger('test.contracts'))


def _number():
    return f"AGS-84{next_seq('MAKE')[4:]}"


def _request():
    return f"NCAR{next_seq('rq')[2:]}"


def _record(number, pi_name='Pat Doe'):
    return AwardRecord(provenance='NSF Awards API', contract_number=number,
                       title='Clouds and more clouds', start_date=date(2026, 9, 1),
                       end_date=date(2029, 8, 31), url='https://nsf.example/a',
                       pi=PersonRef(name=pi_name, email=f"{next_seq('pi')}@uni.example"),
                       monitor=None)


def _target(number, request_number=None, pi_name='Pat Doe', usernames=()):
    return {'number': number, 'pi_name': pi_name, 'usernames': list(usernames),
            'request_number': request_number or _request()}


@pytest.fixture
def armed(monkeypatch, session):
    """The lever on, NSF present, and a cache the test fills; nothing fetches."""
    monkeypatch.setenv('XRAS_AUTO_CONTRACTS', '1')
    make_contract_source(session, name='NSF')
    cache = {}
    monkeypatch.setattr(awards, 'award_is_cached', lambda source, number, exact=True: True)
    monkeypatch.setattr(awards, 'cached_award',
                        lambda source, number, exact=True: cache.get(number))
    return cache


class TestCreate:

    def test_a_cached_award_becomes_the_contract(self, session, armed):
        number, request_number = _number(), _request()
        armed[number] = _record(number)
        detail = {}
        unblocked = xras_sweep._create_contracts(
            _ctx(), session, [_target(number, request_number)], detail)
        contract = Contract.get_by_number(session, number)
        assert contract is not None and contract.pi_contact.name == 'Pat Doe'
        assert detail['contracts']['created'] == [number] and unblocked == {request_number}

    def test_a_roster_user_is_the_pi(self, session, armed):
        user = make_user(session, first_name='Ian', last_name=f"Grooms{next_seq('gr')}")
        number = _number()
        armed[number] = _record(number, pi_name=f'Ian {user.last_name}')
        xras_sweep._create_contracts(_ctx(), session, [_target(
            number, pi_name=f'Ian {user.last_name}', usernames=[user.username])], {})
        assert Contract.get_by_number(session, number).principal_investigator_user_id \
            == user.user_id

    def test_two_requests_citing_one_award_make_one_contract(self, session, armed):
        number = _number()
        armed[number] = _record(number)
        first, second = _request(), _request()
        detail = {}
        unblocked = xras_sweep._create_contracts(
            _ctx(), session, [_target(number, first), _target(number, second)], detail)
        assert detail['contracts']['created'] == [number] and unblocked == {first, second}

    def test_refusals_stay_blockers(self, session, armed):
        mismatch, absent = _number(), _number()
        armed[mismatch] = _record(mismatch, pi_name='Sam Roe')
        detail = {}
        unblocked = xras_sweep._create_contracts(
            _ctx(), session, [_target(mismatch), _target(absent)], detail)
        assert detail['contracts'] == {'created': [],
                                       'refused': {'pi_mismatch': 1, 'no_award': 1}}
        assert unblocked == set() and Contract.get_by_number(session, mismatch) is None

    def test_an_existing_contract_is_skipped(self, session, armed):
        number = _number()
        make_contract(session, contract_number=number)
        armed[number] = _record(number)
        detail = {}
        assert xras_sweep._create_contracts(_ctx(), session, [_target(number)], detail) == set()
        assert detail['contracts']['created'] == []

    def test_the_lever_off_creates_nothing(self, session, armed, monkeypatch):
        monkeypatch.setenv('XRAS_AUTO_CONTRACTS', '0')
        number = _number()
        armed[number] = _record(number)
        xras_sweep._create_contracts(_ctx(), session, [_target(number)], {})
        assert Contract.get_by_number(session, number) is None

    def test_an_uncached_award_is_fetched_within_the_cap(self, session, armed, monkeypatch):
        number = _number()
        monkeypatch.setattr(awards, 'award_is_cached', lambda *a, **k: False)
        monkeypatch.setattr(awards, 'resolve_award',
                            lambda source, n, exact=False: _record(n))
        detail = {'awards': {'warmed': xras_sweep._AWARD_WARM_CAP, 'cached': 0,
                             'unavailable': 0}}
        xras_sweep._create_contracts(_ctx(), session, [_target(number)], detail)
        assert Contract.get_by_number(session, number) is None, 'the cap is spent'
        detail['awards']['warmed'] = 0
        xras_sweep._create_contracts(_ctx(), session, [_target(number)], detail)
        assert Contract.get_by_number(session, number) is not None


def _payload(request_number, number, status='Approved', agency_id=1):
    return {'requestNumber': request_number, 'requestStatus': status,
            'grants': [{'grantNumber': number, 'fundingAgencyId': agency_id,
                        'piName': 'Pat Doe'}]}


def _row(request_number, number, *, action_status='Approved', push_state='pending',
         push_detail=None, reason='missing', agency='NSF'):
    grant = {'number': number, 'reason': reason, 'agency': agency, 'pi_name': 'Pat Doe'}
    return {'request_number': request_number,
            'roster': [{'username': 'someone', 'placeholder': False},
                       {'username': 'ph-user-x', 'placeholder': True}],
            'actions': [{'preflight': {'status': 'failed', 'action_status': action_status,
                                       'push_state': push_state, 'push_detail': push_detail,
                                       'resolved': {'unresolved_grants': [grant]}}}]}


class TestTargets:

    def test_a_pending_approved_request_is_a_target(self, session):
        request_number, number = _request(), _number()
        targets = xras_sweep.contract_targets(
            session, [_payload(request_number, number)], {request_number}, None)
        assert [(t['number'], t['request_number'], t['pi_name']) for t in targets] == [
            (number, request_number, 'Pat Doe')]

    def test_pushed_unapproved_non_nsf_and_waived_requests_are_not(self, session):
        pushed, submitted, doe, waived = (_request() for _ in range(4))
        XrasRequestOverride.set(session, request_number=waived, kind='ignore_contract',
                                created_by='op')
        payloads = [_payload(pushed, _number()), _payload(submitted, _number(), 'Submitted'),
                    _payload(doe, _number(), agency_id=99), _payload(waived, _number())]
        assert xras_sweep.contract_targets(
            session, payloads, {submitted, doe, waived}, None) == []

    def test_an_index_blocker_is_a_target_with_its_roster(self, session):
        request_number, number = _request(), _number()
        targets = xras_sweep.contract_targets(session, [], set(),
                                              [_row(request_number, number)])
        assert [(t['number'], t['usernames']) for t in targets] == [(number, ['someone'])]

    @pytest.mark.parametrize('overrides', [
        {'action_status': 'Submitted'},
        {'push_state': 'applied_inferred'},
        {'push_state': 'seen_in_log', 'push_detail': {'status': 'processed'}},
        {'reason': 'ambiguous'},
        {'agency': 'DOE'},
    ])
    def test_index_rows_that_are_not_targets(self, session, overrides):
        assert xras_sweep.contract_targets(
            session, [], set(), [_row(_request(), _number(), **overrides)]) == []

    def test_a_refused_post_is_still_a_target(self, session):
        row = _row(_request(), _number(), push_state='seen_in_log',
                   push_detail={'status': 'failed'})
        assert len(xras_sweep.contract_targets(session, [], set(), [row])) == 1
