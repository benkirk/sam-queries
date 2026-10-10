"""The tomcat watch: scripts/lib/tomcat_log_summary.sh against a fixture Tomcat log tree, then
scripts/lib/tomcat_report.py and scripts/tomcat_watch.sh over its records."""

import gzip
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
READER = REPO / 'scripts' / 'lib' / 'tomcat_log_summary.sh'
REPORT = REPO / 'scripts' / 'lib' / 'tomcat_report.py'
TICK = REPO / 'scripts' / 'tomcat_watch.sh'

# 2026-10-10 12:00:00 MDT; the tick window is the 35 minutes before it.
NOW = int(datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc).timestamp())
JVM = NOW - 864000


def line(local, path, status=200, client='54.85.201.121', ua='Ruby', ms=120, size='512', off='-0600'):
    """One access-log line as sam-tomcat's syslog writes it; `local` is 'DD/Mon/YYYY:HH:MM:SS'."""
    return (f'Oct 10 00:00:00 sam-tomcat {client} 128.117.224.190 - - [{local} {off}] '
            f'"GET {path} HTTP/1.1" {status} {size} {ms} "{ua}"\n')


@pytest.fixture
def logs(tmp_path):
    today = [
        line('10/Oct/2026:11:30:00', '/api/protected/heuv/v1/report?username=x', ms=100),
        line('10/Oct/2026:11:31:00', '/api/protected/heuv/v1/report', ms=300, size='-'),
        line('10/Oct/2026:10:40:00', '/api/protected/heuv/v1/user', off='-0700'),  # 17:40Z, inside
        line('10/Oct/2026:10:00:00', '/api/protected/heuv/v1/user'),  # 16:00Z, before the window
        line('10/Oct/2026:11:32:00', '/api/protected/amie/v1/tasks/AMIE', client='128.117.177.140',
             ua='python-requests/2.32.5'),
        line('10/Oct/2026:11:33:00', '/api/.env', status=404, client='95.181.232.39', ua='Mozilla/5.0'),
        line('10/Oct/2026:11:34:00', '/api/protected/admin/ssg/queue/Derecho', status=401,
             client='44.204.179.135', ua='Slackbot'),
        line('10/Oct/2026:11:35:00', '/api/xras/v1/people', client='18.223.62.77'),
        line('10/Oct/2026:11:36:00', '/api/protected/admin/ssg/fairShareTree/v3/Casper', ms=6100,
             client='128.117.211.150', ua='python-requests/2.31.0'),
        line('10/Oct/2026:11:37:00', '//', client='128.117.224.126', ua='libwww-perl/6.13', ms=0),
        line('10/Oct/2026:11:38:00', '/', client='20.70.184.248', ua='-', ms=0),
        line('10/Oct/2026:11:39:00', '/app/home', status=302, client='71.218.234.130', ua='Mozilla/5.0'),
    ]
    (tmp_path / 'access.log-20261010').write_text(''.join(today))
    with gzip.open(tmp_path / 'access.log-20261003.gz', 'wt') as fh:
        fh.write(line('03/Oct/2026:11:40:00', '/api/protected/heuv/v1/user'))
        fh.write(line('03/Oct/2026:11:41:00', '/api/protected/admin/ldapsync/user', client='128.117.177.140',
                      ua='libwww-perl/6.52'))
    with gzip.open(tmp_path / 'access.log-20261009.gz', 'wt') as fh:
        fh.write(line('09/Oct/2026:23:55:00', '/api/protected/heuv/v1/user'))
        fh.write(line('09/Oct/2026:12:00:00', '/api/protected/amie/v1/tasks/AMIE', client='128.117.177.140'))
        fh.write(line('09/Oct/2026:12:00:01', '/wp-login.php', status=404, client='1.2.3.4', ua='x'))
    (tmp_path / 'sam.log').write_text(
        '2026-10-10 11:40:01,133 ERROR [e.u.c.s.p.r.ApiController:handleUnexpectedException] - Unexpected 123\n'
        '2026-10-10 11:41:01,133 ERROR [e.u.c.s.p.r.ApiController:handleUnexpectedException] - Unexpected 456\n'
        '\tat some.Stack.trace(Line.java:12)\n'
        '2026-10-10 09:00:00,000 ERROR [x:y] - outside the window\n')
    (tmp_path / 'catalina.out').write_text(
        "Oct 10 11:42:00 sam-tomcat SEVERE: Servlet.service() for servlet [samApp] threw 'x'\n")
    return tmp_path


