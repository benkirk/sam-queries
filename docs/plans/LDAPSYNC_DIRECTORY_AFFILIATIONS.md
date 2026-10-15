# Affiliations that match the directory

**Status:** proposal, for review (2026-10-15). No code yet.
**Scope:** a person's positions (`user_organization`) and collaborations (`user_institution`) in SAM.
**Related:** `LDAP_SYNC_API.md` (§ 10.3 deviations D1-D4, § 10.7 soak), `LDAPSYNC_DEV_SHADOW.md`,
`SAM_LDAP_SYNCD_REFERENCE.md` (daemon bug numbers N17, N21, 1, 7).

## 1. The question and the short answer

**Goal.** SAM should hold the same organizational and institutional information as the directory:
the same current affiliations, the same employers and the same dates, end dates included, whether a
change arrives through the daemon's full pass or as a single change.

**Short answer.**

1. The daemon's lists are not a copy of the directory. The daemon keeps its own record of each
   person, seeded from SAM, and edits it as directory changes arrive.
   - It never removes a position or collaboration.
   - It erases a position's end date whenever an update says the position is active. 314 of the 316
     current positions with a planned end date have lost it in the daemon's record; the other 2 are
     missing altogether.
   - It never learns the ids of rows SAM creates.

   No matching rule in SAMuel can recover information the daemon has already dropped. Fixing this
   on the daemon's side means redesigning it, not fixing a bug. That is the whack-a-mole: each fix
   leaves the design that produces the next mole.
2. SAMuel's POST API should still stand on its own. A site without our directory has to be able to
   run SAMuel from the API alone, and get a correct copy provided its sender is faithful. So the
   first change is to SAMuel's core:
   - treat the posted lists as the person's truth;
   - reconcile SAM's rows to them properly, which means amending D2, applying end dates as sent,
     and deciding what happens to rows the sender did not list.
3. For UCAR, add an **optional directory checkup**. After a user PUT is applied, SAMuel reads that
   person's entries from the directory and runs the same reconcile against them. A nightly pass
   does the same for everyone.

   This converges by construction: each run compares the directory with SAM's actual rows, so
   anything that drifted for any reason is corrected the next time the person is touched, or
   overnight. No daemon fix is needed.

## 2. What "the same as the directory" means

The directory (`ou=allPeople` on ldap.ucar.edu, readable anonymously) holds each affiliation as a
child entry of the person:

