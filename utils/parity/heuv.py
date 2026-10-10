"""HEUV parity: sample over HTTP, fetch every ported route from both stacks, compare bytes.

Legacy bodies are changed only by the named rules of ``docs/plans/HEUV_API_PORT.md`` §6
(F1-F10) before a byte comparison; anything else counts as a difference. Row sets are
compared in both directions. The §7 ruleset counters ride along as an informational check,
for the old/new statements Ben decides on.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from utils.parity.comparators import CheckResult

USER_ROUTES = ('defaultproject', 'group', 'access', 'assignedproject?thresholdlimited=false',
               'assignedproject', 'assignedproject?thresholdlimited=true',
               'assignedresource?thresholdlimited=false', 'userrolelogin', 'wallclockexemption')
ALWAYS_PROJECTS = ('SCSG0001', 'NCIS0001')
AMOUNT_KEYS = frozenset({'allocationAmount', 'balance'})


@dataclass
class Pair:
    kind: str
    rule: str
    legacy: tuple
    new: tuple
    ms: tuple = (0.0, 0.0)


@dataclass
class Outcome:
    identical: int = 0
    normalized: Counter = field(default_factory=Counter)
    different: list = field(default_factory=list)


def _compact(obj) -> bytes:
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode()


def _kind(rule: str) -> str:
    path = rule.split('?')[0]
    parts = path.split('/')
    if parts[0] == 'user':
        return f'user/{parts[2]}'
    if path.startswith('report/usage'):
        return 'report/usage'
    if path.startswith('report/project'):
        return 'report/project'
    if parts[0] == 'project':
        return 'project/hierarchy'
    if path.startswith('access/resource'):
        return 'access/resource'
    return parts[0] if parts[0] != 'search' else 'search/projcode'


def _timed_get(client, rule):
    t0 = time.monotonic()
    out = client.get(rule)
    return out, (time.monotonic() - t0) * 1000


def _fetch(legacy, new, rules, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        lres = list(pool.map(lambda r: _timed_get(legacy, r), rules))
        nres = list(pool.map(lambda r: _timed_get(new, r), rules))
    return [Pair(_kind(r), r, lo, no, (lms, nms)) for r, (lo, lms), (no, nms) in zip(rules, lres, nres)]


def _json(body: bytes):
    try:
        return json.loads(body)
    except ValueError:
        return None


def sample_and_fetch(legacy, new, *, sample_size=40, user_cap=60, seed=20261010, extra_projects=(),
                     workers=4, verbose=0, log=print) -> list[Pair]:
    status, body, _ = legacy.get('search/projcode')
    active = [p['projcode'] for p in json.loads(body)]
    rng = random.Random(seed)
    projects = [p for p in ALWAYS_PROJECTS + tuple(extra_projects) if p in active or p in extra_projects]
    projects += rng.sample([p for p in active if p not in projects], min(sample_size, len(active)))
    if verbose:
        log(f'  heuv: {len(active)} active projects, sampling {len(projects)}')

    pairs = [Pair('search/projcode', 'search/projcode', (status, body, ''), new.get('search/projcode'))]
    project_rules = [f'{route}{p}' for p in projects
                     for route in ('report/project/', 'report/usage/project/')]
    project_rules += [f'project/{p}/hierarchy' for p in projects]
    project_rules += [f'report/usage/project/{projects[0].lower()}', f'report/project/{projects[0].lower()}']
    pairs += _fetch(legacy, new, project_rules, workers)

    users = sorted({u for pr in pairs if pr.kind == 'report/usage' and pr.legacy[0] == 200
                    for row in (_json(pr.legacy[1]) or {}).get('accountReports', []) for u in row['usernames']})
    users = sorted(rng.sample(users, min(user_cap, len(users))))
    if verbose:
        log(f'  heuv: sampling {len(users)} users')
    user_rules = [f'user/{u}/{route}' for u in users for route in USER_ROUTES]
    user_rules += [f'user/{users[0].upper()}/group', f'user/{users[0].upper()}/wallclockexemption'] if users else []
    pairs += _fetch(legacy, new, user_rules, workers)

    assigned = {pc['projcode'] for pr in pairs if pr.kind == 'user/assignedproject' and pr.legacy[0] == 200
                for pc in _json(pr.legacy[1]) or []}
    inactive = sorted(assigned - set(active))[:3]
    access = _json(legacy.get('access')[1]) or []
    global_rules = ['access', 'search/projcode?fragment=zzzz', 'search/projcode?fragment=SCSG',
                    *(f'search/projcode?fragment=scs&username={u}' for u in users[:3]),
                    *(f'access/resource/{r["resourceName"]}' for r in access),
                    'access/resource/Derecho', 'access/resource/HPC',
                    'user/nosuchuserxx/group', 'user/nosuchuserxx/wallclockexemption',
                    'report/project/NOSU9999', 'report/usage/project/NOSU9999',
                    *(f'report/project/{p}' for p in inactive), *(f'report/usage/project/{p}' for p in inactive)]
    pairs += _fetch(legacy, new, global_rules, workers)
    return pairs


# ---------------------------------------------------------------------------
# Normalization (F-rules) and comparison
# ---------------------------------------------------------------------------

def _f3(kind: str, obj):
    """F3 order.sorted: legacy's HashMap/Set orders, put in the port's order."""
    def shells(r):
        if isinstance(r, dict) and 'shells' in r:
            r['shells'] = sorted(r['shells'], key=lambda s: s['shellName'])
        return r
    if kind == 'report/usage' and isinstance(obj, dict):
        obj['accountReports'] = sorted(obj['accountReports'],
                                       key=lambda r: (r['resourceUsageType'] != 'DataHoldings', r['resourceName']))
    elif kind in ('user/access', 'access') and isinstance(obj, list):
        obj = sorted((shells(r) for r in obj), key=lambda r: r['resourceName'])
    elif kind == 'access/resource':
        obj = shells(obj)
    return obj


def _first_json_diff(a, b, path='$'):
    if type(a) is not type(b):
        return path, a, b
    if isinstance(a, dict):
        if list(a) != list(b):
            return f'{path} (keys)', list(a), list(b)
        for k in a:
            d = _first_json_diff(a[k], b[k], f'{path}.{k}')
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f'{path} (length)', len(a), len(b)
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_json_diff(x, y, f'{path}[{i}]')
            if d:
                return d
        return None
    return None if a == b else (path, a, b)


def _within_f4(a, b, key=None) -> bool:
    """F4 amount.exact: allocation-derived integers may differ by 1 (legacy sums in float32)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(_within_f4(a[k], b[k], k) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_within_f4(x, y, key) for x, y in zip(a, b))
    if key in AMOUNT_KEYS and isinstance(a, int) and isinstance(b, int):
        return abs(a - b) <= 1
    return a == b