def read(logs, *args):
    env = {**os.environ, 'TOMCAT_LOGS': str(logs), 'TOMCAT_NOW': str(NOW), 'TOMCAT_JVM_START': str(JVM)}
    out = subprocess.run(['bash', str(READER), *map(str, args)], env=env,
                         capture_output=True, text=True, check=True).stdout
    return [ln.split('\t') for ln in out.splitlines()]


def window(logs, frm=NOW - 2100, to=NOW, base=True):
    return read(logs, 'window', frm, to, frm - 604800 if base else 0, to - 604800 if base else 0)


def rec(records, kind):
    return [r[1:] for r in records if r[0] == kind]


class TestReader:
    def test_api_records_aggregate_by_family_client_status(self, logs):
        api = {tuple(r[:4]): r[4:] for r in rec(window(logs), 'api')}
        assert api[('heuv/report', '54.85.201.121', 'Ruby', '200')] == ['2', '400', '300']
        assert api[('heuv/user', '54.85.201.121', 'Ruby', '200')][0] == '1'  # the -0700 line only

    def test_amie_is_dropped_everywhere(self, logs):
        records = window(logs) + read(logs, 'days', '20261009')
        assert not any('amie' in field for r in records for field in r)

    def test_scanners_count_once_and_a_refused_caller_keeps_its_status(self, logs):
        records = window(logs)
        assert rec(records, 'scan') == [['1']]
        assert ['ssg/queue', '44.204.179.135', 'Slackbot', '401', '1', '120', '120'] in rec(records, 'api')

    def test_xras_path_is_its_own_family(self, logs):
        assert any(r[0] == 'xras/v1' for r in rec(window(logs), 'api'))

    def test_probe_is_the_libwww_root_hit_and_other_root_hits_are_web(self, logs):
        records = window(logs)
        probe = rec(records, 'probe')[0]
        assert probe[1] == '1' and int(probe[0]) == NOW - 23 * 60
        assert ['other', '200', '1'] in rec(records, 'web') and ['app', '302', '1'] in rec(records, 'web')

    def test_slow_calls_carry_their_utc_time(self, logs):
        slow = rec(window(logs), 'slow')
        assert slow == [[str(NOW - 24 * 60), 'ssg/fairShareTree', '128.117.211.150', 'python-requests/2.31.0',
                         '6100']]

    def test_week_ago_window_reads_the_gz(self, logs):
        base = rec(window(logs), 'base')
        assert ['heuv/user', '54.85.201.121', 'Ruby', '200', '1'] in base
        assert ['ldapsync/user', '128.117.177.140', 'libwww-perl/6.52', '200', '1'] in base

    def test_window_across_mountain_midnight_reads_both_files(self, logs):
        midnight = int(datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc).timestamp())
        (logs / 'access.log-20261010').write_text(
            (logs / 'access.log-20261010').read_text()
            + line('10/Oct/2026:00:03:00', '/api/protected/heuv/v1/user'))
        api = rec(window(logs, midnight - 600, midnight + 600, base=False), 'api')
        assert api == [['heuv/user', '54.85.201.121', 'Ruby', '200', '2', '240', '120']]

    def test_errors_are_windowed_masked_and_counted(self, logs):
        records = window(logs)
        assert rec(records, 'err') == [['sam.log', '2'], ['catalina', '1']]
        msgs = {r[0]: r for r in rec(records, 'errmsg')}
        assert msgs['sam.log'][1] == '2' and msgs['sam.log'][4].endswith('Unexpected N')
        assert "'" not in msgs['catalina'][4] and 'Q' in msgs['catalina'][4]

    def test_host_record(self, logs):
        host = dict(kv.split('=', 1) for kv in rec(window(logs), 'host')[0])
        assert host['now'] == str(NOW) and host['jvm_start'] == str(JVM)

    def test_api_records_are_capped(self, logs):
        many = ''.join(line('10/Oct/2026:11:50:00', '/api/protected/heuv/v1/user', client=f'10.0.{i // 250}.{i % 250}')
                       for i in range(300))
        (logs / 'access.log-20261010').write_text(many)
        assert len(rec(window(logs), 'api')) == 200

    def test_days_mode(self, logs):
        records = read(logs, 'days', '20261009', '20261001')
        assert rec(records, 'day') == [['20261009', 'heuv/user', '54.85.201.121', 'Ruby', '200', '1']]
        assert rec(records, 'dayweb') == [['20261009', 'other', '1']]  # a scanner outside /api is web

    @pytest.mark.parametrize('script', [READER, TICK, REPORT])
    def test_never_names_the_syslog_pipes(self, script):
        text = script.read_text()
        assert '/var/log/tomcat' not in text and 'tomcat-sam_access' not in text

    def test_reader_has_no_write_commands(self):
        words = READER.read_text().split()
        assert not {'tee', 'rm', 'mv', 'cp', 'touch', 'mkdir', 'truncate'} & set(words)


