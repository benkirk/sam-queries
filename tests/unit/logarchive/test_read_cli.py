"""`logarchive.read` and the `cat` / `status` commands."""

import json
import os
import time

import pytest

from logarchive import read, store
from logarchive.__main__ import main

from logarchive_fakes import stamp


def write(root, app, pod_name, records, *, finish=False):
    path = store.log_path(root, app, pod_name, 'app')
    for ts, line in records:
        store.append(path, ts, line)
    return store.finalize(path) if finish else path


@pytest.fixture
def archive(tmp_path):
    """Two webapp pods across a roll (one finished, one live) and one finished task pod."""
    write(tmp_path, 'web', 'web-old', [(stamp(1), 'old 1'), (stamp(3, '5'), 'old 3.5'), (stamp(5), 'old 5')], finish=True)
    write(tmp_path, 'web', 'web-new', [(stamp(2), 'new 2'), (stamp(3, '25'), 'new 3.25'), (stamp(9), 'new 9')])
    write(tmp_path, 'tasks', 'job-1', [(stamp(4), '{"task": "x"}')], finish=True)
    return tmp_path


def cat(capsysbinary, root, *args):
    assert main(['--root', str(root), 'cat', *args]) == 0
    return capsysbinary.readouterr().out.decode().splitlines()


def test_cat_merges_pods_in_time_order_across_plain_and_gzipped_files(archive, capsysbinary):
    assert cat(capsysbinary, archive, '--app', 'web') == [
        'old 1', 'new 2', 'new 3.25', 'old 3.5', 'old 5', 'new 9']


def test_cat_default_output_is_the_raw_line_and_timestamps_are_opt_in(archive, capsysbinary):
    assert cat(capsysbinary, archive, '--app', 'tasks') == ['{"task": "x"}']
    assert cat(capsysbinary, archive, '--app', 'tasks', '--timestamps') == [f'{stamp(4)} {{"task": "x"}}']


def test_cat_without_an_app_reads_every_app_and_repeats_combine(archive, capsysbinary):
    everything = ['old 1', 'new 2', 'new 3.25', 'old 3.5', '{"task": "x"}', 'old 5', 'new 9']
    assert cat(capsysbinary, archive) == everything
    assert cat(capsysbinary, archive, '--app', 'web', '--app', 'tasks') == everything


def test_cat_window_is_inclusive_of_since_and_until(archive, capsysbinary):
    assert cat(capsysbinary, archive, '--since', stamp(2), '--until', stamp(4)) == [
        'new 2', 'new 3.25', 'old 3.5', '{"task": "x"}']


def test_cat_of_a_missing_root_prints_nothing(tmp_path, capsysbinary):
    assert cat(capsysbinary, tmp_path / 'absent') == []


def test_a_plain_file_left_beside_its_gzip_is_read_once(archive):
    store.append(store.log_path(archive, 'tasks', 'job-1', 'app'), stamp(4), '{"task": "x"}')
    assert [line for _, line in read.iter_lines(archive, ['tasks'])] == ['{"task": "x"}']


def test_files_written_before_the_window_are_skipped_by_mtime(archive):
    old = store.finished_path(store.log_path(archive, 'web', 'web-old', 'app'))
    since = store.parse_ts(stamp(0))[0]
    os.utime(old, (since - 3600, since - 3600))
    assert [line for _, line in read.iter_lines(archive, ['web'], since=since)] == ['new 2', 'new 3.25', 'new 9']


def test_the_merge_opens_a_file_only_when_it_is_reached(tmp_path, monkeypatch):
    """96 task pods a day must not mean 96 open files."""
    for n in range(40):
        write(tmp_path, 'tasks', f'job-{n:02d}', [(stamp(n * 10), f'{n} a'), (stamp(n * 10 + 1), f'{n} b')], finish=True)
    held, peak, real = set(), [0], read._open

    class Tracked:
        def __init__(self, fh):
            self.fh = fh
            held.add(self)
            peak[0] = max(peak[0], len(held))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            held.discard(self)
            return self.fh.__exit__(*exc)

        def __iter__(self):
            return iter(self.fh)

    monkeypatch.setattr(read, '_open', lambda path: Tracked(real(path)))
    got = [line for _, line in read.iter_lines(tmp_path, ['tasks'])]
    assert got == [f'{n} {x}' for n in range(40) for x in 'ab']
    assert peak[0] <= 2


def test_parse_when_reads_a_duration_ago_or_an_absolute_time():
    assert read.parse_when('48h', now=1_000_000) == 1_000_000 - 48 * 3600
    assert read.parse_when('7d', now=1_000_000) == 1_000_000 - 7 * 86400
    assert read.parse_when('2026-11-26T15:00:01.5Z') == store.parse_ts('2026-11-26T15:00:01Z')[0] + 0.5
    with pytest.raises(ValueError):
        read.parse_when('yesterday')


def test_status_max_age_exits_non_zero_on_a_stale_or_missing_heartbeat(tmp_path, capsys):
    assert main(['--root', str(tmp_path), 'status', '--max-age', '120']) == 1
    assert 'never' in capsys.readouterr().out
    store.beat(tmp_path)
    assert main(['--root', str(tmp_path), 'status', '--max-age', '120']) == 0
    stale = time.time() - 600
    os.utime(tmp_path / store.HEARTBEAT, (stale, stale))
    assert main(['--root', str(tmp_path), 'status', '--max-age', '120']) == 1
    assert 'STALE' in capsys.readouterr().out


def test_status_json_carries_the_watch_lines_fields(archive, capsys):
    store.beat(archive)
    assert main(['--root', str(archive), 'status', '--json']) == 0
    info = json.loads(capsys.readouterr().out)
    assert info['files'] == 3 and info['bytes'] > 0
    assert info['volume_bytes'] > info['volume_free'] > 0 and 0 <= info['percent_full'] <= 100
    assert info['largest_live']['name'] == 'web/web-new__app.log'
    assert info['oldest'] == time.strftime('%Y-%m-%d', time.gmtime())
    assert main(['--root', str(archive), 'status']) == 0
    assert 'largest live: web/web-new__app.log' in capsys.readouterr().out


def test_run_refuses_an_empty_app_list(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('LOGARCHIVE_APPS', raising=False)
    assert main(['--root', str(tmp_path), 'run']) == 2
    assert 'LOGARCHIVE_APPS' in capsys.readouterr().err