def _rows(obj, key):
    return any(r.get(key) is None for r in obj) if isinstance(obj, list) else False


def _legacy_500_fix(pair: Pair, new_obj) -> str | None:
    """The fix that explains a legacy 500 next to a new answer, or None."""
    if pair.kind == 'user/wallclockexemption' and pair.new[0] == 400:
        return 'F7 wallclock.unknown_user'
    if pair.new[0] != 200 or not isinstance(new_obj, dict):
        return None
    if pair.kind == 'report/usage':
        if pair.rule.split('/')[-1] != pair.rule.split('/')[-1].upper():
            return 'F2 usage.lowercase_projcode'
        reports = new_obj.get('accountReports', [])
        if any(t['allocationAmount'] is None for r in reports for t in r.get('thresholdReports', [])):
            return 'F1 usage.zero_threshold_alloc'
        if _rows(reports, 'allocationEndDate'):
            return 'F5 null_end_date'
    if pair.kind == 'report/project':
        if any(a['endDate'] is None for acct in new_obj.get('accounts', []) for a in acct['allocations']):
            return 'F5 null_end_date'
    return None


def classify(pair: Pair, out: Outcome) -> None:
    (ls, lb, _), (ns, nb, _) = pair.legacy, pair.new
    if (ls, lb) == (ns, nb):
        out.identical += 1
        return
    new_obj = _json(nb)
    if ls == 500:
        fix = _legacy_500_fix(pair, new_obj)
        if fix:
            out.normalized[fix] += 1
        else:
            out.different.append(f'{pair.rule}: legacy 500 {lb[:80]!r}, new {ns} {nb[:80]!r}')
        return
    if ls != ns:
        out.different.append(f'{pair.rule}: status {ls} -> {ns}; legacy {lb[:80]!r}, new {nb[:80]!r}')
        return
    legacy_obj = _json(lb)
    if legacy_obj is None or new_obj is None:
        out.different.append(f'{pair.rule}: non-JSON body; legacy {lb[:80]!r}, new {nb[:80]!r}')
        return
    roundtrip = _compact(legacy_obj) == lb
    fixed = _f3(pair.kind, json.loads(lb))
    if roundtrip and _compact(fixed) == nb:
        out.normalized['F3 order.sorted'] += 1
        return
    if _within_f4(fixed, new_obj):
        out.normalized['F4 amount.exact'] += 1
        return
    path, a, b = _first_json_diff(fixed, new_obj) or ('$ (bytes)', lb[:60], nb[:60])
    out.different.append(f'{pair.rule}: {path}: legacy {str(a)[:100]} / new {str(b)[:100]}')


# ---------------------------------------------------------------------------
# §7 counters
# ---------------------------------------------------------------------------