def report(records, tmp_path, cmap='', state=None, mode='tick', to=NOW):
    (tmp_path / 'map').write_text(cmap)
    st = tmp_path / 'state'
    if state is not None:
        st.write_text(state)
    out = subprocess.run(['python3', str(REPORT), 'report', '--map', str(tmp_path / 'map'), '--state', str(st),
                          '--tsv', str(tmp_path / 'daily.tsv'), '--mode', mode, '--to', str(to)],
                         input=''.join('\t'.join(r) + '\n' for r in records),
                         capture_output=True, text=True, check=True).stdout
    *lines, verdict = out.splitlines()
    _, warn, fail = verdict.split()
    return '\n'.join(lines), int(warn), int(fail)


CMAP = ('watch * * sam-tomcat-watch\nself * 128.117.65.3 *\n'
        'heuv/portal heuv/ 54.85.201.121 Ruby\nssg/150 * 128.117.211.150 *\nxras/broker * 18.223.62.77 *\n')


class TestReport:
    def test_unmapped_authenticated_callers_are_new_and_refused_scanners_are_not(self, logs, tmp_path):
        text, warn, fail = report(window(logs), tmp_path, state='LASTJVM=%d\n' % JVM)
        assert 'NEW caller (unmapped): client=54.85.201.121 ua=Ruby' in text
        assert '44.204.179.135' not in text
        assert 'legacy XRAS hit: xras/v1' in text and fail == 1

    def test_mapped_callers_line_and_self_excluded(self, logs, tmp_path):
        text, warn, fail = report(window(logs), tmp_path, CMAP, state='LASTJVM=%d\n' % JVM)
        assert 'callers: heuv/portal 3 (1)' in text and 'NEW' not in text
        assert 'slow: 17:36:00Z ssg/fairShareTree ssg/150 6.1s' in text

    def test_missing_caller_in_business_hours(self, tmp_path):
        host = ['host', f'now={NOW}', 'up_s=1', f'jvm_start={JVM}', 'jvm_pid=1', 'tomcat_pct=5', 'rotated=-']
        base = [['base', 'heuv/user', '54.85.201.121', 'Ruby', '200', '6'],
                ['base', 'ssg/queue', '128.117.65.3', 'curl', '200', '9']]
        text, warn, _ = report([host, *base, ['probe', str(NOW - 60), '30']], tmp_path, CMAP, state=f'LASTJVM={JVM}\n')
        assert 'MISSING caller heuv/portal' in text and 'MISSING caller self' not in text and warn == 1

    def test_restart_and_baseline(self, logs, tmp_path):
        text, _, _ = report(window(logs), tmp_path, CMAP)
        assert 'baseline seeded' in text and 'restarted' not in text
        text, _, _ = report(window(logs), tmp_path, CMAP, state=f'LASTJVM={JVM - 86400}\n')
        assert 'tomcat restarted' in text

    def test_daily_history_and_cutover_line(self, logs, tmp_path):
        records = read(logs, 'days', '20261009', '20261002')
        text, _, _ = report(records, tmp_path, CMAP, mode='daily')
        assert 'cutover 20261009' in text and 'porting 1 (0)' in text
        rows = (tmp_path / 'daily.tsv').read_text().splitlines()
        assert '20261009\theuv/user\theuv/portal\t1\t0\t0' in rows
        assert not any('amie' in r for r in rows)


def test_tick_runs_locally_against_the_fixture(logs, tmp_path):
    (tmp_path / 'xdg' / 'sam-watch').mkdir(parents=True)
    (tmp_path / 'xdg' / 'sam-watch' / 'tomcat-callers').write_text(CMAP)
    env = {**os.environ, 'TOMCAT_HOST': 'local', 'TOMCAT_LOGS': str(logs), 'TOMCAT_NOW': str(NOW),
           'TOMCAT_JVM_START': str(JVM), 'XDG_STATE_HOME': str(tmp_path / 'xdg')}
    out = subprocess.run(['bash', str(TICK), '--no-color'], env=env, capture_output=True, text=True)
    assert out.returncode == 2, out.stdout + out.stderr  # the fixture's XRAS hit
    assert '[tomcat: local]' in out.stdout and 'callers: heuv/portal 3' in out.stdout
    assert 'cutover 20261009' in out.stdout