| Directory | Fields that matter | SAM |
|---|---|---|
| `x-ucar-position` (13,717 entries) | `x-ucar-positionId` (the entry's own id), `x-ucar-organizationId`, `x-ucar-startDate`, `x-ucar-endDate`, `x-ucar-active`, `x-ucar-source` (PDB or FI) | `user_organization`, with `idms_unique_name` = `x-ucar-positionId` |
| `x-ucar-collaboration` (19,611 entries) | `x-ucar-externalOrgId` (the institution, also the entry's key), `x-ucar-startDate`, `x-ucar-endDate`, `x-ucar-active` | `user_institution` |

A finished entry is usually marked `x-ucar-active: FALSE` with **no** end date (10,969 of the 13,717
positions are inactive; only 2,519 carry an end date). So "the same" has to be defined as rules:

- **Current set.** An entry is current when it is active and its end date, if any, is in the future.
  SAM's current rows for the person must be exactly the current entries.
- **End dates.** When the directory gives an end date, SAM stores it, future ones included. Positions
  keep the legacy end-of-day convention; collaborations are stored as sent. When an entry is inactive
  with no end date, SAM's row must be ended. If SAM already holds an end date, it is kept; otherwise
  the row is ended at the time SAMuel first sees the entry inactive.
- **Employer and start.** These follow the directory, updated in place on the row that holds the entry.
- **History the directory never had.** SAM holds about 11,700 positions with no directory id, mostly
  finished jobs from before the directory existed. They feed the user card's "former" lists,
  statistics and purge permits. Proposed: keep finished history, and end any such row that is still
  open (45 on dev today). Decision D-A in § 7.

## 3. Evidence

Measured 2026-10-15 by the census in Appendix B. It reads the live directory anonymously and three
syncd snapshots of the same morning. It prints counts only.

| | Directory | Daemon's record | Prod SAM (legacy) | Dev SAM (SAMuel shadow) |
|---|---|---|---|---|
| Current positions with a planned end date | **316** | 0 carry it (314 missing, 2 absent) | 181 carry it, 113 missing | **2 carry it, 309 missing** |
| Positions finished in the directory, open here | | 58 | 55 | 57 |
| Positions current in the directory, ended here | | 0 | 113 | 0 |
| Directory positions with no row under their id (all have a twin, § 5.3) | | 753 | 977 | 750 |
| Rows with no directory id (history), still open | | 45 | 81 | 45 |
| Positions whose known organization differs (current) | | 7 | 15 | 14 |
| Collaborations: no row for the institution / current-ness differs | | 2 / 1 | 11 / 1 | 2 / 1 |

How to read it:

- **Planned end dates are the clearest case.**
  - The directory has 316 current positions with an end date, mostly term appointments.
  - The daemon's record has none of them.
  - Prod still shows 181 because legacy's stock daemon rarely delivers position changes at all (§ 10.5).
  - Dev shows 2, because the shadow's reset sent everyone again and SAMuel applied what it was sent.
  - So **a faithful SAM fed by this daemon gets worse, not better**: the soak erased about 180
    correct end dates on dev.
- **Collaborations are nearly right everywhere.** The trouble is concentrated in positions.
- **The 113 positions current in the directory but ended in prod** are the undelivered changes
  (§ 10.5). The shadow fixed them on dev, so the daemon *can* deliver; it just delivers its own
  record, flaws included.
- **Rows with no directory id are expected history.** Only the open ones (45 on dev) contradict the
  directory.

## 4. Why the daemon path cannot get there

The test for whack-a-mole is whether the defects are independent bugs, which can be fixed one by
one and stay fixed, or consequences of a design, which keeps producing new ones. Paths below are in
NCAR/sam-ldap-syncd.

### 4.1 Design: these need a redesign, not a fix

1. **It diffs the directory against its own record, never against SAM.**
   - The daemon seeds its in-memory database from SAM at a reset (`lib/IMDB.pm`). From then on it
     compares directory changes with that record (`lib/IDMSDumpSynchronizer.pm`, around
     lines 109-188).
   - Whatever SAM got wrong before the reset is echoed back as truth.
   - A full pass compares the directory with the daemon's record, not with SAM, so SAM-side drift
     is invisible to it.
2. **It never removes a position or collaboration, on purpose.** `lib/IDMSUserEmployment.pm`
   (around 254-269) says so in a comment: SAM's history is richer than the directory's. So a
   directory removal can never reach SAM.
3. **Its identity model does not fit the directory.**
   - A collaboration has no id of its own; its key is the institution. So moving a collaboration
     to another institution is "delete the old one" (kept, still open, by item 2) plus "add a new
     one" (`transformer_rewrite.pl`, around 498-500).
   - Positions are keyed by the directory id, but the directory holds twins for one job (§ 5.3).
     The daemon's de-duplication compares neighbors only, and can drop the record that carries
     SAM's row id (`lib/SamPositionData.pm`, around 379-527).
4. **It delivers deltas only.** A person whose data diverged before is never corrected until their
   directory entry changes again. That is how a difference like the 55 open-but-finished positions
   in prod can persist indefinitely.

### 4.2 Bugs: fixable, but fixing them leaves 4.1 in place

- **End-date erasure.** Any update carrying `ACTIVE=TRUE` deletes the position's end date
  (`lib/SamUserEmploymentData.pm`, around 388-420). This is the 316-row loss above.
- **Dead read-back.** The daemon never learns the id of a row SAM inserts, so it re-sends that
  record without an id until its next reset (`lib/SamUserData.pm` 292 and 298; bugs 1 and 7; N21).
  About 250 records are in this state at any time.
- **A removed entry becomes a whole-user delete** (N17).
- **Neighbor-only de-duplication** (item 3 above).

### 4.3 The count

Parity through the daemon needs at least six changes across at least five Perl modules owned
upstream:
- the four bugs;
- removal semantics;
- a collaboration identity;
- a comparison against SAM rather than against its own record.

Several of these change behavior legacy relies on. Even then, delta delivery cannot repair a person
the directory does not touch. A reconcile that reads the source and compares it with SAM's actual
rows has none of these failure modes, because it holds no state of its own.

## 5. Options

| Option | For | Against |
|---|---|---|
| (a) Fix the daemon | Keeps one sync path | § 4.3: a redesign of someone else's Perl, slow to land, still delta-only |
| (b) Better matching in SAMuel on the posted lists | Needed anyway; correct for any site with a faithful sender | Bounded by its input: here the input has already lost end dates and removals |
| (c) The daemon reads the whole person from the directory | The right idea | The wrong owner; still couples SAM's correctness to the daemon |
| **(d) (b) as the core, plus an optional directory checkup** | POST API stands alone; UCAR converges; no daemon change needed | One more moving part: a directory read from the pods |

### 5.1 Core: one reconcile engine for the POST path (site-neutral)

`reconcile_affiliations(session, user, targets)` would live in `src/sam/manage/`, beside
`employment.match`. It replaces `_sync_affiliations` (`src/sam/manage/ldapsync.py`, around 268-314)
and makes the user's rows equal the target list:

- **Ids first.** Records that carry a SAM row id claim their rows before records without one are
  matched by data. This fixes the swapped rows seen on 10-15, where an id-less record grabbed an
  existing row and the record that owned it was inserted again.
- **An id inside the user's own rows is trusted, employer included (amended D2).** If the employer
  changed, the row's employer is updated in place. Legacy ignores the employer; SAMuel today inserts
  a duplicate. A row is still matched at most once (D2's other half, kept).
- **Dates applied exactly as sent.** Positions keep end-of-day. Collaborations are stored as sent:
  today `DateRangeMixin` silently turns a midnight end into 23:59:59 (`src/sam/base.py`, around
  53-71), so those collaborations never compare equal and § 10.5's 22 collaboration PUTs never converge.
- **Rows the sender did not list** follow decision D-A: kept as finished history, ended if open.
  "Never end or delete" stops being the rule; the change is recorded as a deviation in § 10.3.
- **Nothing in the core names LDAP or `x-ucar`.** A site whose sender is faithful gets an exact copy
  from the POST alone.

### 5.2 Optional directory checkup (UCAR)

A small module in the ldapsync package (not a plugin), behind a config lever:
`LDAPSYNC_DIRECTORY_URL`, empty means off, and prod stays off until the `SAM_URL` flip.

- **After each user PUT,** once the POST's result is committed, read that person's entries by upid
  (one anonymous search, a few entries), map them to the same target shape, and run the same engine.
  - If the directory is slow or unreachable, the POST's result stands, with one log line.
  - The PUT's response never waits for the checkup and never fails because of it: a bounded
    timeout, and the work runs after the commit.
- **Nightly,** the same code runs for everyone as a scheduled task. A new task must be named in
  `helm/values.yaml` `SAM_TASKS_DISABLED` for prod in the same change, because that lever is
  fail-open. A counts-only census command shares the code and becomes the soak's daily measure of
  this goal.
- **Mapping rules** live in this module only:
  - current-ness and end dates per § 2;
  - twins per § 5.3;
  - an unknown employer is skipped and counted. 2,049 old inactive positions name organizations
    SAM does not have;
  - a person not in SAM is skipped.
- **Privacy:** logs carry counts and row ids, never names.
- **Rollback:** turn the lever off. The core keeps working from the POST.

### 5.3 Twins in the directory

The directory holds 892 jobs twice for the same person and organization: one entry from the old
PeopleDB (`x-ucar-source: PDB`, a numeric id) and one from Workday (`FI`, a `P00…` id).

- In 760 pairs both are active.
- In 120 pairs the PeopleDB copy is active and the Workday copy is not.
- Their start dates differ in 758 pairs.

SAM holds one row per pair today, almost always the Workday one, and a literal copy would show two
current positions. Proposed rule: one SAM row per pair, following the Workday entry (dates and
active flag), keyed by the Workday id. The PeopleDB entry counts only when it has no Workday twin.
Concurrent Workday positions in one organization (104 groups) are separate jobs and stay separate
rows. Decision D-B in § 7.

### 5.4 First run, coexistence, access

- **First run on dev** (from § 3), roughly:
  - restore 309 future end dates;
  - end 57 finished positions and 45 open history rows;
  - correct about 14 employers;
  - add 2 collaborations.

  The census before and after is the proof.
- **Legacy coexistence:** during the shadow only samuel-dev runs this. Prod keeps legacy until the
  flip; legacy would answer "unknown id" for a row SAMuel deleted, which is one more reason to end
  rather than delete.
- **Access:**
  - an anonymous read of ldap.ucar.edu from the nwc1 pods (to verify before building);
  - a new dependency, `ldap3`, which is pure Python.

## 6. Plan if accepted

1. **Core PR:** the reconcile engine, amended D2, end dates as sent, the history policy, and tests
   built from the soak's real cases (moved collaboration, twin positions, planned end dates, id-less
   resends, swapped rows). The § 10.3 table is updated.
2. **Checkup PR:** the directory module, the lever (dev on, prod off), the nightly task (named in
   `SAM_TASKS_DISABLED` for prod), and the census command.
3. **Shadow:** re-baseline (`make refresh-dev`, ldap restarted first), census to zero, then five
   clean weekdays counted again. The daily check gains the census.
4. **Upstream:** an issue for the daemon's end-date erasure. Even with the checkup, a stock daemon
   in prod would keep erasing end dates between nightly passes until fixed.

## 7. Decisions for Ben

- **D-A, history the directory never had.** Recommended: keep finished rows, end the open ones.
  Alternative: delete them, about 11,700 rows of the user card's history.
- **D-B, twins.** Recommended: one row per pair, the Workday entry wins. Alternatives: PeopleDB
  wins, or two rows.
- **D-C, inactive with no end date.** Recommended: keep SAM's end date if it has one, else end at
  the time SAMuel first sees the entry inactive. Alternative: use the entry's `modifyTimestamp`
  (closer to the real date, but it moves with any edit of the entry).
- **D-D, tell upstream now about the end-date erasure.** Recommended: yes, as an issue, separate
  from this work.

## Appendix A. Soak friction for the same round

- **A user-rename command** (`sam-admin`) to replace this week's one-off scripts:
  - the placeholder rename (D26) and a real rename;
  - checks for a taken name and for a placeholder.

  The sync itself keeps refusing renames: three transient numeric usernames arrived on 10-14 alone.
- **Collaboration end dates as sent** (§ 5.1), which settles § 10.5's non-converging PUTs.
- **Shadow tooling:** `compare-dumps` ignores row ids, and the daily check adds the census.

## Appendix B. The census (read-only, counts only)

Run from `containers/ldap-pipeline` with the testbed's snapshots, for example:

```
python3 -I affiliation_census.py daemon=out/<live>/sam-data.json prod=out/<A>/sam-data.json dev=out/<B>/sam-data.json
```

```python
"""Counts-only census: the directory's positions and collaborations against syncd snapshots.

Usage: python3 -I affiliation_census.py LABEL=path/sam-data.json [LABEL=path ...]
Reads ou=allPeople on ldap.ucar.edu anonymously. Prints counts only, never a name or id.
"""
import collections
import datetime
import json
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Denver')
NOW = datetime.datetime.now(TZ)


def directory(objclass, attrs):
    out = subprocess.run(['ldapsearch', '-x', '-LLL', '-o', 'ldif-wrap=no', '-H', 'ldap://ldap.ucar.edu',
                          '-b', 'ou=allPeople,dc=ucar,dc=edu', f'(objectClass={objclass})'] + attrs,
                         capture_output=True, text=True, check=True).stdout
    return [d for d in (dict(re.findall(r'^([\w-]+): (.*)$', e, re.M)) for e in out.split('\n\n')) if d]


def ldap_time(v):
    if not v:
        return None
    v = v.rstrip('Z')
    return datetime.datetime.strptime(v[:14] + (v[14:] or '+0000'), '%Y%m%d%H%M%S%z')


def ms_time(v):
    return None if v is None else datetime.datetime.fromtimestamp(int(v) / 1000, TZ)


def is_open(end):
    return end is None or end > NOW


def census(pos, col, users, orgs):
    by_upid = {str(r.get('upid')): r for r in users.values()}
    dir_ids = collections.defaultdict(set)
    for p in pos:
        dir_ids[p.get('x-ucar-upid')].add(p.get('x-ucar-positionId'))
    c = collections.Counter()
    for p in pos:
        u = by_upid.get(p.get('x-ucar-upid'))
        if u is None:
            c['P0 person not in SAM'] += 1
            continue
        rows = [e for e in (u.get('positions') or []) if e.get('idmsUniqueName') == p.get('x-ucar-positionId')]
        end = ldap_time(p.get('x-ucar-endDate'))
        active = p.get('x-ucar-active') == 'TRUE'
        if active and end is not None and end > NOW:
            c['P1 future end: ' + ('absent' if not rows else 'missing' if rows[0].get('endDate') is None
                                   else 'carried')] += 1
        if not rows:
            twins = [q for q in dir_ids[p.get('x-ucar-upid')] if q != p.get('x-ucar-positionId')]
            held = {e.get('idmsUniqueName') for e in (u.get('positions') or [])}
            c['P3 absent by id'] += 1
            c['P3   of which a twin is held'] += any(t in held for t in twins)
            continue
        r = rows[0]
        if not (active and is_open(end)) and is_open(ms_time(r.get('endDate'))):
            c['P2 finished in directory, open here'] += 1
        if active and is_open(end) and not is_open(ms_time(r.get('endDate'))):
            c['P2b current in directory, ended here'] += 1
        if str(r.get('organizationId')) != p.get('x-ucar-organizationId') and p.get('x-ucar-organizationId') in orgs:
            c['P5 known organization differs' + (' (active)' if active else '')] += 1
        if end is not None and end <= NOW and r.get('endDate') is not None \
                and ms_time(r['endDate']).date() != end.astimezone(TZ).date():
            c['P6 past end, different day'] += 1
    for u in users.values():
        mine = dir_ids.get(str(u.get('upid')), set())
        for e in (u.get('positions') or []):
            if e.get('idmsUniqueName') in mine:
                continue
            key = 'P4 rows the directory lacks: ' + ('no directory id' if not e.get('idmsUniqueName') else 'id not in directory')
            c[key] += 1
            c[key + ', open'] += is_open(ms_time(e.get('endDate')))
    for p in col:
        u = by_upid.get(p.get('x-ucar-upid'))
        if u is None:
            continue
        rows = [e for e in (u.get('collaborations') or []) if str(e.get('institutionId')) == p.get('x-ucar-externalOrgId')]
        if not rows:
            c['C1 no row for the institution'] += 1
            continue
        end = ldap_time(p.get('x-ucar-endDate'))
        current = p.get('x-ucar-active') == 'TRUE' and is_open(end)
        if current != any(is_open(ms_time(e.get('endDate'))) for e in rows):
            c['C2 current-ness differs'] += 1
        if end is not None and all(e.get('endDate') is None for e in rows):
            c['C3 directory end, none here'] += 1
    return c


def main():
    pos = directory('x-ucar-position', ['x-ucar-upid', 'x-ucar-positionId', 'x-ucar-organizationId',
                                        'x-ucar-endDate', 'x-ucar-active'])
    col = directory('x-ucar-collaboration', ['x-ucar-upid', 'x-ucar-externalOrgId', 'x-ucar-endDate',
                                             'x-ucar-active'])
    act = [p for p in pos if p.get('x-ucar-active') == 'TRUE']
    print(f'{NOW:%Y-%m-%d %H:%M} directory: positions {len(pos)} (active {len(act)}, active with a future end '
          f'{sum(1 for p in act if (ldap_time(p.get("x-ucar-endDate")) or NOW) > NOW)}), collaborations '
          f'{len(col)} (active {sum(1 for p in col if p.get("x-ucar-active") == "TRUE")})')
    for arg in sys.argv[1:]:
        label, path = arg.split('=', 1)
        snap = json.load(open(path))[1]
        users = snap['user']
        rows = sum(len(u.get('positions') or []) for u in users.values())
        print(f'== {label}: users {len(users)}, positions {rows}, collaborations '
              f'{sum(len(u.get("collaborations") or []) for u in users.values())}')
        for k, v in sorted(census(pos, col, users, set(snap.get('organization', {}))).items()):
            print(f'   {k}: {v}')


if __name__ == '__main__':
    main()
```

The twin figures in § 5.3 come from grouping `x-ucar-position` entries by person and organization
and counting the PDB/FI pairs, their `x-ucar-active` values and start days, in the same counts-only way.
