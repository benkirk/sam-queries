"""`logarchive.follow`: resume without loss or duplicate, finalize, and the poll loop."""

import errno
import gzip

import pytest

from logarchive import follow, store
from logarchive.follow import Archiver, Follower
from logarchive.kube import KubeError, PodGone, StreamBroken

from logarchive_fakes import FakeClient, pod, stamp

LIVE, DONE = [pod('web-1')], [pod('web-1', finished=True)]


def follower(client, root, stop, name='web-1'):
    return Follower(client, root, 'web', name, 'app', stop, clock=lambda: 0)


def lines(root, name='web-1', app='web'):
    path = store.log_path(root, app, name, 'app')
    data = gzip.decompress(store.finished_path(path).read_bytes()) if store.is_finished(path) else path.read_bytes()
    return [raw.split(' ', 1)[1] for raw in data.decode().splitlines()]


def test_a_finished_container_is_copied_and_finalized(tmp_path, stop):
    client = FakeClient(streams=[[(stamp(1), 'a'), (stamp(2), 'b')]], listings=[DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b']
    assert store.is_finished(store.log_path(tmp_path, 'web', 'web-1', 'app'))
    assert client.stream_calls == [('web-1', 'app', None)]


def test_resume_after_a_restart_writes_no_duplicate_and_loses_no_line(tmp_path, stop):
    """Three lines share the cursor's timestamp; the archiver died after writing two of them."""
    same = stamp(2, '5')
    path = store.log_path(tmp_path, 'web', 'web-1', 'app')
    for ts, line in [(stamp(1), 'a'), (same, 'b1'), (same, 'b2')]:
        store.append(path, ts, line)
    redelivered = [(stamp(2), 'early-in-the-second'), (same, 'b1'), (same, 'b2'), (same, 'b3'),
                   (stamp(2, '75'), 'c'), (stamp(3), 'd')]
    client = FakeClient(streams=[redelivered], listings=[DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b1', 'b2', 'b3', 'c', 'd']
    assert client.stream_calls == [('web-1', 'app', '2026-11-26T15:00:02Z')]   # floored to the second


def test_end_of_stream_on_a_live_container_reconnects_and_resumes(tmp_path, stop):
    client = FakeClient(
        streams=[[(stamp(1), 'a'), (stamp(2), 'b')],
                 [(stamp(2), 'b'), (stamp(3), 'c')],
                 [(stamp(3), 'c')]],
        listings=[LIVE, LIVE, DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b', 'c']
    assert [since for _, _, since in client.stream_calls] == [None, stamp(2), stamp(3)]
    assert store.is_finished(store.log_path(tmp_path, 'web', 'web-1', 'app'))


def test_a_dropped_stream_never_finalizes_even_on_a_finished_container(tmp_path, stop):
    """A drop may have cut the transfer short; only a clean end proves the last line arrived."""
    client = FakeClient(
        streams=[[(stamp(1), 'a'), StreamBroken('reset')],
                 [(stamp(1), 'a'), (stamp(2), 'b')]],
        listings=[DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b'] and len(client.stream_calls) == 2


def test_a_404_the_listing_contradicts_does_not_finalize(tmp_path, stop):
    client = FakeClient(streams=[[PodGone('404')], [(stamp(1), 'a')]], listings=[LIVE, LIVE, DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a'] and len(client.stream_calls) == 2


def test_a_finalize_that_fails_is_logged_and_left_for_the_next_poll(tmp_path, stop, monkeypatch, caplog):
    def full(path):
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(store, 'finalize', full)
    client = FakeClient(streams=[[(stamp(1), 'a')]], listings=[DONE])
    with caplog.at_level('WARNING', logger='logarchive'):
        follower(client, tmp_path, stop).run()
    path = store.log_path(tmp_path, 'web', 'web-1', 'app')
    assert path.exists() and not store.is_finished(path) and 'finalize' in caplog.text


def test_a_pod_that_disappears_mid_stream_is_finalized_with_what_arrived(tmp_path, stop):
    client = FakeClient(streams=[[(stamp(1), 'a'), (stamp(2), 'b'), StreamBroken('eof')], [PodGone('404')]],
                        listings=[LIVE, []])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b']
    assert store.is_finished(store.log_path(tmp_path, 'web', 'web-1', 'app'))


def test_an_api_refusal_backs_off_and_retries_without_finalizing(tmp_path, stop):
    client = FakeClient(streams=[[KubeError('400 ContainerCreating')], [KubeError('503')], [(stamp(1), 'a')]],
                        listings=[LIVE, LIVE, DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a'] and stop.waits == [1, 2]


def test_a_failed_listing_is_not_read_as_finished(tmp_path, stop):
    client = FakeClient(streams=[[(stamp(1), 'a')], [(stamp(1), 'a'), (stamp(2), 'b')]],
                        listings=[KubeError('timeout'), DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b'] and len(client.stream_calls) == 2


def test_a_line_without_a_timestamp_is_the_apis_notice_and_is_skipped(tmp_path, stop):
    client = FakeClient(streams=[[(stamp(1), 'a'), ('unable', 'to retrieve container logs'), (stamp(2), 'b')]],
                        listings=[DONE])
    follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b']


def test_enospc_on_append_is_logged_and_retried_not_raised(tmp_path, stop, monkeypatch, caplog):
    real, failures = store.append, [OSError(errno.ENOSPC, 'No space left on device')] * 2

    def flaky(path, ts, line):
        if line == 'b' and failures:
            with open(path, 'ab') as fh:     # the failed write left half a line behind
                fh.write(b'2026-11-26T15:00:02Z ha')
            raise failures.pop()
        real(path, ts, line)

    monkeypatch.setattr(store, 'append', flaky)
    client = FakeClient(streams=[[(stamp(1), 'a'), (stamp(2), 'b'), (stamp(3), 'c')]], listings=[DONE])
    with caplog.at_level('WARNING', logger='logarchive'):
        follower(client, tmp_path, stop).run()
    assert lines(tmp_path) == ['a', 'b', 'c']
    assert stop.waits == [follow.WRITE_RETRY_SECONDS] * 2
    assert caplog.text.count('retrying every') == 1


def test_a_crashed_follower_logs_and_returns(tmp_path, stop, caplog):
    client = FakeClient(streams=[[RuntimeError('boom')]], listings=[LIVE])
    with caplog.at_level('ERROR', logger='logarchive'):
        follower(client, tmp_path, stop).run()
    assert 'the next poll restarts it' in caplog.text


# --- the poll loop ------------------------------------------------------------

class Handle:
    def __init__(self, alive):
        self.alive = alive

    def is_alive(self):
        return self.alive


def archiver(client, root, stop, *, alive=False, self_pod=None):
    """An Archiver whose followers run inline; `started` records which were spawned."""
    started = []

    def spawn(f):
        started.append((f.app, f.pod, f.container))
        if not alive:
            f.run()
        return Handle(alive)

    arch = Archiver(client, root, ['web'], 90, self_pod=self_pod, clock=lambda: 0, spawn=spawn)
    arch.stop = stop
    return arch, started


def test_poll_follows_each_started_container_once(tmp_path, stop):
    pods = [pod('web-1', finished=True), pod('web-2', started=False), pod('me')]
    client = FakeClient(streams=[[(stamp(1), 'a')]], listings=[pods])
    arch, started = archiver(client, tmp_path, stop, self_pod='me')   # never its own pod
    arch.poll()
    assert started == [('web', 'web-1', 'app')] and lines(tmp_path) == ['a']
    assert client.list_calls[0] == 'app=web'


def test_a_finished_file_is_never_refetched(tmp_path, stop):
    client = FakeClient(streams=[[(stamp(1), 'a')]], listings=[DONE])
    arch, started = archiver(client, tmp_path, stop)
    arch.poll()
    arch.poll()
    assert len(started) == 1 and len(client.stream_calls) == 1


def test_a_live_follower_is_not_doubled_and_a_dead_one_is_forgotten(tmp_path, stop):
    arch, started = archiver(FakeClient(listings=[LIVE]), tmp_path, stop, alive=True)
    arch.poll()
    arch.poll()
    assert len(started) == 1 and len(arch.followers) == 1
    next(iter(arch.followers.values())).alive = False
    arch.poll()
    assert len(started) == 2


def test_a_file_whose_pod_is_gone_is_finalized_but_not_on_a_failed_listing(tmp_path, stop):
    orphan = store.log_path(tmp_path, 'web', 'web-0', 'app')
    store.append(orphan, stamp(1), 'left behind')
    arch, _ = archiver(FakeClient(listings=[KubeError('timeout'), []]), tmp_path, stop)
    arch.poll()
    assert orphan.exists() and not store.is_finished(orphan)
    arch.poll()
    assert store.is_finished(orphan) and lines(tmp_path, 'web-0') == ['left behind']


def test_housekeeping_prunes_and_reclaims_on_their_own_clocks(tmp_path, stop, monkeypatch):
    calls, now = [], [0]
    monkeypatch.setattr(store, 'prune', lambda root, days: calls.append(('prune', days)) or [])
    monkeypatch.setattr(store, 'reclaim', lambda root: calls.append(('reclaim',)) or [])
    arch = Archiver(FakeClient(), tmp_path, ['web'], 90, clock=lambda: now[0], spawn=lambda f: Handle(False))
    for t in (0, 30, follow.RECLAIM_SECONDS, follow.PRUNE_SECONDS):
        now[0] = t
        arch.poll()
    assert calls == [('reclaim',), ('prune', 90), ('reclaim',), ('reclaim',), ('prune', 90)]


def test_run_beats_every_pass_and_survives_a_failing_poll(tmp_path, stop, caplog):
    class Stop3:
        waits = 0

        def is_set(self):
            return self.waits >= 3

        def wait(self, seconds=None):
            self.waits += 1

    arch = Archiver(FakeClient(), tmp_path, ['web'], 90, spawn=lambda f: Handle(False))
    arch.poll = lambda: (_ for _ in ()).throw(RuntimeError('boom'))
    with caplog.at_level('ERROR', logger='logarchive'):
        arch.run(Stop3())
    assert caplog.text.count('poll failed') == 3
    assert store.heartbeat_age(tmp_path) is not None