def counters(pairs: list[Pair]) -> list[str]:
    hierarchical = {}
    for p in pairs:
        if p.kind == 'report/project' and p.legacy[0] == 200:
            obj = _json(p.legacy[1])
            hierarchical[obj['projcode']] = obj['hierarchical']
    c = Counter()
    status_pairs, surplus, missing = Counter(), Counter(), Counter()
    charge_delta = defaultdict(list)
    for p in pairs:
        if p.legacy[0] != 200 or p.new[0] != 200:
            continue
        lo, no = _json(p.legacy[1]), _json(p.new[1])
        if p.kind == 'user/group':
            ls = {(g['groupName'], g['unixGid']) for g in lo}
            ns = {(g['groupName'], g['unixGid']) for g in no}
            c['a group users compared'] += 1
            c['a group users with new surplus'] += bool(ns - ls)
            c['a group users with new missing'] += bool(ls - ns)
            surplus.update(g for g, _ in ns - ls)
            missing.update(g for g, _ in ls - ns)
        elif p.kind == 'report/project':
            la = {a['resourceName'] for a in lo['accounts']}
            na = {a['resourceName'] for a in no['accounts']}
            c['e report/project account rows legacy-only'] += len(la - na)
            c['e report/project account rows new-only'] += len(na - la)
        elif p.kind == 'report/usage':
            lr = {r['resourceName']: r for r in lo['accountReports']}
            nr = {r['resourceName']: r for r in no['accountReports']}
            c['d/e usage rows legacy-only'] += len(set(lr) - set(nr))
            c['d/e usage rows new-only'] += len(set(nr) - set(lr))
            for name in set(lr) & set(nr):
                a, b = lr[name], nr[name]
                c['usage rows compared'] += 1
                if a['status'] != b['status']:
                    status_pairs[(a['status'], b['status'])] += 1
                if (a['allocationStartDate'], a['allocationEndDate']) != (b['allocationStartDate'], b['allocationEndDate']):
                    c['j usage rows with a different active allocation'] += 1
                if set(a['usernames']) != set(b['usernames']):
                    c['h usage rows with different usernames'] += 1
                if a['resourceUsageType'] == 'DataHoldings':
                    c['f DISK rows totalHoldings differ'] += a.get('totalHoldings') != b.get('totalHoldings')
                    c['f DISK rows numberOfFiles differ'] += a.get('numberOfFiles') != b.get('numberOfFiles')
                else:
                    if a.get('totalCharges') != b.get('totalCharges'):
                        bucket = 'hierarchical' if hierarchical.get(lo['projcode']) else 'leaf'
                        charge_delta[bucket].append(b['totalCharges'] - a['totalCharges'])
                    la = [t['allocationAmount'] for t in a.get('thresholdReports', [])]
                    na = [t['allocationAmount'] for t in b.get('thresholdReports', [])]
                    c['g threshold allocationAmount mismatches'] += sum(x != y for x, y in zip(la, na))
    lines = [f'{k}: {v}' for k, v in sorted(c.items())]
    lines += [f'i status {a!r} -> {b!r}: {n}' for (a, b), n in status_pairs.most_common()]
    lines += [f'c totalCharges differ on {k} rows: {len(v)} (median delta {statistics.median(v):+})'
              for k, v in sorted(charge_delta.items())]
    lines += [f'a surplus group {g}: {n} users' for g, n in surplus.most_common(10)]
    lines += [f'a missing group {g}: {n} users' for g, n in missing.most_common(10)]
    return lines


def compare_heuv(pairs: list[Pair]) -> list[CheckResult]:
    by_kind: dict[str, Outcome] = defaultdict(Outcome)
    ms = defaultdict(lambda: ([], []))
    for p in pairs:
        classify(p, by_kind[p.kind])
        ms[p.kind][0].append(p.ms[0])
        ms[p.kind][1].append(p.ms[1])
    results = []
    for kind, o in sorted(by_kind.items()):
        n = o.identical + sum(o.normalized.values()) + len(o.different)
        rules = ', '.join(f'{k} {v}' for k, v in sorted(o.normalized.items()))
        lat = f'p50 ms legacy {statistics.median(ms[kind][0]):.0f} / new {statistics.median(ms[kind][1]):.0f}'
        results.append(CheckResult(
            name=f'heuv {kind}', passed=not o.different, compared=n,
            summary=(f'{o.identical} identical, {sum(o.normalized.values())} normalized'
                     f'{" (" + rules + ")" if rules else ""}, {len(o.different)} different; {lat}'),
            mismatches=o.different[:5]))
    results.append(CheckResult(name='heuv §7 counters (informational)', passed=True,
                               summary='ruleset watchlist, plan §7', mismatches=counters(pairs),
                               compared=len(pairs)))
    return results
