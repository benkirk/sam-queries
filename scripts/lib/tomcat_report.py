#!/usr/bin/env python3
"""Reads scripts/lib/tomcat_log_summary.sh records and prints the tomcat watch's lines.

Stdlib only; scripts/tomcat_watch.sh drives it. Subcommands: `times` (window epochs),
`days` (log dates the daily history still lacks), `report` (the tick), `trend` (the
per-family history table). Thresholds and the caller-map format: the watch-tomcat skill.
"""
import argparse
import fnmatch
import hashlib
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MT = ZoneInfo('America/Denver')

# Family prefix -> cutover status; first match wins. A port landing is a one-line edit here.
FAMILIES = [
    ('xras/', 'cutover'),
    ('heuv/', 'cutover'),
    ('ssg/', 'ported'),
    ('sysacct/directoryaccess', 'ported'),
    ('sysacct/groupstatus', 'ported'),
    ('dasg/diskquota', 'ported'),
    ('ldapsync/', 'ported'),
    ('admin/userlifecycle', 'ported'),
    ('admin/userPurge', 'ported'),
    ('admin/groupPurge', 'ported'),
    ('admin/institutionPurge', 'ported'),
    ('admin/organizationPurge', 'ported'),
    ('peoplesearch/', 'retired'),
]
EXCLUDED = ('self', 'watch')
SLOW_MS = 4000
PROBE_STALE_S = 300
ERR_WARN = 5
DISK_WARN = 85


def status_of(family):
    return next((s for p, s in FAMILIES if family.startswith(p)), 'other')


def parse_span(text):
    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    return int(text[:-1]) * units[text[-1]] if text[-1] in units else int(text)


def utc(epoch):
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime('%H:%M:%SZ')


def week_ago(epoch):
    """The same Mountain wall-clock time seven days earlier (DST-safe)."""
    local = datetime.fromtimestamp(epoch, MT) - timedelta(days=7)
    return int(local.replace(tzinfo=None).replace(tzinfo=MT).timestamp())


def load_map(path):
    rules = []
    if path and os.path.exists(path):
        for line in open(path):
            parts = line.split('#', 1)[0].split()
            if len(parts) == 4:
                rules.append(parts)
    return rules


def label_for(rules, family, client, ua):
    """Family and UA match as prefixes, the client exactly (a glob when it carries one)."""
    for label, fam, cl, agent in rules:
        if (fnmatch.fnmatchcase(family, fam + '*') and fnmatch.fnmatchcase(ua, agent + '*')
                and fnmatch.fnmatchcase(client, cl)):
            return label
    return None


def load_state(path):
    state = {}
    if path and os.path.exists(path):
        for line in open(path):
            if '=' in line:
                k, v = line.rstrip('\n').split('=', 1)
                state[k] = v
    return state


def authed(status):
    return status not in ('401', '403')


def fmt(n):
    return f'{n:,}'


class Report:
    def __init__(self, rules):
        self.rules = rules
        self.lines = []
        self.warn = 0
        self.fail = 0

    def say(self, text):
        self.lines.append(text)

    def flag(self, level, text):
        self.lines.append(f"  {'✘' if level == 'FAIL' else '⚠'} {level} — {text}")
        if level == 'FAIL':
            self.fail += 1
        else:
            self.warn += 1


def cmd_times(a):
    now = int(a.now or datetime.now(timezone.utc).timestamp())
    span = parse_span(a.span)
    if a.at:
        at = datetime.fromisoformat(a.at.rstrip('Z')).replace(tzinfo=timezone.utc).timestamp()
        frm, to = int(at - span / 2), int(at + span / 2)
    else:
        frm, to = now - span, now
    print(f'FROM={frm} TO={to} BFROM={week_ago(frm)} BTO={week_ago(to)}')


def tsv_dates(path):
    if not path or not os.path.exists(path):
        return set()
    return {line.split('\t', 1)[0] for line in open(path) if line.strip()}


