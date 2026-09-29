"""scripts/lib/nhd_lane_summary.sh reads an ncar-hpc-deploy lane tree into one record per
stamp, with ages computed where the files are. Exercised against a fixture tree."""

import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
READER = REPO / 'scripts' / 'lib' / 'nhd_lane_summary.sh'


def _touch(path: Path, text: str, age_s: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    then = time.time() - age_s
    os.utime(path, (then, then))


@pytest.fixture
def lanes(tmp_path):
    """A dev lane as ncar-hpc-deploy leaves it: one healthy rapid tick per host, a failed
    hourly step with its log block, a stale lock, a fresh spool, and no prod lane."""
    r = tmp_path / 'dev'
    (r / 'images').mkdir(parents=True)
    (r / 'images' / 'samuel-staging-2ea87f8dcdbe.sif').write_text('x')
    (r / 'images' / 'samuel-staging-976d6764f6e5.sif').write_text('x')
    os.symlink('images/samuel-staging-2ea87f8dcdbe.sif', r / 'current')
    os.symlink('images/samuel-staging-976d6764f6e5.sif', r / 'previous')
    s = r / 'state'
    _touch(s / 'last-digest', 'sha256:2ea87f8dcdbe34fa\n', 3600)
    _touch(s / 'update-history',
           '2026-09-28T16:23:00-0600 sha256:976d sif samuel-staging-976d6764f6e5.sif git_sha=aaaaaaa\n'
           '2026-09-28T17:00:00-0600 sha256:2ea87f8dcdbe34fa samuel-staging-2ea87f8dcdbe.sif git_sha=d809bdc\n', 3600)
    _touch(s / 'last-update', '2026-09-28T17:47:00-0600 unchanged sha256:2ea87f8dcdbe34fa\n', 900)
    _touch(s / 'last-tick.rapid.casper', '2026-09-28T17:40:08-0600 exit=0 6s collectors=0\n', 130)
    _touch(s / 'last-tick.rapid.derecho', '2026-09-28T17:40:09-0600 exit=0 6s collectors=0\n', 131)
    _touch(s / 'last-tick.hourly.casper', '2026-09-28T17:19:24-0600 exit=2 20s accounting-comp=2\n', 1500)
    _touch(s / 'last-run.collectors.casper', '2026-09-28T17:40:08-0600 exit=0 6s\n', 130)
    _touch(s / 'last-run.accounting-comp.casper', '2026-09-28T17:19:24-0600 exit=2 20s\n', 1500)
    _touch(r / 'logs' / 'accounting-comp' / 'casper-2026-09-28.log',
           '#----------------------------------------------------------------------------\n'
           '[2026-09-28T16:19:00-0600] [dev] run accounting-comp --last 2d on casper image=images/x.sif\n'
           'earlier block, must not leak\n'
           '[2026-09-28T16:19:20-0600] [dev] run accounting-comp exit=0 (20s)\n'
           '#----------------------------------------------------------------------------\n'
           '[2026-09-28T17:19:04-0600] [dev] run accounting-comp --last 2d on casper image=images/x.sif\n'
           "Skip: User 'wenpuho' (no uid) not found in SAM\n"
           ' Errors                  12 \n'
           '\n'
           'All fallbacks failed\n'
           '[2026-09-28T17:19:24-0600] [dev] run accounting-comp exit=2 (20s)\n', 1500)
    _touch(s / 'collectors-casper.lock', '', 130)
    _touch(s / 'jobhist-sync-derecho.lock', '', 7200)
    _touch(s / 'tick-daily-casper.lock', '', 7200)
    _touch(s / 'last-tick.daily.casper', '2026-09-28T16:09:56-0600 exit=0 53s accounting-comp=0\n', 7100)
    _touch(s / 'accounting-disk-casper.lock', '', 7200)
    _touch(s / 'last-run.accounting-disk.casper', '2026-09-27T01:09:56-0600 exit=0 34s\n', 90000)
    cap = s / 'spool' / 'casper' / 'casper.1790638803.121786'
    _touch(cap / 'scrape.meta', 'host=crlogin3\nfinished=x\n', 125)
    os.symlink(cap.name, s / 'spool' / 'casper' / 'casper')
    (tmp_path / 'prod').mkdir()
    return tmp_path


def read(lanes, lane):
    out = subprocess.run(['bash', str(READER), lane], env={**os.environ, 'NHD_LANES': str(lanes)},
                         capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def _age(record):
    return int(next(t for t in record.split() if t.startswith('age=')).split('=')[1])


class TestRecords:
    def test_uninstalled_lane_is_one_line(self, lanes):
        lines = read(lanes, 'prod')
        assert len(lines) == 1 and lines[0].startswith('lane=prod installed=0 now=')

    def test_image_row_maps_the_current_image_to_its_git_sha(self, lanes):
        image = next(l for l in read(lanes, 'dev') if l.startswith('image '))
        assert 'current=samuel-staging-2ea87f8dcdbe' in image
        assert 'git_sha=d809bdc' in image and 'previous=samuel-staging-976d6764f6e5' in image
        assert 'candidate=0' in image and 'digest=sha256:2ea87f8dcdbe34fa' in image

    def test_update_prefers_the_stamp_and_reports_its_outcome(self, lanes):
        upd = [l for l in read(lanes, 'dev') if l.startswith('update ')]
        assert len(upd) == 1 and 'outcome=unchanged' in upd[0]
        assert abs(_age(upd[0]) - 900) <= 2

    def test_update_falls_back_to_the_lock_mtime(self, lanes):
        (lanes / 'dev' / 'state' / 'last-update').unlink()
        _touch(lanes / 'dev' / 'state' / 'update.lock', '', 3000)
        upd = [l for l in read(lanes, 'dev') if l.startswith('update ')]
        assert len(upd) == 1 and 'outcome=unknown' in upd[0] and abs(_age(upd[0]) - 3000) <= 2

    def test_a_failed_update_is_reported_with_its_stamp(self, lanes):
        _touch(lanes / 'dev' / 'state' / 'last-update.FAILED',
               '2026-09-28T18:47:00-0600 sha256:abc smoke exit=2\n', 60)
        upd = [l for l in read(lanes, 'dev') if l.startswith('update ') and 'failed' in l]
        assert upd and 'detail=2026-09-28T18:47:00-0600 sha256:abc smoke exit=2' in upd[0]

    def test_ticks_carry_age_exit_and_steps(self, lanes):
        ticks = {tuple(l.split()[1:3]): l for l in read(lanes, 'dev') if l.startswith('tick ')}
        assert set(ticks) == {('rapid', 'casper'), ('rapid', 'derecho'), ('hourly', 'casper'), ('daily', 'casper')}
        assert 'exit=0 dur=6 steps=collectors=0' in ticks[('rapid', 'casper')]
        assert abs(_age(ticks[('rapid', 'casper')]) - 130) <= 2
        assert 'exit=2 dur=20 steps=accounting-comp=2' in ticks[('hourly', 'casper')]

    def test_a_failed_run_carries_the_jobs_last_word(self, lanes):
        runs = {l.split()[1]: l for l in read(lanes, 'dev') if l.startswith('run ')}
        assert runs['collectors'].endswith('exit=0 dur=6')
        assert runs['accounting-comp'].endswith('exit=2 dur=20 reason=All fallbacks failed')

    def test_a_lock_is_stale_only_when_nothing_completed_since_it(self, lanes):
        """Lock files persist after release: tick-daily-casper is old but its tick stamp is
        newer, so it is free; accounting-disk-casper's last stamp predates it, so it is hung."""
        locks = {l.split()[1]: l for l in read(lanes, 'dev') if l.startswith('lock ')}
        assert set(locks) == {'jobhist-sync-derecho', 'accounting-disk-casper'}
        assert abs(_age(locks['jobhist-sync-derecho']) - 7200) <= 2

    def test_a_finished_update_lock_is_not_stale(self, lanes):
        _touch(lanes / 'dev' / 'state' / 'update.lock', '', 7200)
        assert not [l for l in read(lanes, 'dev') if l.startswith('lock update')]
        (lanes / 'dev' / 'state' / 'last-update').unlink()
        assert [l for l in read(lanes, 'dev') if l.startswith('lock update age=')]

    def test_spool_age_is_the_meta_files(self, lanes):
        spool = [l for l in read(lanes, 'dev') if l.startswith('spool ')]
        assert len(spool) == 1 and spool[0].startswith('spool casper casper age=')
        assert abs(_age(spool[0]) - 125) <= 2
