"""`logarchive.store`: timestamps, the cursor, finalize, retention and the volume guard."""

import gzip
import os
import time
from types import SimpleNamespace

import pytest

from logarchive import store

from logarchive_fakes import stamp


def write(path, *records, mtime=None):
    for ts, line in records:
        store.append(path, ts, line)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_timestamps_with_trimmed_zeros_order_by_value_not_by_string():
    whole, half, later = '2026-11-26T15:00:01Z', '2026-11-26T15:00:01.5Z', '2026-11-26T15:00:01.50001Z'
    assert sorted([whole, half, later]) == [later, half, whole]        # the strings run backward
    assert sorted([later, whole, half], key=store.parse_ts) == [whole, half, later]
    assert store.parse_ts(half) == (store.parse_ts(whole)[0], 500_000_000)
    assert store.parse_ts('2026-11-26T15:00:01.123456789Z')[1] == 123_456_789


def test_a_zone_offset_is_the_same_instant_as_its_utc_form():
    assert store.parse_ts('2026-11-26T08:00:01.25-07:00') == store.parse_ts('2026-11-26T15:00:01.25Z')
    assert store.parse_ts('2026-11-26T16:00:01+01:00') == store.parse_ts('2026-11-26T15:00:01Z')


@pytest.mark.parametrize('text', ['', 'unable', '2026-11-26 15:00:01', '2026-11-26T15:00:01'])
def test_a_non_timestamp_is_refused(text):
    with pytest.raises(ValueError):
        store.parse_ts(text)


def test_since_time_floors_to_the_second():
    assert store.since_time(store.parse_ts('2026-11-26T15:00:01.999999999Z')) == '2026-11-26T15:00:01Z'


def test_cursor_counts_the_lines_sharing_the_last_timestamp(tmp_path):
    path = tmp_path / 'app' / 'p__c.log'
    assert store.cursor(path) is None
    write(path, (stamp(1), 'a'), (stamp(2, '5'), 'b'), (stamp(2, '5'), 'c'), (stamp(2, '5'), 'd'))
    assert store.cursor(path) == (store.parse_ts(stamp(2, '5')), 3)


def test_cursor_reads_past_one_tail_block_when_a_timestamp_repeats(tmp_path):
    path = tmp_path / 'app' / 'p__c.log'
    write(path, (stamp(1), 'first'))
    with open(path, 'ab') as fh:
        fh.write(''.join(f'{stamp(2)} {"x" * 90}\n' for _ in range(2000)).encode())
    assert path.stat().st_size > 2 * 65536
    assert store.cursor(path) == (store.parse_ts(stamp(2)), 2000)


def test_repair_cuts_a_torn_final_line_and_nothing_else(tmp_path):
    path = write(tmp_path / 'app' / 'p__c.log', (stamp(1), 'whole'))
    whole = path.read_bytes()
    with open(path, 'ab') as fh:
        fh.write(f'{stamp(2)} half a li'.encode())
    assert store.cursor(path) == (store.parse_ts(stamp(1)), 1)   # the fragment is not a line
    store.repair(path)
    assert path.read_bytes() == whole
    store.repair(path)
    assert path.read_bytes() == whole
    store.repair(tmp_path / 'absent.log')


def test_finalize_gzips_keeps_the_write_time_and_removes_the_plain_file(tmp_path):
    path = write(tmp_path / 'app' / 'p__c.log', (stamp(1), 'a'), (stamp(2), 'b'), mtime=1_700_000_000)
    plain = path.read_bytes()
    done = store.finalize(path)
    assert done.name == 'p__c.log.gz' and not path.exists()
    assert gzip.decompress(done.read_bytes()) == plain
    assert done.stat().st_mtime == 1_700_000_000
    assert store.is_finished(path)
    assert store.finalize(path) == done   # idempotent
    assert not list(tmp_path.glob('*/*.tmp'))


def test_finalize_of_a_container_that_never_logged_leaves_an_empty_marker(tmp_path):
    path = tmp_path / 'app' / 'quiet__c.log'
    done = store.finalize(path)
    assert gzip.decompress(done.read_bytes()) == b''
    assert store.is_finished(path)


def test_tidy_removes_what_a_kill_mid_finalize_leaves(tmp_path):
    done = store.finalize(write(tmp_path / 'app' / 'p__c.log', (stamp(1), 'a')))
    twin = write(tmp_path / 'app' / 'p__c.log', (stamp(1), 'a'))
    tmp = tmp_path / 'app' / 'q__c.log.gz.tmp'
    tmp.write_bytes(b'partial')
    live = write(tmp_path / 'app' / 'r__c.log', (stamp(1), 'a'))
    store.tidy(tmp_path)
    assert done.exists() and live.exists() and not twin.exists() and not tmp.exists()


