"""``sam.queries.xras_identity_report`` — a pivot over stamped worklist rows."""

from __future__ import annotations

import pytest

from sam.queries.xras_identity_report import identity_merge_report

pytestmark = pytest.mark.unit


def _row(username, *numbers, remedy='merge', target='real', active=True,
         placeholder=True, reconciled=False, email='p@example.invalid'):
    return {
        'username': username, 'classification': 'absent', 'remedy': remedy,
        'placeholder': placeholder, 'is_reconciled': reconciled,
        'roles': ('PI',), 'sources': ['reports'], 'waiting_since': None,
        'waiting_days': 3, 'person': {'email': email},
        'merge_target': ({'username': target, 'active': active} if target else None),
        'actions': [{'request_number': n, 'source': 'reports'} for n in numbers],
    }


class TestRanking:

    def test_targets_rank_by_distinct_requests_then_username(self):
        report = identity_merge_report([
            _row('b-user-1', 'NCAR0001'),
            _row('a-user-1', 'NCAR0002', 'NCAR0003', 'NCAR0003'),
            _row('c-user-1', 'NCAR0004'),
        ])
        assert [t['username'] for t in report['targets']] == \
            ['a-user-1', 'b-user-1', 'c-user-1']
        assert report['targets'][0]['unblock_count'] == 2
        assert report['targets'][0]['sample'] == ['NCAR0002', 'NCAR0003']
        assert report['targets'][0]['target_username'] == 'real'
        assert report['targets'][0]['email'] == 'p@example.invalid'

    def test_in_view_restricts_the_count_and_drops_rows_with_nothing_in_view(self):
        report = identity_merge_report(
            [_row('a-user-1', 'NCAR0001', 'NCAR0002'), _row('b-user-1', 'NCAR0009')],
            in_view={'NCAR0002'})
        assert [t['username'] for t in report['targets']] == ['a-user-1']
        assert report['targets'][0]['unblock_count'] == 1


class TestBuckets:

    def test_an_inactive_target_is_a_reactivation_not_a_merge(self):
        report = identity_merge_report([_row('a-user-1', 'N1', remedy='reactivate',
                                             active=False)])
        assert report['targets'] == []
        assert report['reactivations'][0]['target_active'] is False

    def test_no_target_means_needs_account(self):
        report = identity_merge_report([_row('a-user-1', 'N1', remedy='create',
                                             target=None)])
        assert [n['username'] for n in report['needs_account']] == ['a-user-1']

    def test_a_real_username_is_not_a_placeholder_and_never_listed(self):
        report = identity_merge_report([_row('jsmith', 'N1', placeholder=False)])
        assert report == identity_merge_report([])


def test_an_empty_worklist_is_a_clean_empty_report():
    assert identity_merge_report([], generated_at='2026-08-25') == {
        'kind': 'xras_identity_report', 'generated_at': '2026-08-25',
        'targets': [], 'reactivations': [], 'needs_account': []}