def cmd_days(a):
    have = tsv_dates(a.tsv)
    today = datetime.fromtimestamp(int(a.now or datetime.now(timezone.utc).timestamp()), MT).date()
    if a.backfill:
        want = [today - timedelta(days=i) for i in range(a.backfill, 0, -1)]
    elif a.date:
        d = datetime.strptime(a.date, '%Y%m%d').date()
        want = [d - timedelta(days=7), d]
    else:
        y = today - timedelta(days=1)
        want = [] if y.strftime('%Y%m%d') in have else [y - timedelta(days=7), y]
    print(' '.join(d.strftime('%Y%m%d') for d in want if d.strftime('%Y%m%d') not in have or a.date))


def read_records(stream):
    recs = defaultdict(list)
    for line in stream:
        parts = line.rstrip('\n').split('\t')
        if parts and parts[0]:
            recs[parts[0]].append(parts[1:])
    return recs


def report_host(r, recs, state, baseline):
    if not recs['host']:
        return
    h = dict(kv.split('=', 1) for kv in recs['host'][0])
    jvm, now = int(h['jvm_start']), int(h['now'])
    since = datetime.fromtimestamp(jvm, timezone.utc).strftime('%Y-%m-%d %H:%MZ') if jvm else 'none'
    r.say(f"host: up {int(h['up_s']) / 86400:.1f}d · jvm pid {h['jvm_pid']} since {since}"
          f" · /tomcat {h['tomcat_pct']}% · newest catalina rotation {h['rotated']}")
    if not jvm:
        r.flag('FAIL', 'no java process on the host')
    elif not baseline and state.get('LASTJVM') and abs(jvm - int(state['LASTJVM'])) > 300:
        r.flag('WARN', f"tomcat restarted (jvm start {utc(state['LASTJVM'])} -> {utc(jvm)}, {since})")
    if int(h['tomcat_pct'] or 0) >= DISK_WARN:
        r.flag('WARN', f"/tomcat at {h['tomcat_pct']}%")
    state['LASTJVM'] = str(jvm or state.get('LASTJVM', 0))
    return now


def percentile(buckets, q):
    total = sum(buckets.values())
    seen = 0
    order = sorted(buckets, key=lambda e: float('inf') if e == 'inf' else int(e))
    for edge in order:
        seen += buckets[edge]
        if seen >= q * total:
            return '>8000' if edge == 'inf' else f'≤{edge}'
    return '-'


