"""Comparison logic for legacy vs new Systems Integration APIs.

Each comparator function returns a list of `CheckResult` records — one per
check — instead of raising AssertionError. The CLI entry point aggregates
the results into a report.

Tolerances mirror the original pytest module
(test_legacy_api_parity.py): subset checks allow ≤10 missing items, status
mismatches allow ≤5, allocation usage is checked at ±5% (or ±500 AU
floor), and expiration dates allow ±1 day to absorb the legacy "round
end-of-month up" quirk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .helpers import (
    count_tolerance,
    dates_within_one_day,
    normalize_gecos,
    subset_diff,
    within_tolerance,
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    summary: str
    mismatches: list[str] = field(default_factory=list)
    compared: int = 0


# ---------------------------------------------------------------------------
# Shared indexing helpers
# ---------------------------------------------------------------------------

def _branch_index(data: dict) -> dict:
    return {b['accessBranchName']: b for b in data['accessBranchDirectories']}


def _build_fstree_index(fstree_data: dict) -> dict:
    """Nested index: facility → alloc_type → project_code → resource → resource_node."""
    idx: dict = {}
    for fac in fstree_data.get('facilities', []):
        fname = fac['name']
        idx.setdefault(fname, {})
        for at in fac.get('allocationTypes', []):
            atname = at['name']
            idx[fname].setdefault(atname, {})
            for proj in at.get('projects', []):
                pcode = proj['projectCode']
                idx[fname][atname].setdefault(pcode, {})
                for res in proj.get('resources', []):
                    idx[fname][atname][pcode][res['name']] = res
    return idx


def _date_part(value) -> str | None:
    """Return the YYYY-MM-DD prefix of an ISO date/datetime string (or None).

    Legacy emits full ISO datetimes with a tz offset
    (``2021-10-01T00:00:00.000-07:00``); the new API emits naive-Mountain
    ``.isoformat()`` (``2021-10-01T00:00:00``). ``date.fromisoformat`` rejects a
    time component, so we truncate to the date before comparing. Both sides are
    Mountain wall-clock, so the date part lines up (±1 day tolerance absorbs the
    midnight/tz edge).
    """
    if not value:
        return None
    return str(value)[:10]


def _index_queue_resources(data: dict) -> dict:
    """resourceName -> {queueName -> queue_dict} for a /queue response."""
    idx: dict = {}
    for res in data.get('resources', []):
        qmap = {q['queueName']: q for q in res.get('queues', [])}
        idx[res['resourceName']] = qmap
    return idx


def _index_exemption_resources(data: dict) -> dict:
    """resourceName -> queueName -> {username -> wallClockLimit} for an
    exemptions response."""
    idx: dict = {}
    for res in data.get('resources', []):
        qmap: dict = {}
        for q in res.get('queues', []):
            qmap[q['queueName']] = {
                lim['username']: lim.get('wallClockLimit')
                for lim in q.get('limits', [])
            }
        idx[res['resourceName']] = qmap
    return idx


def collect_resource_names(new_fstree_data: dict) -> list[str]:
    """Return sorted unique resource names appearing in the new fstree response."""
    names: set = set()
    for fac in new_fstree_data.get('facilities', []):
        for at in fac.get('allocationTypes', []):
            for proj in at.get('projects', []):
                for res in proj.get('resources', []):
                    names.add(res['name'])
    return sorted(names)


# ===========================================================================
# Directory Access — 15 checks
# ===========================================================================

def compare_directory_access(legacy: dict, new: dict) -> list[CheckResult]:
    legacy_idx = _branch_index(legacy)
    new_idx = _branch_index(new)
    results: list[CheckResult] = []

    # 1. Branch names: every legacy branch must appear in new
    legacy_branches = set(legacy_idx.keys())
    new_branches = set(new_idx.keys())
    missing = legacy_branches - new_branches
    results.append(CheckResult(
        name='directory_access / branch names',
        passed=not missing,
        summary=f'{len(legacy_branches)} legacy branches vs {len(new_branches)} new',
        mismatches=[f'branch {b!r} present in legacy, absent from new' for b in sorted(missing)],
        compared=len(legacy_branches),
    ))

    shared_branches = sorted(legacy_branches & new_branches)

    # 2. Group counts within tolerance
    mismatches: list[str] = []
    compared = 0
    for branch in shared_branches:
        lcount = len(legacy_idx[branch]['unixGroups'])
        ncount = len(new_idx[branch]['unixGroups'])
        compared += 1
        tol = count_tolerance(lcount)
        if abs(lcount - ncount) > tol:
            mismatches.append(
                f'{branch}: legacy {lcount} groups, new {ncount} (tolerance ±{tol})'
            )
    results.append(CheckResult(
        name='directory_access / group counts comparable',
        passed=not mismatches,
        summary=f'{compared} branches checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 3. Group names subset
    mismatches = []
    compared = 0
    for branch in shared_branches:
        legacy_names = {g['groupName'] for g in legacy_idx[branch]['unixGroups']}
        new_names = {g['groupName'] for g in new_idx[branch]['unixGroups']}
        compared += len(legacy_names)
        miss, ok = subset_diff(legacy_names, new_names, max_missing=10)
        if not ok:
            mismatches.append(
                f'{branch}: {len(miss)} legacy group names missing from new '
                f'(tolerance 10). Sample: {sorted(miss)[:10]}'
            )
    results.append(CheckResult(
        name='directory_access / group names ⊆ new',
        passed=not mismatches,
        summary=f'{compared} legacy group names checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 4. Group GIDs match for shared names
    mismatches = []
    compared = 0
    for branch in shared_branches:
        new_groups = {g['groupName']: g['gid'] for g in new_idx[branch]['unixGroups']}
        for grp in legacy_idx[branch]['unixGroups']:
            name = grp['groupName']
            if name in new_groups:
                compared += 1
                if grp['gid'] != new_groups[name]:
                    mismatches.append(
                        f'{branch}/{name}: legacy GID={grp["gid"]}, new GID={new_groups[name]}'
                    )
    results.append(CheckResult(
        name='directory_access / group GIDs match',
        passed=not mismatches,
        summary=f'{compared} shared groups checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 5. Group members subset (≤5 missing per group)
    failures: list[str] = []
    compared = 0
    for branch in shared_branches:
        new_groups = {g['groupName']: set(g['usernames']) for g in new_idx[branch]['unixGroups']}
        for grp in legacy_idx[branch]['unixGroups']:
            name = grp['groupName']
            if name not in new_groups:
                continue
            compared += 1
            legacy_members = set(grp['usernames'])
            missing = legacy_members - new_groups[name]
            if len(missing) > 5:
                failures.append(
                    f'{branch}/{name}: {len(missing)} legacy members missing from new '
                    f'(tolerance 5). Sample: {sorted(missing)[:10]}'
                )
    results.append(CheckResult(
        name='directory_access / group members ⊆ new',
        passed=not failures,
        summary=f'{compared} shared groups checked',
        mismatches=failures,
        compared=compared,
    ))

    # 6. Account counts within tolerance
    mismatches = []
    compared = 0
    for branch in shared_branches:
        lcount = len(legacy_idx[branch]['unixAccounts'])
        ncount = len(new_idx[branch]['unixAccounts'])
        compared += 1
        tol = count_tolerance(lcount)
        if abs(lcount - ncount) > tol:
            mismatches.append(
                f'{branch}: legacy {lcount} accounts, new {ncount} (tolerance ±{tol})'
            )
    results.append(CheckResult(
        name='directory_access / account counts comparable',
        passed=not mismatches,
        summary=f'{compared} branches checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 7. Account usernames subset
    mismatches = []
    compared = 0
    for branch in shared_branches:
        legacy_users = {a['username'] for a in legacy_idx[branch]['unixAccounts']}
        new_users = {a['username'] for a in new_idx[branch]['unixAccounts']}
        compared += len(legacy_users)
        miss, ok = subset_diff(legacy_users, new_users, max_missing=10)
        if not ok:
            mismatches.append(
                f'{branch}: {len(miss)} legacy usernames missing from new '
                f'(tolerance 10). Sample: {sorted(miss)[:10]}'
            )
    results.append(CheckResult(
        name='directory_access / usernames ⊆ new',
        passed=not mismatches,
        summary=f'{compared} legacy usernames checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 8-12. Per-user field equality (uid, gid, homeDirectory, loginShell, gecos)
    field_specs = [
        ('uid', 'uid', None),
        ('gid', 'gid', None),
        ('homeDirectory', 'homeDirectory', None),
        ('loginShell', 'loginShell', None),
        ('gecos', 'gecos', normalize_gecos),
    ]
    for label, key, normalizer in field_specs:
        mismatches = []
        compared = 0
        for branch in shared_branches:
            new_users = {a['username']: a for a in new_idx[branch]['unixAccounts']}
            for acct in legacy_idx[branch]['unixAccounts']:
                uname = acct['username']
                if uname not in new_users:
                    continue
                compared += 1
                lv = acct[key]
                nv = new_users[uname][key]
                if normalizer:
                    lv_cmp = normalizer(lv)
                    nv_cmp = normalizer(nv)
                else:
                    lv_cmp, nv_cmp = lv, nv
                if lv_cmp != nv_cmp:
                    mismatches.append(
                        f'{branch}/{uname}: legacy {label}={lv!r}, new {label}={nv!r}'
                    )
        results.append(CheckResult(
            name=f'directory_access / {label} matches for shared users',
            passed=not mismatches,
            summary=f'{compared} shared users checked',
            mismatches=mismatches,
            compared=compared,
        ))

    # -----------------------------------------------------------------------
    # 13-15. Reverse direction (new − legacy).
    #
    # Checks 1-12 are all one-directional (legacy ⊆ new), so surplus rows in
    # the new endpoint can never fail them. That blind spot is exactly how the
    # ungated adhoc-member merge shipped: 3,959 group-member rows across 339
    # groups that legacy drops, with all 12 checks green.
    # -----------------------------------------------------------------------

    # 13. No surplus group members — mirror of check 5, same ±5 per-group
    #     tolerance (DB-mirror lag can legitimately add rows either way).
    failures = []
    compared = 0
    for branch in shared_branches:
        legacy_groups = {g['groupName']: set(g['usernames']) for g in legacy_idx[branch]['unixGroups']}
        for grp in new_idx[branch]['unixGroups']:
            name = grp['groupName']
            if name not in legacy_groups:
                continue
            compared += 1
            surplus, ok = subset_diff(set(grp['usernames']), legacy_groups[name], max_missing=5)
            if not ok:
                failures.append(
                    f'{branch}/{name}: {len(surplus)} new members absent from legacy '
                    f'(tolerance 5). Sample: {sorted(surplus)[:10]}'
                )
    results.append(CheckResult(
        name='directory_access / no surplus group members',
        passed=not failures,
        summary=f'{compared} shared groups checked',
        mismatches=failures,
        compared=compared,
    ))

    # 14. No surplus account usernames — mirror of check 7, same ±10 tolerance.
    mismatches = []
    compared = 0
    for branch in shared_branches:
        legacy_users = {a['username'] for a in legacy_idx[branch]['unixAccounts']}
        new_users = {a['username'] for a in new_idx[branch]['unixAccounts']}
        compared += len(new_users)
        surplus, ok = subset_diff(new_users, legacy_users, max_missing=10)
        if not ok:
            mismatches.append(
                f'{branch}: {len(surplus)} new usernames absent from legacy '
                f'(tolerance 10). Sample: {sorted(surplus)[:10]}'
            )
    results.append(CheckResult(
        name='directory_access / no surplus account usernames',
        passed=not mismatches,
        summary=f'{compared} new usernames checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 15. No dangling group members. Unlike every other check this is a
    #     self-consistency invariant of a single payload, not a comparison —
    #     a group member with no unixAccounts entry is a dangling reference for
    #     the downstream LDAP provisioner. No tolerance: it must be exactly 0.
    #     Both payloads are checked; legacy is the control (already 0).
    mismatches = []
    compared = 0
    for label, idx in (('legacy', legacy_idx), ('new', new_idx)):
        for branch in sorted(idx):
            accounts = {a['username'] for a in idx[branch]['unixAccounts']}
            members = set()
            for grp in idx[branch]['unixGroups']:
                members.update(grp['usernames'])
            compared += len(members)
            dangling = members - accounts
            if dangling:
                mismatches.append(
                    f'{label} {branch}: {len(dangling)} group members have no '
                    f'unixAccounts entry. Sample: {sorted(dangling)[:10]}'
                )
    results.append(CheckResult(
        name='directory_access / no dangling group members',
        passed=not mismatches,
        summary=f'{compared} group memberships checked across both payloads',
        mismatches=mismatches,
        compared=compared,
    ))

    return results


# ===========================================================================
# Project Access — 7 checks
# ===========================================================================

_LIVE_STATUSES = frozenset({'ACTIVE', 'EXPIRING', 'EXPIRED'})


def compare_project_access(legacy_by_branch: dict, new: dict) -> list[CheckResult]:
    """legacy_by_branch: {branch: [project_dict, ...]} — one fetch per branch."""
    results: list[CheckResult] = []

    # 1. All branches covered
    missing_branches = [b for b in legacy_by_branch if b not in new]
    results.append(CheckResult(
        name='project_access / branches covered',
        passed=not missing_branches,
        summary=f'{len(legacy_by_branch)} legacy branches vs {len(new)} new',
        mismatches=[f'branch {b!r} present in legacy, absent from new'
                    for b in missing_branches],
        compared=len(legacy_by_branch),
    ))

    shared_branches = sorted(b for b in legacy_by_branch if b in new)

    # 2. Project counts within tolerance
    mismatches = []
    compared = 0
    for branch in shared_branches:
        lcount = len(legacy_by_branch[branch])
        ncount = len(new[branch])
        compared += 1
        tol = count_tolerance(lcount)
        if abs(lcount - ncount) > tol:
            mismatches.append(
                f'{branch}: legacy {lcount} projects, new {ncount} (tolerance ±{tol})'
            )
    results.append(CheckResult(
        name='project_access / project counts comparable',
        passed=not mismatches,
        summary=f'{compared} branches checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 3. Project names subset
    mismatches = []
    compared = 0
    for branch in shared_branches:
        legacy_names = {p['groupName'] for p in legacy_by_branch[branch]}
        new_names = {p['groupName'] for p in new[branch]}
        compared += len(legacy_names)
        miss, ok = subset_diff(legacy_names, new_names, max_missing=10)
        if not ok:
            mismatches.append(
                f'{branch}: {len(miss)} legacy project names missing from new '
                f'(tolerance 10). Sample: {sorted(miss)[:10]}'
            )
    results.append(CheckResult(
        name='project_access / project names ⊆ new',
        passed=not mismatches,
        summary=f'{compared} legacy project names checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 4. DEAD projects consistent (new=DEAD must be legacy=DEAD if present)
    failures = []
    compared = 0
    for branch in shared_branches:
        legacy_by_name = {p['groupName']: p for p in legacy_by_branch[branch]}
        for proj in new[branch]:
            if proj['status'] != 'DEAD':
                continue
            if proj['groupName'] not in legacy_by_name:
                continue
            compared += 1
            ls = legacy_by_name[proj['groupName']].get('status', '')
            if ls != 'DEAD':
                failures.append(f'{branch}/{proj["groupName"]}: new=DEAD, legacy={ls!r}')
        if len(failures) > 5:
            break
    results.append(CheckResult(
        name='project_access / DEAD projects consistent',
        passed=len(failures) <= 5,
        summary=f'{compared} new DEAD projects checked (tolerance 5 mismatches)',
        mismatches=failures,
        compared=compared,
    ))

    # 5. Live projects consistent (new ACTIVE/EXPIRING/EXPIRED must not be legacy DEAD)
    failures = []
    compared = 0
    for branch in shared_branches:
        legacy_by_name = {p['groupName']: p for p in legacy_by_branch[branch]}
        for proj in new[branch]:
            if proj['status'] not in _LIVE_STATUSES:
                continue
            if proj['groupName'] not in legacy_by_name:
                continue
            compared += 1
            ls = legacy_by_name[proj['groupName']].get('status', '')
            if ls == 'DEAD':
                failures.append(
                    f'{branch}/{proj["groupName"]}: new={proj["status"]}, legacy=DEAD'
                )
    results.append(CheckResult(
        name='project_access / live projects not legacy-DEAD',
        passed=len(failures) <= 5,
        summary=f'{compared} new live projects checked (tolerance 5 mismatches)',
        mismatches=failures,
        compared=compared,
    ))

    # 6. Expiration dates match within ±1 day
    mismatches = []
    compared = 0
    for branch in shared_branches:
        new_by_name = {p['groupName']: p for p in new[branch]}
        for proj in legacy_by_branch[branch]:
            name = proj['groupName']
            if name not in new_by_name:
                continue
            le = proj.get('expiration')
            ne = new_by_name[name].get('expiration')
            if not (le and ne):
                continue
            compared += 1
            if not dates_within_one_day(le, ne):
                mismatches.append(f'{branch}/{name}: legacy={le!r}, new={ne!r}')
    results.append(CheckResult(
        name='project_access / expiration dates within ±1 day',
        passed=not mismatches,
        summary=f'{compared} matched projects checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 7. resourceGroupStatuses subset (legacy entries appear in new w/ matching dates)
    failures = []
    compared = 0
    for branch in shared_branches:
        new_by_name = {p['groupName']: p for p in new[branch]}
        for proj in legacy_by_branch[branch]:
            name = proj['groupName']
            if name not in new_by_name:
                continue
            new_rgs = {
                r['resourceName']: r['endDate']
                for r in new_by_name[name].get('resourceGroupStatuses', [])
            }
            for rgs in proj.get('resourceGroupStatuses', []):
                rname = rgs['resourceName']
                compared += 1
                if rname not in new_rgs:
                    failures.append(f'{branch}/{name}: resource {rname!r} missing from new')
                elif not dates_within_one_day(rgs['endDate'], new_rgs[rname]):
                    failures.append(
                        f'{branch}/{name}/{rname}: legacy endDate={rgs["endDate"]!r}, '
                        f'new endDate={new_rgs[rname]!r} (>1 day)'
                    )
    results.append(CheckResult(
        name='project_access / resourceGroupStatuses ⊆ new',
        passed=len(failures) <= 10,
        summary=f'{compared} legacy resource entries checked (tolerance 10 mismatches)',
        mismatches=failures,
        compared=compared,
    ))

    return results


# ===========================================================================
# FairShare Tree — 9 checks
# ===========================================================================

def compare_fstree_access(legacy_by_resource: dict, new: dict) -> list[CheckResult]:
    """legacy_by_resource: {resource_name: fstree_dict} — one fetch per resource."""
    results: list[CheckResult] = []
    new_idx = _build_fstree_index(new)

    # 1. Facility names: per-resource new facilities must appear in legacy
    failures = []
    compared = 0
    for resource, legacy_data in legacy_by_resource.items():
        new_fac_for_resource: set = set()
        for fac in new.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    for res in proj.get('resources', []):
                        if res['name'] == resource:
                            new_fac_for_resource.add(fac['name'])
                            break
        legacy_facilities = {f['name'] for f in legacy_data.get('facilities', [])}
        compared += len(new_fac_for_resource)
        miss, ok = subset_diff(new_fac_for_resource, legacy_facilities, max_missing=2)
        if not ok:
            failures.append(
                f'{resource}: {len(miss)} new facilities missing from legacy: {sorted(miss)}'
            )
    results.append(CheckResult(
        name='fstree / new facilities ⊆ legacy (per resource)',
        passed=not failures,
        summary=f'{compared} (resource, facility) pairs checked',
        mismatches=failures,
        compared=compared,
    ))

    # 2. Allocation types in legacy (every new alloc type must be in legacy)
    legacy_at_by_fac: dict = {}
    for legacy_data in legacy_by_resource.values():
        for fac in legacy_data.get('facilities', []):
            legacy_at_by_fac.setdefault(fac['name'], set())
            for at in fac.get('allocationTypes', []):
                legacy_at_by_fac[fac['name']].add(at['name'])

    failures = []
    compared = 0
    for fac in new.get('facilities', []):
        fname = fac['name']
        legacy_ats = legacy_at_by_fac.get(fname, set())
        for at in fac.get('allocationTypes', []):
            compared += 1
            if at['name'] not in legacy_ats:
                failures.append(f'{fname}: new alloc type {at["name"]!r} not in legacy')
    results.append(CheckResult(
        name='fstree / new allocation types ⊆ legacy',
        passed=len(failures) <= 3,
        summary=f'{compared} new alloc types checked (tolerance 3 mismatches)',
        mismatches=failures,
        compared=compared,
    ))

    # 3. New project count ≤ legacy + 10 per resource (new is active-only subset)
    def _count_projects(data, resource_filter=None):
        count = 0
        for fac in data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    if resource_filter is None:
                        count += 1
                    else:
                        for res in proj.get('resources', []):
                            if res['name'] == resource_filter:
                                count += 1
                                break
        return count

    failures = []
    compared = 0
    for resource, legacy_data in legacy_by_resource.items():
        lcount = _count_projects(legacy_data)
        ncount = _count_projects(new, resource_filter=resource)
        compared += 1
        if ncount > lcount + 10:
            failures.append(
                f'{resource}: new {ncount} projects, legacy {lcount} '
                f'(new should not greatly exceed legacy)'
            )
    results.append(CheckResult(
        name='fstree / new project count ≤ legacy (active-only subset)',
        passed=not failures,
        summary=f'{compared} resources checked',
        mismatches=failures,
        compared=compared,
    ))

    # 4. New project codes appear in legacy
    legacy_codes_by_resource: dict = {}
    for resource, legacy_data in legacy_by_resource.items():
        codes: set = set()
        for fac in legacy_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    codes.add(proj['projectCode'])
        legacy_codes_by_resource[resource] = codes

    missing_by_resource: dict = {}
    compared = 0
    for fac in new.get('facilities', []):
        for at in fac.get('allocationTypes', []):
            for proj in at.get('projects', []):
                pcode = proj['projectCode']
                for res in proj.get('resources', []):
                    rname = res['name']
                    if rname not in legacy_codes_by_resource:
                        continue
                    compared += 1
                    if pcode not in legacy_codes_by_resource[rname]:
                        missing_by_resource.setdefault(rname, set()).add(pcode)

    failures = []
    for resource, codes in missing_by_resource.items():
        if len(codes) > 10:
            failures.append(
                f'{resource}: {len(codes)} new project codes not in legacy '
                f'(tolerance 10). Sample: {sorted(codes)[:5]}'
            )
    results.append(CheckResult(
        name='fstree / new project codes ⊆ legacy',
        passed=not failures,
        summary=f'{compared} (project, resource) pairs checked',
        mismatches=failures,
        compared=compared,
    ))

    # 5. allocationAmount equality
    mismatches = []
    compared = 0
    for resource, legacy_data in legacy_by_resource.items():
        for fac in legacy_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    pcode = proj['projectCode']
                    for res in proj.get('resources', []):
                        rname = res['name']
                        new_res = (
                            new_idx.get(fac['name'], {})
                            .get(at['name'], {})
                            .get(pcode, {})
                            .get(rname)
                        )
                        if new_res is None:
                            continue
                        compared += 1
                        la = res.get('allocationAmount', 0)
                        na = new_res.get('allocationAmount', 0)
                        if la != na:
                            mismatches.append(
                                f'{resource}/{pcode}/{rname}: legacy={la}, new={na}'
                            )
    results.append(CheckResult(
        name='fstree / allocationAmount exact match',
        passed=not mismatches,
        summary=f'{compared} matched (project, resource) nodes checked',
        mismatches=mismatches,
        compared=compared,
    ))

    # 6. adjustedUsage within ±5% (or ±500 AU floor); ≤2% of nodes may differ
    failures = []
    compared = 0
    for resource, legacy_data in legacy_by_resource.items():
        for fac in legacy_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    pcode = proj['projectCode']
                    for res in proj.get('resources', []):
                        rname = res['name']
                        new_res = (
                            new_idx.get(fac['name'], {})
                            .get(at['name'], {})
                            .get(pcode, {})
                            .get(rname)
                        )
                        if new_res is None:
                            continue
                        compared += 1
                        lu = res.get('adjustedUsage', 0)
                        nu = new_res.get('adjustedUsage', 0)
                        if not within_tolerance(lu, nu, pct=5.0, abs_floor=500):
                            failures.append(
                                f'{resource}/{pcode}/{rname}: legacy={lu}, new={nu}'
                            )
    max_failures = max(10, int(compared * 0.02))
    results.append(CheckResult(
        name='fstree / adjustedUsage within ±5% (DB lag tolerance)',
        passed=len(failures) <= max_failures,
        summary=f'{compared} matched nodes checked (tolerance: {max_failures} = max(10, 2%))',
        mismatches=failures,
        compared=compared,
    ))

    # 7. balance internal consistency (allocationAmount - adjustedUsage)
    failures = []
    compared = 0
    for fac in new.get('facilities', []):
        for at in fac.get('allocationTypes', []):
            for proj in at.get('projects', []):
                for res in proj.get('resources', []):
                    compared += 1
                    amt = res.get('allocationAmount', 0)
                    usage = res.get('adjustedUsage', 0)
                    bal = res.get('balance', 0)
                    if abs(bal - (amt - usage)) > 1:
                        failures.append(
                            f'new/{proj["projectCode"]}/{res["name"]}: '
                            f'balance={bal}, amt-usage={amt - usage}'
                        )
    for resource, legacy_data in legacy_by_resource.items():
        for fac in legacy_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    for res in proj.get('resources', []):
                        compared += 1
                        amt = res.get('allocationAmount', 0)
                        usage = res.get('adjustedUsage', 0)
                        bal = res.get('balance', 0)
                        if abs(bal - (amt - usage)) > 1:
                            failures.append(
                                f'legacy/{resource}/{proj["projectCode"]}/{res["name"]}: '
                                f'balance={bal}, amt-usage={amt - usage}'
                            )
    results.append(CheckResult(
        name='fstree / balance == allocationAmount − adjustedUsage',
        passed=len(failures) <= 5,
        summary=f'{compared} resource nodes checked (tolerance 5)',
        mismatches=failures,
        compared=compared,
    ))

    # 8. User rosters: legacy users ⊆ new (≤3 missing per node)
    failures = []
    compared = 0
    for resource, legacy_data in legacy_by_resource.items():
        for fac in legacy_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    pcode = proj['projectCode']
                    for res in proj.get('resources', []):
                        rname = res['name']
                        new_res = (
                            new_idx.get(fac['name'], {})
                            .get(at['name'], {})
                            .get(pcode, {})
                            .get(rname)
                        )
                        if new_res is None:
                            continue
                        compared += 1
                        legacy_users = {u['username'] for u in res.get('users', [])}
                        new_users = {u['username'] for u in new_res.get('users', [])}
                        missing = legacy_users - new_users
                        if len(missing) > 3:
                            failures.append(
                                f'{resource}/{pcode}/{rname}: '
                                f'{len(missing)} legacy users missing from new: '
                                f'{sorted(missing)[:5]}'
                            )
    results.append(CheckResult(
        name='fstree / legacy users ⊆ new (per project+resource)',
        passed=len(failures) <= 10,
        summary=f'{compared} matched nodes checked (tolerance 10)',
        mismatches=failures,
        compared=compared,
    ))

    # 9. accountStatus: if legacy is non-Normal, new should also be non-Normal
    failures = []
    compared = 0
    for resource, legacy_data in legacy_by_resource.items():
        for fac in legacy_data.get('facilities', []):
            for at in fac.get('allocationTypes', []):
                for proj in at.get('projects', []):
                    pcode = proj['projectCode']
                    for res in proj.get('resources', []):
                        rname = res['name']
                        new_res = (
                            new_idx.get(fac['name'], {})
                            .get(at['name'], {})
                            .get(pcode, {})
                            .get(rname)
                        )
                        if new_res is None:
                            continue
                        compared += 1
                        ls = res.get('accountStatus', 'Normal')
                        ns = new_res.get('accountStatus', 'Normal')
                        if ls != 'Normal' and ns == 'Normal':
                            failures.append(
                                f'{resource}/{pcode}/{rname}: legacy={ls!r}, new=Normal'
                            )
    results.append(CheckResult(
        name='fstree / accountStatus consistency (legacy non-Normal ⇒ new non-Normal)',
        passed=len(failures) <= 5,
        summary=f'{compared} matched nodes checked (tolerance 5)',
        mismatches=failures,
        compared=compared,
    ))

    return results


# ===========================================================================
# Queue — 5 checks
# ===========================================================================

def compare_queue(legacy: dict, new: dict) -> list[CheckResult]:
    """Compare the ``/ssg/queue`` (all-resources) responses.

    Both sides share the shape ``{name, resources:[{resourceName, queues:[...]}]}``.
    Comparison is order-insensitive (indexed by resourceName / queueName).
    """
    results: list[CheckResult] = []
    legacy_idx = _index_queue_resources(legacy)
    new_idx = _index_queue_resources(new)

    # 1. Resource names: legacy resources should appear in new (DB-mirror lag tol)
    legacy_resources = set(legacy_idx)
    new_resources = set(new_idx)
    missing, ok = subset_diff(legacy_resources, new_resources, max_missing=2)
    results.append(CheckResult(
        name='queue / legacy resources ⊆ new',
        passed=ok,
        summary=f'{len(legacy_resources)} legacy resources vs {len(new_resources)} new',
        mismatches=[f'resource {r!r} present in legacy, absent from new'
                    for r in sorted(missing)],
        compared=len(legacy_resources),
    ))

    shared_resources = sorted(legacy_resources & new_resources)

    # 2. Queue names per resource: legacy ⊆ new (tolerance 5 — start_date tightening)
    failures: list[str] = []
    compared = 0
    for r in shared_resources:
        legacy_qs = set(legacy_idx[r])
        new_qs = set(new_idx[r])
        compared += len(legacy_qs)
        miss, sub_ok = subset_diff(legacy_qs, new_qs, max_missing=5)
        if not sub_ok:
            failures.append(
                f'{r}: {len(miss)} legacy queues missing from new '
                f'(tolerance 5). Sample: {sorted(miss)[:5]}'
            )
    results.append(CheckResult(
        name='queue / legacy queue names ⊆ new (per resource)',
        passed=not failures,
        summary=f'{compared} legacy queue names checked',
        mismatches=failures,
        compared=compared,
    ))

    # 3. wallClockHoursLimit equality for shared queues
    # 4. cosId equality for shared queues
    # 5. start/end dates within ±1 day for shared queues
    wch_mismatches: list[str] = []
    cos_mismatches: list[str] = []
    date_mismatches: list[str] = []
    compared = 0
    for r in shared_resources:
        for qname, lq in legacy_idx[r].items():
            nq = new_idx[r].get(qname)
            if nq is None:
                continue
            compared += 1
            lw, nw = lq.get('wallClockHoursLimit'), nq.get('wallClockHoursLimit')
            if lw is None or nw is None:
                if lw is not nw:
                    wch_mismatches.append(f'{r}/{qname}: legacy={lw}, new={nw}')
            elif not within_tolerance(lw, nw, pct=0.1):
                wch_mismatches.append(f'{r}/{qname}: legacy={lw}, new={nw}')

            if lq.get('cosId') != nq.get('cosId'):
                cos_mismatches.append(
                    f'{r}/{qname}: legacy cosId={lq.get("cosId")}, new={nq.get("cosId")}'
                )

            for field in ('startDate', 'endDate'):
                ld, nd = _date_part(lq.get(field)), _date_part(nq.get(field))
                if ld is None or nd is None:
                    if ld is not nd:
                        date_mismatches.append(
                            f'{r}/{qname}/{field}: legacy={lq.get(field)!r}, new={nq.get(field)!r}'
                        )
                elif not dates_within_one_day(ld, nd):
                    date_mismatches.append(
                        f'{r}/{qname}/{field}: legacy={ld!r}, new={nd!r}'
                    )

    results.append(CheckResult(
        name='queue / wallClockHoursLimit match',
        passed=not wch_mismatches,
        summary=f'{compared} shared queues checked',
        mismatches=wch_mismatches,
        compared=compared,
    ))
    results.append(CheckResult(
        name='queue / cosId match',
        passed=not cos_mismatches,
        summary=f'{compared} shared queues checked',
        mismatches=cos_mismatches,
        compared=compared,
    ))
    results.append(CheckResult(
        name='queue / start & end dates within ±1 day',
        passed=not date_mismatches,
        summary=f'{compared} shared queues checked',
        mismatches=date_mismatches,
        compared=compared,
    ))

    return results


# ===========================================================================
# WallClock Exemption — 4 checks
# ===========================================================================

def compare_wallclock_exemption(legacy: dict, new: dict) -> list[CheckResult]:
    """Compare the ``/ssg/wallClockExemption`` responses.

    Both sides share the shape
    ``{name, resources:[{resourceName, queues:[{queueName, limits:[...]}]}]}``.
    Comparison is order-insensitive (indexed by resourceName / queueName / username).
    """
    results: list[CheckResult] = []
    legacy_idx = _index_exemption_resources(legacy)
    new_idx = _index_exemption_resources(new)

    # 1. Resource names: legacy ⊆ new
    legacy_resources = set(legacy_idx)
    new_resources = set(new_idx)
    missing, ok = subset_diff(legacy_resources, new_resources, max_missing=2)
    results.append(CheckResult(
        name='wallclock / legacy resources ⊆ new',
        passed=ok,
        summary=f'{len(legacy_resources)} legacy resources vs {len(new_resources)} new',
        mismatches=[f'resource {r!r} present in legacy, absent from new'
                    for r in sorted(missing)],
        compared=len(legacy_resources),
    ))

    shared_resources = sorted(legacy_resources & new_resources)

    # 2. Queue names per resource: legacy ⊆ new
    failures: list[str] = []
    compared = 0
    for r in shared_resources:
        legacy_qs = set(legacy_idx[r])
        new_qs = set(new_idx[r])
        compared += len(legacy_qs)
        miss, sub_ok = subset_diff(legacy_qs, new_qs, max_missing=3)
        if not sub_ok:
            failures.append(
                f'{r}: {len(miss)} legacy exemption-queues missing from new '
                f'(tolerance 3). Sample: {sorted(miss)[:5]}'
            )
    results.append(CheckResult(
        name='wallclock / legacy queue names ⊆ new (per resource)',
        passed=not failures,
        summary=f'{compared} legacy queue names checked',
        mismatches=failures,
        compared=compared,
    ))

    # 3. Usernames per (resource, queue): legacy ⊆ new
    user_failures: list[str] = []
    compared = 0
    for r in shared_resources:
        for qname, legacy_users in legacy_idx[r].items():
            new_users = new_idx[r].get(qname)
            if new_users is None:
                continue
            compared += len(legacy_users)
            miss, sub_ok = subset_diff(set(legacy_users), set(new_users), max_missing=3)
            if not sub_ok:
                user_failures.append(
                    f'{r}/{qname}: {len(miss)} legacy users missing from new '
                    f'(tolerance 3). Sample: {sorted(miss)[:5]}'
                )
    results.append(CheckResult(
        name='wallclock / legacy users ⊆ new (per resource+queue)',
        passed=not user_failures,
        summary=f'{compared} legacy user exemptions checked',
        mismatches=user_failures,
        compared=compared,
    ))

    # 4. wallClockLimit equality for shared (resource, queue, user)
    limit_mismatches: list[str] = []
    compared = 0
    for r in shared_resources:
        for qname, legacy_users in legacy_idx[r].items():
            new_users = new_idx[r].get(qname)
            if new_users is None:
                continue
            for uname, lw in legacy_users.items():
                if uname not in new_users:
                    continue
                compared += 1
                nw = new_users[uname]
                if lw is None or nw is None:
                    if lw is not nw:
                        limit_mismatches.append(f'{r}/{qname}/{uname}: legacy={lw}, new={nw}')
                elif not within_tolerance(lw, nw, pct=0.1):
                    limit_mismatches.append(f'{r}/{qname}/{uname}: legacy={lw}, new={nw}')
    results.append(CheckResult(
        name='wallclock / wallClockLimit match for shared users',
        passed=not limit_mismatches,
        summary=f'{compared} shared user exemptions checked',
        mismatches=limit_mismatches,
        compared=compared,
    ))

    return results


# ---------------------------------------------------------------------------
# XRAS (/api/xras/v1/*)
# ---------------------------------------------------------------------------
#
# The strictest comparator here, and deliberately so. Every other API in this
# module is compared with tolerance — subset checks, ±5% on usage, ±1 day on
# dates — because those legacy endpoints read a *mirrored* database and some
# lag is expected. XRAS has no such lag: both stacks read the same production
# `sam` schema (see XRAS_REIMPLEMENTATION.md section 4.3), so any difference is
# a rendering difference and must fail.
#
# That is also why comparison is on raw bytes rather than parsed JSON. The bugs
# worth catching here are length-preserving — swapped firstName/lastName, a
# reordered field, a `%.1f` that became `%.2f` and back — and a parsed
# comparison sees none of them.
# ---------------------------------------------------------------------------

#: `masters[]` is emitted in Java `HashMap` bucket order over the projcode
#: keys. That is an artifact of legacy's data structure, not of the data, and
#: reproducing it was rejected (see section 7 of the plan doc), so the two
#: multi-master endpoints are compared master-by-master instead of byte-by-byte.
_ORDER_INSENSITIVE_MASTERS = True


def _first_difference(legacy: bytes, new: bytes) -> str:
    """Describe where two bodies diverge, in a form that is actually useful.

    Reports the size delta first (usually the whole story), then the byte
    offset of the first difference with a window of context either side.
    """
    if legacy == new:
        return ''
    if len(legacy) != len(new):
        head = f'size {len(legacy):,} -> {len(new):,} ({len(new) - len(legacy):+,} B)'
    else:
        head = f'same size ({len(legacy):,} B), content differs'
    limit = min(len(legacy), len(new))
    offset = next((i for i in range(limit) if legacy[i] != new[i]), limit)
    lo, hi = max(0, offset - 40), offset + 40
    return (f'{head}; first difference at byte {offset:,}\n'
            f'      legacy: ...{legacy[lo:hi]!r}...\n'
            f'      new:    ...{new[lo:hi]!r}...')


def _byte_check(name: str, legacy: bytes, new: bytes) -> CheckResult:
    diff = _first_difference(legacy, new)
    return CheckResult(
        name=name,
        passed=not diff,
        summary=(f'{len(legacy):,} B identical' if not diff
                 else 'bodies differ'),
        mismatches=[diff] if diff else [],
        compared=1,
    )


def _canonicalise_master(master: dict) -> dict:
    """Sort the one array whose legacy order is not derived from the data.

    `allocations[]` follows `xras_allocation`'s `ORDER BY al.start_date DESC`,
    which is **not a total order** — one production project has 11 allocations
    sharing a start_date. MySQL returns tied rows in whatever order it likes, so
    legacy's own output is arbitrary there (measured against production for
    SCSG0001: the 6 request groups with no tie match a deterministic order
    exactly; the 9 with a tie match neither ascending nor descending
    allocation_id). Our port adds a primary-key tiebreaker so its output is at
    least reproducible.

    Sorting both sides by the serialized allocation keeps the *content* check
    exact — every field, value and key order still has to match — while
    ignoring an order neither side can be held to. Same reasoning as the
    `masters[]` relaxation, one level down.
    """
    import json

    out = dict(master)
    out['requests'] = [
        {**request,
         'allocations': sorted(
             request.get('allocations', []),
             key=lambda a: json.dumps(a, separators=(',', ':'),
                                      ensure_ascii=False))}
        for request in master.get('requests', [])
    ]
    return out


def _masters_check(name: str, legacy: bytes, new: bytes) -> CheckResult:
    """Compare an `AccountingRequestResponse` master-by-master.

    Byte-exact *within* each master, except for the two array orders legacy
    does not derive from the data: the master sequence itself (Java `HashMap`
    bucket order) and `allocations[]` under a `start_date` tie.
    """
    import json

    try:
        lm = {m['requestNumber']: _canonicalise_master(m)
              for m in json.loads(legacy)['result']['masters']}
        nm = {m['requestNumber']: _canonicalise_master(m)
              for m in json.loads(new)['result']['masters']}
    except (ValueError, KeyError, TypeError) as exc:
        return CheckResult(name=name, passed=False,
                           summary='unparseable response',
                           mismatches=[str(exc)], compared=0)

    mismatches = []
    for code in sorted(set(lm) - set(nm)):
        mismatches.append(f'master {code!r} present in legacy, absent from new')
    for code in sorted(set(nm) - set(lm)):
        mismatches.append(f'master {code!r} present in new, absent from legacy')

    shared = sorted(set(lm) & set(nm))
    for code in shared:
        # Re-serialize each master with the same writer, so the comparison sees
        # field order and value formatting but not the enclosing array order.
        lb = json.dumps(lm[code], separators=(',', ':'), ensure_ascii=False)
        nb = json.dumps(nm[code], separators=(',', ':'), ensure_ascii=False)
        if lb != nb:
            mismatches.append(
                f'master {code}: ' + _first_difference(lb.encode(), nb.encode()))

    return CheckResult(
        name=name,
        passed=not mismatches,
        summary=f'{len(shared)} masters compared byte-exact (order-insensitive)',
        mismatches=mismatches,
        compared=len(shared),
    )


def compare_xras(legacy: dict, new: dict) -> list[CheckResult]:
    """Compare captured XRAS responses.

    Both arguments map a probe label to `(status, body_bytes)`; the caller
    (`_fetch_xras`) decides which usernames and projcodes to sample.
    """
    results: list[CheckResult] = []

    for label in sorted(set(legacy) & set(new)):
        l_status, l_body = legacy[label]
        n_status, n_body = new[label]

        if l_status != n_status:
            results.append(CheckResult(
                name=f'xras / {label} status',
                passed=False,
                summary=f'legacy HTTP {l_status}, new HTTP {n_status}',
                mismatches=[f'status {l_status} != {n_status}'],
                compared=1,
            ))
            continue

        if _ORDER_INSENSITIVE_MASTERS and label.startswith(
                ('requests/request', 'requests/user', 'requests/role')):
            results.append(_masters_check(f'xras / {label}', l_body, n_body))
        else:
            results.append(_byte_check(f'xras / {label}', l_body, n_body))

    missing = sorted(set(legacy) ^ set(new))
    if missing:
        results.append(CheckResult(
            name='xras / probe coverage',
            passed=False,
            summary=f'{len(missing)} probes ran against only one stack',
            mismatches=missing,
            compared=0,
        ))

    return results
