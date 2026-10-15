# Reconciling SAM with the directory

**Status:** direction agreed as the end goal (2026-10-15). The sync daemon stays as is for now. The
first step is a read-only discrepancy report. No code yet.
**Scope:** directory → SAM: people, then their positions (`user_organization`) and collaborations
(`user_institution`), then organizations, institutions and adhoc groups. SAM → directory (project
groups, collaborator end dates) stays with the daemon.
**Related:** `LDAP_SYNC_API.md` (§ 10.3 deviations D1-D4, § 10.5 residuals, § 10.7 soak),
`LDAPSYNC_DEV_SHADOW.md`, `SAM_LDAP_SYNCD_REFERENCE.md` (daemon bugs N17, N21, 1, 7; staging § 3.6-3.8).

## 1. Summary

- **End goal.** The daemon keeps two jobs: noticing directory changes, and writing back upstream.
  SAMuel takes over the rest: when told "something changed for person X", it reads X from the
  directory itself and makes SAM agree, and a nightly sweep catches anything a notice missed.
  SAMuel's POST API stays faithful and self-sufficient, so a site without our directory can still
  run SAMuel from it alone.
- **Why.** The daemon's view of a person is not the directory's, and no rule inside SAMuel can
  recover what the daemon has already dropped.
  - The plainest case is planned end dates. The directory has 316 current positions with a planned
    end. The daemon's record carries none of them.
  - Our dev shadow lost about 180 that dev still had at the baseline, because SAMuel applied what
    the daemon sent.
  - Fixing this inside the daemon is a redesign, not a bug fix (§ 3).
- **Not yet.** This is a ways off; the synchronizer stays as it is for now.
- **What is still open.** After three months of the new directory, SAM's own history may be the more
  complete record. So nothing here decides which side is right. The first step only **reports
  discrepancies**, and changes nothing. Applying fixes comes in stages that each need a decision (§ 4).

## 2. The end goal

| Who | Does | Today |
|---|---|---|
| Daemon | Notices directory changes and tells SAMuel which person, group, organization or institution changed | It already does: every user PUT is such a notice, so no daemon change is needed to start |
| Daemon | Writes SAM → directory: project groups and collaborator end dates, through staging | Unchanged |
| SAMuel | On a notice, reads that record from the directory and reconciles SAM to it | New. The POST's content is still applied first, so a site without our directory loses nothing |
| SAMuel | Nightly, sweeps the whole directory the same way, to catch missed notices | New. Missed notices are real: the testbed's poller missed one claim this week, and the daemon has its own gaps |
| SAMuel | Verifies any delete or purge against the directory before acting | New. The daemon turns a removed affiliation entry into a whole-user delete (N17) |

Notes:

- **One record type at a time.** Affiliations go first, because that's where the damage is proven.
  User identity fields (names, emails, phones, active, academic status) follow, then organizations,
  institutions and posix groups. Each type goes through the stages in § 4.
- **For UCAR, SAMuel would ignore much of what the daemon sends.** That is deliberate. It is written
  here so nobody later fixes the daemon's content expecting it to matter.
- **Access.** An anonymous read of `ou=allPeople` sees people, positions and collaborations.
  - Service accounts appear only under `ou=serviceAccounts`, also readable anonymously. Nothing
    stage 1 needs is hidden from an anonymous read, so no credential is requested until a hidden
    field is named.
  - A full sweep is about 60,000 entries, a minute or less. One person is a single query.
  - The pods' route to ldap.ucar.edu is still to be verified.

## 3. Why the daemon's content cannot converge

### 3.1 Evidence

Measured 2026-10-15 by the census in Appendix B: live directory, anonymous, counts only, against three
syncd snapshots of that morning. The directory holds 13,717 positions (2,748 active) and 19,614
collaborations (4,512 active).

| | Directory | Daemon's record | Prod SAM (legacy) | Dev SAM (SAMuel shadow) |
|---|---|---|---|---|
| Current positions with a planned end date | **316** | 0 carry it (314 missing, 2 absent) | 181 carry it, 113 missing | **2 carry it, 309 missing** |
| Positions finished in the directory, open here | | 58 | 55 | 57 |
| Positions current in the directory, ended here | | 0 | 113 | 0 |
| Directory positions with no row under their id (each has a twin that is held, § 4) | | 753 | 977 | 750 |
| Rows with no directory id (history) that are still open | | 45 | 81 | 45 |
| Current positions whose known organization differs | | 7 | 15 | 14 |
| Collaborations: no row for the institution / current-ness differs | | 2 / 1 | 11 / 1 | 2 / 1 |

How to read the table:

- **Planned end dates.** Prod keeps 181 only because legacy's stock daemon rarely delivers position
  changes at all (§ 10.5). The shadow delivered them, and SAMuel applied the erasure. So a faithful
  SAM fed by this daemon gets worse.