def report_traffic(r, recs, tick, to):
    rules = r.rules
    by_label, base_label = defaultdict(int), defaultdict(int)
    new, refused, total, status_mix = defaultdict(int), defaultdict(int), 0, defaultdict(int)
    newfam = defaultdict(set)
    for fam, cl, ua, st, n, ms_sum, ms_max in recs['api']:
        n = int(n)
        label = label_for(rules, fam, cl, ua)
        kind = status_of(fam)
        total += n
        status_mix[st[0] + 'xx'] += n
        if kind == 'cutover' and label not in EXCLUDED:
            r.flag('FAIL' if authed(st) else 'WARN', f'legacy cutover hit: {fam} {cl} {ua} {st} x{n}')
        if not authed(st):
            if label and label not in EXCLUDED:
                refused[(label, fam, st)] += n
            continue
        if st.startswith('5') and label not in EXCLUDED:
            r.flag('FAIL', f'{st} x{n} to {label or cl} on {fam}')
        if label is None:
            new[(cl, ua)] += n
            newfam[(cl, ua)].add(fam)
            continue
        if kind == 'retired' and label not in EXCLUDED:
            r.flag('WARN', f'retired family answered: {fam} {label} x{n}')
        by_label[label] += n
    for fam, cl, ua, st, n in recs['base']:
        label = label_for(rules, fam, cl, ua)
        if label and authed(st):
            base_label[label] += int(n)
    lat = {e: int(n) for e, n in recs['lat']}
    web = defaultdict(int)
    for fam, st, n in recs['web']:
        web[fam] += int(n)
    scan = sum(int(n[0]) for n in recs['scan'])
    mix = ' '.join(f'{k} {fmt(v)}' for k, v in sorted(status_mix.items()))
    r.say(f"web: api {fmt(total)} ({mix or 'none'}) p50 {percentile(lat, .5) if lat else '-'}ms"
          f" p95 {percentile(lat, .95) if lat else '-'}ms · app {fmt(web['app'])} · saml {fmt(web['saml'])}"
          f" · other {fmt(web['other'])} · scan {fmt(scan)}")
    labels = sorted(set(by_label) | set(base_label), key=lambda k: (k in EXCLUDED, -by_label[k], k))
    r.say('callers: ' + (' · '.join(f'{k} {by_label[k]} ({base_label[k]})' for k in labels) or 'none')
          + '   [now (same window a week ago)]')
    hour = datetime.fromtimestamp(to, MT).hour
    for k in labels:
        if k not in EXCLUDED and base_label[k] >= 5 and by_label[k] == 0 and 7 <= hour < 19:
            r.flag('WARN', f'MISSING caller {k}: 0 now, {base_label[k]} a week ago')
    for (cl, ua), n in sorted(new.items(), key=lambda kv: -kv[1])[:5]:
        r.flag('WARN', f"NEW caller (unmapped): client={cl} ua={ua} x{n} on {','.join(sorted(newfam[(cl, ua)]))}")
    for (label, fam, st), n in refused.items():
        r.flag('WARN', f'{label} refused {st} x{n} on {fam} (key revoked or rotated?)')
    for ep, fam, cl, ua, ms in recs['slow'][-3:]:
        r.say(f"slow: {utc(ep)} {fam} {label_for(rules, fam, cl, ua) or cl} {int(ms) / 1000:.1f}s")


def report_errors(r, recs, state, baseline):
    counts = {src: int(n) for src, n in recs['err']}
    r.say('errors: ' + ' · '.join(f'{k} {v}' for k, v in counts.items()))
    seen = set(filter(None, state.get('ERRCLASSES', '').split(',')))
    for src, n, first, last, text in recs['errmsg']:
        key = hashlib.md5(f'{src}\t{text}'.encode()).hexdigest()[:8]
        r.say(f'  {src} x{n} {utc(first)}-{utc(last)}  {text}')
        if not baseline and key not in seen:
            r.flag('WARN', f'new error class on {src}: {text[:80]}')
        seen.add(key)
    if sum(counts.values()) > ERR_WARN:
        r.flag('WARN', f'{sum(counts.values())} errors in the window (> {ERR_WARN})')
    state['ERRCLASSES'] = ','.join(sorted(seen)[-300:])


def report_probe(r, recs, tick, to):
    for last, n in recs['probe']:
        last, n = int(last), int(n)
        when = f'last {utc(last)}' if last else 'not seen'
        r.say(f'probe: GET / health check x{n}, {when}')
        if tick and (not last or to - last > PROBE_STALE_S):
            r.flag('WARN', f'the GET / health check is silent ({when})')


ZOO = ('slapd', 'syncd', 'ldif')


