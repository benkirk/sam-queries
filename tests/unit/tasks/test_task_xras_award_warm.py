"""xras_sweep's award warm-up: NSF awards for missing contracts reach the cache, capped."""

import logging
from types import SimpleNamespace

import pytest

import sam.integration.awards as awards
from scheduling.tasks import xras_sweep
from factories import make_contract
from factories._seq import next_seq


def _ctx():
    return SimpleNamespace(logger=logging.getLogger('test.warm'))


def _payload(*numbers, agency_id=1):
    return {'grants': [{'grantNumber': n, 'fundingAgencyId': agency_id} for n in numbers]}


def _number():
    return f"AGS-85{next_seq('WARM')[4:]}"


@pytest.fixture
def fetched(monkeypatch):
    monkeypatch.setenv('XRAS_AUTO_CONTRACTS', '1')
    calls = []
    monkeypatch.setattr(awards, 'award_is_cached', lambda *a, **k: False)
    monkeypatch.setattr(awards, 'resolve_award',
                        lambda source, number, exact=False: calls.append(number))
    return calls


def test_a_missing_nsf_contract_is_fetched(session, fetched):
    number = _number()
    detail = {}
    xras_sweep._warm_awards(_ctx(), session, [_payload(number)], detail)
    assert fetched == [number] and detail['awards']['warmed'] == 1


def test_a_known_contract_and_a_non_nsf_grant_are_skipped(session, fetched):
    known = _number()
    make_contract(session, contract_number=known)
    xras_sweep._warm_awards(_ctx(), session,
                            [_payload(known), _payload(_number(), agency_id=99)], {})
    assert fetched == []


def test_the_lever_off_fetches_nothing(session, fetched, monkeypatch):
    monkeypatch.setenv('XRAS_AUTO_CONTRACTS', '0')
    xras_sweep._warm_awards(_ctx(), session, [_payload(_number())], {})
    assert fetched == []


def test_the_cap(session, fetched, monkeypatch):
    monkeypatch.setattr(xras_sweep, '_AWARD_WARM_CAP', 2)
    xras_sweep._warm_awards(_ctx(), session, [_payload(*[_number() for _ in range(4)])], {})
    assert len(fetched) == 2


def test_an_outage_stops_the_run_without_raising(session, monkeypatch):
    monkeypatch.setenv('XRAS_AUTO_CONTRACTS', '1')
    monkeypatch.setattr(awards, 'award_is_cached', lambda *a, **k: False)

    def _down(*a, **k):
        raise awards.AwardSourceUnavailable('down')
    monkeypatch.setattr(awards, 'resolve_award', _down)
    detail = {}
    xras_sweep._warm_awards(_ctx(), session, [_payload(_number(), _number())], detail)
    assert detail['awards']['unavailable'] == 1


def test_with_named_agencies_names_only_nsf():
    from sam.integration.xras_api.vocabulary import with_named_agencies
    payload = {'grants': [{'grantNumber': 'a', 'fundingAgencyId': 1},
                          {'grantNumber': 'b', 'fundingAgencyId': 7},
                          {'grantNumber': 'c', 'fundingAgency': 'DOE', 'fundingAgencyId': 1}]}
    agencies = [g.get('fundingAgency') for g in with_named_agencies(payload)['grants']]
    assert agencies == ['NSF', None, 'DOE']
    assert 'fundingAgency' not in payload['grants'][0]


def test_a_cached_answer_is_not_fetched_again(session, fetched, monkeypatch):
    """A definite "no such award" is cached as None; it counts as cached, not warmed."""
    monkeypatch.setattr(awards, 'award_is_cached', lambda *a, **k: True)
    detail = {}
    xras_sweep._warm_awards(_ctx(), session, [_payload(_number())], detail)
    assert fetched == [] and detail['awards'] == {'warmed': 0, 'cached': 1, 'unavailable': 0}