- **Collaborations are nearly right everywhere.** The trouble is in positions.
- **The 113 positions current in the directory but ended in prod** are undelivered changes. The
  shadow fixed them on dev, so the daemon can deliver; it delivers its own record, flaws included.

### 3.2 Design, not bugs (paths in NCAR/sam-ldap-syncd)

1. **It compares the directory with its own record, seeded from SAM, never with SAM.** See
   `lib/IMDB.pm` and `lib/IDMSDumpSynchronizer.pm` around 109-188. SAM's past errors come back as
   truth, and a full pass cannot see drift on SAM's side.
2. **It never removes a position or collaboration, by design.** See `lib/IDMSUserEmployment.pm`
   around 254-269.
3. **Its identity model does not fit the directory.**
   - A collaboration has no id of its own, so moving it to another institution keeps the old one
     open and adds a new one (`transformer_rewrite.pl` around 498-500).
   - The directory lists many jobs twice, and the daemon's de-duplication compares neighbors only
     (`lib/SamPositionData.pm` around 379-527).
4. **It delivers changes only.** Anything that diverged earlier stays wrong until the person's entry
   changes again.

### 3.3 Bugs, fixable but not sufficient

- **End-date erasure:** an update carrying `ACTIVE=TRUE` deletes the end date
  (`lib/SamUserEmploymentData.pm` around 388-420).
- **Dead read-back:** the daemon never learns SAM's id for a row SAM inserts (`lib/SamUserData.pm`
  292, 298; bugs 1 and 7; N21).
- **N17:** a removed entry becomes a whole-user delete.
- **Neighbor-only de-duplication.**

Parity through the daemon needs at least six changes across five Perl modules owned upstream, several
of which alter behavior legacy relies on. Even then, change-only delivery cannot repair a person the
directory does not touch. A reconcile that reads the source and compares it with SAM's actual rows
holds no state of its own, so drift from any cause is corrected the next time that person is checked.

### 3.4 What the directory's records are like (review of 2026-10-15)

Counts from the live directory, anonymous (`ou=allPeople` is 28,605 people, 13,717 positions and
19,614 collaborations). They change the plan's rules from edge cases to the normal case:

- **Most finished records have no end date.** 8,816 of 10,969 inactive positions (80%) and 13,384
  of 15,102 inactive collaborations (89%) carry none, where SAM has a date on most. So "inactive, no
  end date" (D-C) is the usual shape, and a reconcile must never invent a date for it (§ 7).
- **The directory disagrees with itself.** 173 inactive people hold an active position. Rule: **the
  person's flag wins**, since account access keys on it; the position is reported, not applied.
- **A collaboration is one entry per person per institution, ever.** Its name is
  `x-ucar-externalOrgId=<inst>,x-ucar-upid=<person>`, so a second stint cannot be recorded. SAM holds
  178 pairs with repeat stints. Rule: the entry maps to the **latest** SAM row for that pair; earlier
  stints are SAM history and are never touched.
- **368 active collaborations have no start date**, and SAM requires one. Rule: keep SAM's start;
  where SAM has no row, report it rather than create one with an invented date.
- **Collaborations SAM sponsors are SAM's truth, derived (Ben, 2026-10-15).** Every collaboration
  names its sponsoring NCAR org: 77% `adminOrg` CISL or USS, which legacy SAM created for HPC users.
  For those, the outbound path is the source: while a person is a member of an active project, SAM
  keeps their collaboration end date about 90 days ahead, derived from project membership and
  allocation dates, and writes it to the directory in a field of its own. The reconcile may read
  that field back, but as SAM's own output, never as new information; the directory's own dates on a
  SAM-sponsored collaboration are reported, not applied. SAM stores no sponsor column and gets none.
- **Fields SAM does not keep.** Positions also carry type, title, supervisor, primary and number;
  none map to SAM and none are needed. 2,050 positions name no organization, 6 of them current.

## 4. Which side is right: three trust stages

The open question, as Ben put it (paraphrased): three months into the new system, we don't fully trust the
directory yet, and where SAM has history the directory doesn't, SAM may have the more complete record.
One answer later is an **epoch**: keep SAM's organization and institution data from before a chosen
date, and mirror the directory after it. Not today. The stages let us build that trust from evidence,
without deciding it up front:

1. **Report only.** SAMuel computes what it would change, by category, and changes nothing. A few
   weeks of reports show where SAM and the directory disagree, in which direction, and since when.
   That is the evidence for or against the epoch.