def report_app(r, recs, state, tick, app_label):
    """sam-app: the zoo's processes and repo heads, and the syncd client's side of the PUTs."""
    a = dict(kv.split('=', 1) for kv in recs['app'][0])
    procs = {name: int(start) for name, _pid, start in recs['aproc']}
    starts = sorted({utc_day(s) for s in procs.values()})
    r.say(f"app: sam-app up {int(a['up_s']) / 86400:.1f}d · zoo {'/'.join(n for n in ZOO if n in procs) or 'none'}"
          f" since {', '.join(starts) or '-'} · /data {a['data_pct']}%")
    first = not any(k.startswith('APP_') for k in state)
    for name in ZOO:
        if name not in procs:
            r.flag('WARN', f'sam-app: {name} is not running')
            continue
        old = state.get(f'APP_{name}')
        if tick and not first and old and abs(procs[name] - int(old)) > 300:
            r.flag('WARN', f'sam-app zoo restarted: {name} since {utc_day(procs[name])}')
        if tick:
            state[f'APP_{name}'] = str(procs[name])
    if int(a['data_pct'] or 0) >= DISK_WARN:
        r.flag('WARN', f"sam-app /data at {a['data_pct']}%")
    heads = {name: (sha, int(ct), subj) for name, sha, ct, subj in recs['arepo']}
    old_heads = dict(kv.split(':', 1) for kv in filter(None, state.get('ZOOHEADS', '').split(',')))
    for name, (sha, ct, subj) in heads.items():
        if tick and not first and old_heads.get(name) not in (None, sha):
            r.flag('WARN', f'zoo: {name} {old_heads[name]} -> {sha} ({utc_day(ct)}) "{subj}"')
    if tick:
        state['ZOOHEADS'] = ','.join(f'{n}:{v[0]}' for n, v in sorted(heads.items()))
    sync = dict(kv.split('=', 1) for kv in recs['async'][0]) if recs['async'] else {}
    puts = {(m, st): int(n) for m, st, n in recs['aput']}
    calls = sum(puts.values())
    seen = sum(int(rec[4]) for rec in recs['api'] if label_for(r.rules, *rec[:3]) == app_label)
    last = int(sync.get('last', 0))
    mix = ' '.join(f'{m} {st}x{n}' for (m, st), n in sorted(puts.items())) or 'no calls'
    r.say(f"syncd: files {sync.get('applied', 0)} applied · {mix} · deferred {sync.get('defer', 0)}"
          f" · errors {sync.get('errors', 0)} · last {utc_day(last) if last else '-'}"
          f" (≈ tomcat saw {seen} from {app_label})")
    for (m, st), n in puts.items():
        if st != '200' and m == 'PUT':
            r.flag('FAIL', f'syncd PUT answered {st} x{n}')
        elif not st.startswith('2'):
            r.flag('WARN', f'syncd {m} answered {st} x{n}')
    for n, f, l, text in recs['aerr']:
        r.flag('WARN', f'syncd logged x{n} {utc(f)}-{utc(l)}: {text}')
    return calls


def utc_day(epoch):
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime('%Y-%m-%d %H:%MZ')


def append_days(recs, rules, tsv):
    """Appends one row per (date, family, label) to the history; returns the dates written."""
    rows = defaultdict(lambda: [0, 0, 0])
    for d, fam, cl, ua, st, n in recs['day']:
        label = label_for(rules, fam, cl, ua) or f'new:{cl}'
        cell = rows[(d, fam, label)]
        if not authed(st):
            cell[2] += int(n)
            continue
        cell[0] += int(n)
        if st.startswith('5'):
            cell[1] += int(n)
    for d, fam, n in recs['dayweb']:
        rows[(d, f'web:{fam}', '-')][0] += int(n)
    for kind in ('dayscan', 'dayprobe'):
        for d, n in recs[kind]:
            rows[(d, kind[3:], '-')][0] += int(n)
    have = tsv_dates(tsv)
    written = sorted({d for d, _, _ in rows} - have)
    with open(tsv, 'a') as fh:
        for (d, fam, label), (n, n5, ref) in sorted(rows.items()):
            if d in written:
                fh.write(f'{d}\t{fam}\t{label}\t{n}\t{n5}\t{ref}\n')
    return written


def load_history(tsv):
    hist = defaultdict(lambda: defaultdict(int))
    if os.path.exists(tsv):
        for line in open(tsv):
            d, fam, label, n, _n5, _ref = line.rstrip('\n').split('\t')
            if label not in EXCLUDED and '/' in fam:
                hist[d][fam] += int(n)
    return hist


