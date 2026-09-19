"""`contract_unblock_report` — the contract-blockers pivot over the readiness snapshot.

Reads `resolved.unresolved_grants` (the structured channel), never the 422 string,
and re-checks every number against the live `contract` table.
"""

from __future__ import annotations

import pytest
from factories import make_contract

from sam.queries.xras_contract_report import (contract_unblock_report,
                                              suggested_source)

pytestmark = pytest.mark.unit


def _grant(number, *, core=None, reason='missing', candidates=(), agency=None,
           title=None):
    return {'number': number, 'core': core or number.split('-')[-1], 'reason': reason,
            'candidates': list(candidates), 'agency': agency, 'title': title,
            'pi_name': 'A. Person', 'begin_date': '2026-01-01', 'end_date': '2027-12-31',
            'is_pending': False}


def _entry(request_number, *grants, pi='pi-one', rollup='failed', status='failed',
           activity='2026-08-20'):
    return {
        'request_number': request_number,
        'preflight_rollup': rollup,
        'pi': {'username': pi},
        'activity_date': activity,
        'actions': [{'action_id': 1,
                     'preflight': {'status': status, 'messages': ['x'],
                                   'resolved': {'unresolved_grants': list(grants)}}}],
    }


def _snapshot(*entries):
    return {'generated_at': '2026-08-24', 'statuses': ['Approved'],
            'extra_statuses': {}, 'rows': list(entries)}


class TestRanking:

    def test_numbers_rank_by_how_many_actions_cite_them(self, session):
        report = contract_unblock_report(session, _snapshot(
            _entry('NCAR0001', _grant('NSF-9980001')),
            _entry('NCAR0002', _grant('NSF-9980001'), pi='pi-two', activity='2026-08-10'),
            _entry('NCAR0003', _grant('ISS 25-643', core='ISS 25-643'))))
        assert report['kind'] == 'xras_contract_report'
        assert report['actions_seen'] == 3
        assert [t['number'] for t in report['targets']] == ['NSF-9980001', 'ISS 25-643']
        top = report['targets'][0]
        assert top['unblock_count'] == 2
        assert top['sample'] == ['NCAR0001', 'NCAR0002']
        assert top['pis'] == ['pi-one', 'pi-two']
        assert top['oldest_activity'] == '2026-08-10'

    def test_the_raw_number_is_shown_never_the_core(self, session):
        [t] = contract_unblock_report(session, _snapshot(
            _entry('NCAR0004', _grant('PRJ013992 BWI', core='013992'))))['targets']
        assert (t['number'], t['core']) == ('PRJ013992 BWI', '013992')

    def test_spelling_variants_are_folded_into_one_target(self, session):
        [t] = contract_unblock_report(session, _snapshot(
            _entry('NCAR0005', _grant('OCE- 9980005', core='9980005')),
            _entry('NCAR0006', _grant('OCE-9980005', core='9980005'))))['targets']
        assert t['unblock_count'] == 2


class TestAwardShape:

    def test_an_nsf_award_number_suggests_the_nsf_source(self, session):
        [t] = contract_unblock_report(session, _snapshot(
            _entry('NCAR0007', _grant('2423211', core='2423211',
                                      agency='National Science Foundation'))))['targets']
        assert t['award_like'] is True and t['suggested_source'] == 'NSF'

    def test_a_reference_with_no_digit_run_suggests_nothing(self, session):
        [t] = contract_unblock_report(session, _snapshot(
            _entry('NCAR0008', _grant('001368-00183', core='001368-00183',
                                      agency='National Science Foundation'))))['targets']
        assert t['award_like'] is False and t['suggested_source'] is None

    @pytest.mark.parametrize('agency, award_like, expected', [
        ('NSF', True, 'NSF'), ('national science foundation', True, 'NSF'),
        ('NASA', True, None), ('NSF', False, None), (None, True, None)])
    def test_the_source_rule(self, agency, award_like, expected):
        assert suggested_source(agency, award_like) == expected


class TestWhatDropsOut:

    def test_a_contract_created_since_the_sweep_drops_out(self, session):
        make_contract(session, contract_number='NSF-9980009')
        report = contract_unblock_report(session, _snapshot(
            _entry('NCAR0009', _grant('NSF-9980009'))))
        assert report['targets'] == [] and report['variants'] == []
        assert report['actions_seen'] == 1

    def test_a_single_suffix_match_counts_as_resolved(self, session):
        make_contract(session, contract_number='AGS-9980010')
        report = contract_unblock_report(session, _snapshot(
            _entry('NCAR0010', _grant('NSF-9980010'))))
        assert report['targets'] == []

    def test_a_tie_is_a_variant_never_a_target(self, session):
        make_contract(session, contract_number='9980011')
        make_contract(session, contract_number='PLR-9980011')
        report = contract_unblock_report(session, _snapshot(
            _entry('NCAR0011', _grant('NSF-9980011', reason='ambiguous',
                                      candidates=['9980011', 'PLR-9980011']))))
        assert report['targets'] == []
        [v] = report['variants']
        assert v['number'] == 'NSF-9980011'
        assert v['candidates'] == ['9980011', 'PLR-9980011']

    def test_a_failure_without_grants_is_ignored(self, session):
        entry = _entry('NCAR0012')
        entry['actions'][0]['preflight']['resolved'] = None
        report = contract_unblock_report(session, _snapshot(entry))
        assert report['actions_seen'] == 0 and report['targets'] == []

    def test_a_rechecked_action_contributes_nothing(self, session):
        report = contract_unblock_report(session, _snapshot(
            _entry('NCAR0013', _grant('NSF-9980013'), rollup='ready', status='rechecked')))
        assert report['targets'] == []


def test_an_empty_snapshot_is_a_clean_empty_report(session):
    report = contract_unblock_report(session, None)
    assert report == {'kind': 'xras_contract_report', 'generated_at': None,
                      'actions_seen': 0, 'targets': [], 'variants': []}