def test_prune_deletes_only_expired_finished_files(tmp_path):
    now = time.time()
    old = store.finalize(write(tmp_path / 'app' / 'old__c.log', (stamp(1), 'a'), mtime=now - 91 * 86400))
    fresh = store.finalize(write(tmp_path / 'app' / 'new__c.log', (stamp(1), 'a'), mtime=now - 89 * 86400))
    live = write(tmp_path / 'app' / 'live__c.log', (stamp(1), 'a'), mtime=now - 400 * 86400)
    assert store.prune(tmp_path, 90, now=now) == [old]
    assert not old.exists() and fresh.exists() and live.exists()


@pytest.fixture
def small_volume(tmp_path, monkeypatch):
    """A 1000-byte volume whose used space is the archive's own file sizes."""
    def statvfs(root):
        used = sum(p.stat().st_size for p in tmp_path.glob('*/*'))
        return SimpleNamespace(f_frsize=1, f_blocks=1000, f_bfree=1000 - used, f_bavail=1000 - used)
    monkeypatch.setattr(store.os, 'statvfs', statvfs)
    return tmp_path


def _sized(path, size, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    os.utime(path, (mtime, mtime))
    return path


def test_reclaim_prunes_oldest_finished_first_and_stops_under_the_low_mark(small_volume, caplog):
    root = small_volume
    live = _sized(root / 'app' / 'live__c.log', 500, 1)          # oldest of all, and live
    oldest = _sized(root / 'app' / 'a__c.log.gz', 100, 10)
    older = _sized(root / 'app' / 'b__c.log.gz', 100, 20)
    newer = _sized(root / 'app' / 'c__c.log.gz', 100, 30)
    newest = _sized(root / 'app' / 'd__c.log.gz', 120, 40)
    assert store.percent_full(root) == 92.0
    with caplog.at_level('WARNING', logger='logarchive'):
        removed = store.reclaim(root)
    assert removed == [oldest, older]                             # 92% -> 82% -> 72%
    assert live.exists() and newer.exists() and newest.exists()
    assert len(caplog.records) == 1 and 'ahead of retention' in caplog.text


def test_reclaim_does_nothing_below_the_high_mark_and_never_touches_a_live_file(small_volume, caplog):
    root = small_volume
    done = _sized(root / 'app' / 'a__c.log.gz', 100, 10)
    live = _sized(root / 'app' / 'live__c.log', 790, 1)
    assert store.reclaim(root) == [] and done.exists()            # 89%
    live.write_bytes(b'x' * 950)
    done.unlink()
    with caplog.at_level('WARNING', logger='logarchive'):
        assert store.reclaim(root) == []                          # 95% and nothing finished
    assert live.exists() and live.stat().st_size == 950
    assert len(caplog.records) == 1


def test_status_reports_size_volume_age_and_the_largest_live_file(small_volume):
    root, now = small_volume, 1_800_000_000
    _sized(root / 'web' / 'a__c.log', 300, now - 30)
    _sized(root / 'web' / 'b__c.log', 120, now - 5)
    _sized(root / 'tasks' / 'job__c.log.gz', 80, now - 3 * 86400)
    store.beat(root)
    info = store.status(root, now=now)
    assert info['files'] == 3 and info['bytes'] == 500
    assert info['volume_bytes'] == 1000 and info['volume_free'] == 500 and info['percent_full'] == 50.0
    assert info['newest_write_age'] == 5.0
    assert info['oldest'] == time.strftime('%Y-%m-%d', time.gmtime(now - 3 * 86400))
    assert info['largest_live'] == {'name': 'web/a__c.log', 'bytes': 300}
    assert sorted(info['live_files']) == ['web/a__c.log', 'web/b__c.log']


def test_status_of_an_empty_archive_has_no_ages(small_volume):
    info = store.status(small_volume)
    assert info['files'] == 0 and info['newest_write_age'] is None
    assert info['oldest'] is None and info['largest_live'] is None and info['heartbeat_age'] is None


def test_heartbeat_age_is_none_until_the_first_beat(tmp_path):
    assert store.heartbeat_age(tmp_path) is None
    store.beat(tmp_path)
    assert store.heartbeat_age(tmp_path) < 5
    assert store.heartbeat_age(tmp_path, now=time.time() + 300) > 295