2. **Apply only where the directory is unambiguously newer.** This covers rows tied to a directory
   entry: planned end dates, finished versus open, employer, and new entries.
   - SAM's own history (rows with no directory id) is never touched.
   - Twins are reported, not added. 892 jobs are listed twice, once from the old PeopleDB (`PDB`) and
     once from Workday (`FI`); they often disagree on start date, and 120 pairs disagree on active.
3. **The epoch.** Before it, SAM's rows are frozen as SAM's record. After it, SAM mirrors the
   directory exactly. This is the same reconcile with one more rule.

Each record type moves through the stages separately. User identity fields may reach stage 2 sooner
than affiliations, because legacy already treats the directory as the truth for them.

## 5. Near term, while the synchronizer stays

**Stays as is:** the daemon, the POST path, and "never end or delete" for affiliations.

**Safe to fix in the core regardless, each a decision (§ 7):**
- **Amended D2.** Records with an id claim their own rows before records without one; an id inside
  the user's own rows is trusted, employer included, and the employer is updated in place. Today an
  id-less record can grab another row, and a moved collaboration is inserted again.
- **Collaboration end dates stored as sent.** Today a midnight end is silently stored as 23:59:59
  (`src/sam/base.py` around 53-71), so 22 collaboration PUTs never converge (§ 10.5).
- **A guard against end-date erasure (D-E):** a POST may not clear a planned end date SAM already
  holds, until upstream fixes the erasure.
  - This deliberately departs from a faithful POST, so it would be recorded in § 10.3.
  - It protects prod after the `SAM_URL` flip: from then on, prod would take the shadow's erasures.

**Upstream:** an issue for the end-date erasure (D-D).

## 6. Stage 1: the discrepancy report

- **What it is.** Read-only: no writes, so it can land without restarting the soak's clean-day count.
- **What it reports:** counts by category, plus row ids for follow-up, never names in logs.
  - **People:**
    - in the directory but not in SAM, and the reverse;
    - username, upid or uid that disagree;
    - the active flag;
    - later, names and emails.
  - **Affiliations:** the census categories in § 3.1.
    - planned end dates;
    - finished versus open;
    - employer;
    - directory entries with no SAM row;
    - twins;
    - SAM history rows still open.
- **Academic status.** PDB people carry a code (`GS`, `PD`, `F`); Workday (`FI`) people mostly carry
  a label ("Graduate Student", "University Research Staff (excluding postdoctorates)"). The daemon
  translates, and SAMuel's POST path stores NULL for a value it does not know. The reconcile keeps
  its own label-to-code map in one place, and the report has a category for an unmapped label.
- **Never touched by the reconcile:** the SAM-only columns `locked`, charging exempt, `deleted`,
  deactivate, primary gid, title, contact person and login type.
- **The uid source is the person entry** (`uidNumber` under the upid), never a lookup by username,
  and never `ou=unix`. Person `uidNumber` equals SAM `unix_uid` on all 28,546 shared upids.
  `ou=unix` is keyed by username and carries 102 people's old accounts, each with its own
  `uidNumber`: joined by upid it disagrees with the person entry on 151 people (245 accounts),
  joined by username on 1, and 73 of its accounts match no person.
- **How to run it:**
  - a `sam-admin` command;
  - an optional scheduled task that records the counts. A new task is named in `helm/values.yaml`
    `SAM_TASKS_DISABLED` for prod in the same change, because that setting is fail-open.
- **Configuration:** the directory URL, empty meaning off.
- **The soak** uses it as its daily measure, alongside `compare-dumps`.

## 7. Decisions for Ben

- **D-A, history the directory never had.** Deferred to the epoch. Until then it is never touched.
- **D-B, twins (for stage 2).** Recommended: one SAM row per pair, following the Workday entry.
  Alternatives: PeopleDB wins, or two rows.
- **D-C, an entry that is inactive with no end date (for stage 2).** The normal case, not an edge
  (§ 3.4: 80% of positions, 89% of collaborations). Recommended, in order: SAM's own end date; else
  the entry's `modifyTimestamp`; else report it and write nothing. Never "the time SAMuel first sees
  it": on first run that stamps today on some 22,000 rows.
- **D-D, an upstream issue now for the end-date erasure.** Recommended: yes.
- **D-E, a near-term guard so a POST cannot clear a planned end date.** Recommended: yes, as a
  recorded deviation until upstream fixes the erasure.
- **D-F, a read-only directory credential.** Dropped: an anonymous read covers stage 1 (§ 2). Ask
  only when a hidden field is named.

## Appendix A. Soak friction for the same round

- **A user-rename command** (`sam-admin`) to replace this week's one-off scripts:
  - the placeholder rename (D26) and a real rename;
  - checks for a taken name and for a placeholder.

  The sync itself keeps refusing renames: three transient numeric usernames arrived on 10-14 alone.
- **Collaboration end dates as sent** (§ 5).
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
