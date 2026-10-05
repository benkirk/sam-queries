"""Pure-function tests for the dormancy review join (no database)."""

from datetime import datetime, timedelta

import pytest

from sam.queries.last_seen_review import (
    CURRENT, CURRENT_WINDOWS, NEVER, NOT_IN_SAM, bucket_for, is_current, review,
)

NOW = datetime(2026, 9, 27, 12, 0)


def ago(days):
    return NOW - timedelta(days=days)


@pytest.mark.parametrize('days, bucket', [
    (0, 'recent'), (29, 'recent'), (30, 'year'), (364, 'year'),
    (365, 'dormant'), (1094, 'dormant'), (1095, 'stale'), (5000, 'stale'),
    (-1, 'recent'),
])
def test_bucket_edges(days, bucket):
    assert bucket_for(ago(days), NOW) == bucket


def test_no_row_is_never():
    assert bucket_for(None, NOW) == NEVER


class TestCurrent:
    """A sighting inside the per-kind window is "now"; one second past it is not."""

    @pytest.mark.parametrize('kind', sorted(CURRENT_WINDOWS))
    def test_window_edge_per_kind(self, kind):
        window = CURRENT_WINDOWS[kind]
        assert is_current(NOW - window, kind, NOW)
        assert not is_current(NOW - window - timedelta(seconds=1), kind, NOW)

    def test_every_kind_shares_the_window(self):
        assert len(set(CURRENT_WINDOWS.values())) == 1      # webapp does not float above the snapshots

    def test_bucket_needs_a_kind(self):
        assert bucket_for(NOW - timedelta(minutes=1), NOW, 'pbs') == CURRENT
        assert bucket_for(NOW - timedelta(minutes=1), NOW) == 'recent'
        assert bucket_for(NOW - timedelta(minutes=1), NOW, 'unknown') == 'recent'

    def test_review_counts_current_once_and_not_as_recent(self):
        ledger = {**LEDGER, 'alice': {'webapp': (NOW - timedelta(minutes=2), 'samuel')},
                  'bob': {'pbs': (NOW - timedelta(minutes=9), 'derecho')}}
        rows, buckets, kinds = review(DIRECTORY, ledger, now=NOW)
        assert buckets[CURRENT] == 2 and buckets['recent'] == 0
        assert {r.username for r in rows if r.current} == {'alice', 'bob'}
        assert kinds['webapp'] == 1 and kinds['pbs'] == 1
        current_rows, _, _ = review(DIRECTORY, ledger, now=NOW, bucket=CURRENT)
        assert [r.username for r in current_rows] == ['alice', 'bob']

    def test_an_old_row_is_not_current(self):
        rows, _, _ = _review()
        assert not any(r.current for r in rows)


DIRECTORY = {
    'alice': ('Alice A', True, False),
    'bob': ('Bob B', True, False),
    'carol': ('Carol C', False, False),
    'dave': ('Dave D', True, True),
}
LEDGER = {
    'alice': {'webapp': (ago(1), 'samuel'), 'pbs': (ago(400), 'cheyenne')},
    'bob': {'pbs': (ago(2000), 'yellowstone')},
    'carol': {'login': (ago(40), 'casper')},
    'svc': {'login': (ago(3), 'derecho')},
}


def _review(**kw):
    return review(DIRECTORY, LEDGER, now=NOW, **kw)


class TestReview:

    def test_newest_across_kinds(self):
        rows, buckets, _ = _review()
        by_user = {r.username: r for r in rows}
        assert (by_user['alice'].kind, by_user['alice'].system) == ('webapp', 'samuel')
        assert by_user['alice'].bucket == 'recent'
        assert by_user['dave'].bucket == NEVER and by_user['dave'].status == 'locked'
        assert by_user['carol'].status == 'inactive'
        assert buckets == {'recent': 1, 'year': 1, 'stale': 1, NEVER: 1}

    def test_kind_narrows_the_sighting(self):
        rows, buckets, _ = _review(kind='pbs')
        by_user = {r.username: r for r in rows}
        assert by_user['alice'].system == 'cheyenne'
        assert by_user['alice'].bucket == 'dormant'
        assert by_user['carol'].bucket == NEVER
        assert buckets[NEVER] == 2

    def test_bucket_filters_rows_but_not_its_own_counts(self):
        rows, buckets, _ = _review(bucket='stale')
        assert [r.username for r in rows] == ['bob']
        assert sum(buckets.values()) == 4

    def test_several_buckets_are_ored(self):
        one, _, _ = _review(bucket='stale')
        other, _, _ = _review(bucket=NEVER)
        both, buckets, _ = _review(bucket=['stale', NEVER])
        assert {r.username for r in both} == (
            {r.username for r in one} | {r.username for r in other})
        assert sum(buckets.values()) == 4
        assert _review(bucket=[])[0] == _review()[0]

    def test_kind_counts_are_self_excluding(self):
        _, _, kinds = _review()
        assert kinds == {'webapp': 1, 'pbs': 2, 'login': 1}
        _, _, kinds = _review(bucket='dormant')
        assert kinds == {'pbs': 1}          # alice, bucketed on pbs alone
        _, _, kinds = _review(bucket=NEVER)
        assert kinds['pbs'] == 2 and kinds['webapp'] == 3

    def test_unlisted_ledger_users_only_when_asked(self):
        rows, _, _ = _review()
        assert 'svc' not in {r.username for r in rows}
        rows, _, _ = _review(include_unlisted=True)
        svc = next(r for r in rows if r.username == 'svc')
        assert svc.status == NOT_IN_SAM and svc.bucket == 'recent'

    def test_search_matches_username_or_name(self):
        rows, buckets, _ = _review(search='CAROL')
        assert [r.username for r in rows] == ['carol']
        assert sum(buckets.values()) == 1
        rows, _, _ = _review(search='ob')
        assert [r.username for r in rows] == ['bob']

    def test_sort_puts_never_seen_oldest(self):
        rows, _, _ = _review()
        assert [r.username for r in rows] == ['alice', 'carol', 'bob', 'dave']
        rows, _, _ = _review(sort_dir='asc')
        assert [r.username for r in rows] == ['dave', 'bob', 'carol', 'alice']
        rows, _, _ = _review(sort_by='username', sort_dir='asc')
        assert [r.username for r in rows] == ['alice', 'bob', 'carol', 'dave']


def test_ties_on_last_seen_sort_by_username_ascending():
    same = {'pbs': (ago(5), 'casper')}
    directory = {u: (u, True, False) for u in ('zed', 'amy', 'kim')}
    rows, _, _ = review(directory, {u: same for u in directory}, now=NOW)
    assert [r.username for r in rows] == ['amy', 'kim', 'zed']