def cutover_line(r, tsv, day):
    hist = load_history(tsv)
    prev = (datetime.strptime(day, '%Y%m%d') - timedelta(days=7)).strftime('%Y%m%d')
    groups, pgroups = defaultdict(int), defaultdict(int)
    for fam, n in hist[day].items():
        groups[status_of(fam)] += n
    for fam, n in hist.get(prev, {}).items():
        pgroups[status_of(fam)] += n
    wk = f' ({datetime.strptime(day, "%Y%m%d"):%a}, vs {prev})'
    r.say(f'cutover {day}{wk}: ' + ' · '.join(
        f'{g} {fmt(groups[g])} ({fmt(pgroups[g])})' for g in ('ported', 'porting', 'other', 'retired', 'cutover')))
    top = sorted(hist[day].items(), key=lambda kv: -kv[1])[:8]
    r.say('  ' + ' · '.join(f'{f} {fmt(n)} ({fmt(hist.get(prev, {}).get(f, 0))})' for f, n in top))


def cmd_report(a):
    rules = load_map(a.map)
    state = load_state(a.state)
    baseline = not state
    recs = read_records(sys.stdin)
    r = Report(rules)
    tick = a.mode == 'tick'
    if recs['host']:
        report_host(r, recs, state, baseline or not tick)
        report_traffic(r, recs, tick, a.to)
        report_errors(r, recs, state, baseline or not tick)
        report_probe(r, recs, tick, a.to)
    if recs['app']:
        report_app(r, recs, state, tick, a.app_label)
    if recs['day'] or recs['dayweb'] or recs['dayprobe']:
        written = append_days(recs, rules, a.tsv)
        if a.mode == 'backfill':
            r.say(f'history: {len(written)} day(s) appended to {a.tsv}')
        elif written or a.day:
            cutover_line(r, a.tsv, a.day or written[-1])
    if tick and (recs['host'] or recs['app']):
        if baseline:
            r.say('  (baseline seeded; restart and new-error checks start next tick)')
        with open(a.state, 'w') as fh:
            fh.writelines(f'{k}={v}\n' for k, v in sorted(state.items()))
    print('\n'.join(r.lines))
    print(f'__verdict {r.warn} {r.fail}')


def cmd_trend(a):
    hist = load_history(a.tsv)
    days = sorted(hist)[-a.days:]
    if not days:
        print(f'no history in {a.tsv}; run --backfill first')
        return
    fams = sorted({f for d in days for f in hist[d]}, key=lambda f: (status_of(f), -sum(hist[d][f] for d in days)))
    w = max(len(f) for f in fams) + 10
    print(f"{'family':<{w}}" + ''.join(f'{d[4:6]}-{d[6:]:>2}'.rjust(7) for d in days))
    for f in fams:
        print(f'{f + " [" + status_of(f) + "]":<{w}}' + ''.join(f'{hist[d][f]:>7}' for d in days))
    for g in ('ported', 'porting'):
        print(f'{"= " + g:<{w}}' + ''.join(
            f'{sum(n for f, n in hist[d].items() if status_of(f) == g):>7}' for d in days))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('times')
    t.add_argument('--span', default='35m')
    t.add_argument('--at')
    t.add_argument('--now', type=int)
    d = sub.add_parser('days')
    d.add_argument('--tsv', required=True)
    d.add_argument('--date')
    d.add_argument('--backfill', type=int)
    d.add_argument('--now', type=int)
    rp = sub.add_parser('report')
    rp.add_argument('--map')
    rp.add_argument('--state', required=True)
    rp.add_argument('--tsv', required=True)
    rp.add_argument('--mode', choices=('tick', 'at', 'daily', 'backfill'), default='tick')
    rp.add_argument('--to', type=int, default=0)
    rp.add_argument('--day')
    rp.add_argument('--app-label', default='ldapsync/sam-app')
    tr = sub.add_parser('trend')
    tr.add_argument('--tsv', required=True)
    tr.add_argument('--days', type=int, default=14)
    a = p.parse_args()
    {'times': cmd_times, 'days': cmd_days, 'report': cmd_report, 'trend': cmd_trend}[a.cmd](a)


if __name__ == '__main__':
    main()
