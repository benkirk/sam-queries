# Reconciling SAM with the directory

**Status:** direction agreed as the end goal (2026-10-15). The sync daemon stays as is for now. The
first step is a read-only discrepancy report, built as the directory client's step 1
(`DIRECTORY_CLIENT.md`, PR #841); stage 2 is its step 2.
**Scope:** directory → SAM: people, then their positions (`user_organization`) and collaborations
(`user_institution`), then organizations, institutions and adhoc groups. SAM → directory (project
groups, collaborator end dates) stays with the daemon.
**Related:** `LDAP_SYNC_API.md` (§ 10.3 deviations D1-D4, § 10.5 residuals; § 10.7 soak, in PR #777),
`LDAPSYNC_DEV_SHADOW.md`, `SAM_LDAP_SYNCD_REFERENCE.md` (daemon bugs N17, N21, N24-N26, 1, 7; staging
§ 3.6-3.8).

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
| Daemon | Writes SAM → directory: project groups and collaborator end dates, through staging | Project groups work; the collaborator end-date pass has never run (§ 3.4) |
| SAMuel | On a notice, reads that record from the directory and reconciles SAM to it | New. The POST's content is still applied first, so a site without our directory loses nothing |
| SAMuel | Nightly, sweeps the whole directory the same way, to catch missed notices | New. Missed notices are real: the testbed's poller missed one claim this week, and the daemon has its own gaps |
| SAMuel | Verifies any delete or purge against the directory before acting | New. The daemon turns a removed affiliation entry into a whole-user delete (N17) |

Notes:

- **One record type at a time.** Affiliations go first, because that's where the damage is proven.
  User identity fields (names, emails, phones, active, academic status) follow, then organizations,
  institutions and adhoc groups. Each type goes through the stages in § 4.
- **For UCAR, SAMuel would ignore much of what the daemon sends.** That is deliberate. It is written
  here so nobody later fixes the daemon's content expecting it to matter.
- **Access.** An anonymous read of `ou=allPeople` sees people, positions and collaborations.
  - Service accounts appear only under `ou=serviceAccounts`, also readable anonymously. Nothing
    stage 1 needs is hidden from an anonymous read, so no credential is requested until a hidden
    field is named.
  - A full sweep is about 60,000 entries, a minute or less. One person is a single query.
  - The pods' route to ldap.ucar.edu is still to be verified.
  - What each subtree holds, attribute by attribute, and how it maps to SAM: Appendix C.

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
  changes at all (`LDAP_SYNC_API.md` § 10.5). The shadow delivered them, and SAMuel applied the erasure. So a faithful
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
   - The directory lists many jobs twice (§ 4); the daemon's de-duplication is a bug, § 3.3.
4. **It delivers changes only.** Anything that diverged earlier stays wrong until the person's entry
   changes again.

### 3.3 Bugs, fixable but not sufficient

- **End-date erasure (N24):** every active position or collaboration loses its end date, on any
  modify and on every full dump.
- **Dead read-back:** the daemon never learns SAM's id for a row SAM inserts (`lib/SamUserData.pm`
  292, 298; bugs 1 and 7; N21).
- **N17:** a removed entry becomes a whole-user delete.
- **Neighbor-only de-duplication:** twins are compared with their neighbors only
  (`lib/SamPositionData.pm` around 379-527).

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
  For those, the outbound path is meant to be the source: while a person is a member of an active
  project, SAM will keep their collaboration end date about 90 days ahead, derived from project
  membership and allocation dates, and the daemon will write it to the collaboration's own
  `x-ucar-endDate` (`lib/StagingTransformers.pm:71`), the attribute the directory uses too.
  **That keep-alive has never run** (no `collabexpiryupdates` GET in 57 days of logs; the cron child
  dies, `LDAP_SYNC_API.md` § 2.4). Every collaboration today was last written by the bulk load or by
  Fischer, so the 984 end dates on CISL/USS collaborations are PeopleDB's, not SAM's. Until the pass
  runs, a date there is the directory's: reported, never applied. Once it runs, the entry's
  `modifiersName` (the daemon's bind DN) tells a SAM-written date from the directory's; that is
  stage 2's hook. SAM stores no sponsor column and gets none.
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
   - Twins are reported, not added, until D-B is decided. 892 jobs are listed twice, once from the old PeopleDB (`PDB`) and
     once from Workday (`FI`); 758 pairs disagree on the start day and 130 on active (2026-10-15).
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
  (`src/sam/base.py` around 53-71), so 22 collaboration PUTs never converge (`LDAP_SYNC_API.md` § 10.5).
- **A guard against end-date erasure (D-E):** a POST may not clear a planned end date SAM already
  holds, until upstream fixes the erasure.
  - This deliberately departs from a faithful POST, so it would be recorded in `LDAP_SYNC_API.md` § 10.3.
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
  (§ 3.4: 80% of positions, 89% of collaborations). Recommended: SAM's own end date; else report it
  and write nothing. Not the entry's `modifyTimestamp`: on every undated inactive position (8,816)
  and all but 8 of 13,384 collaborations it is the 2026-07-10 bulk load. Not "the time SAMuel first
  sees it" either. Both are arbitrary dates. (A first-seen stamp would touch only rows SAM holds open
  that the directory calls finished, 58 positions and 1 collaboration in the census.)
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

The twin figures in § 4 come from grouping `x-ucar-position` entries by person and organization
and counting the PDB/FI pairs, their `x-ucar-active` values and start days, in the same counts-only way.

## Appendix C. The directory as it is (inventory, 2026-10-15)

From an anonymous read of every subtree, counts and attribute presence only. Numbers are the
day's; the shapes are what the plan relies on.

### C.1 Subtrees

| Subtree | Class | Entries (active) | Key | Carries | SAM counterpart |
|---|---|---|---|---|---|
| `ou=allPeople` | `x-ucar-uniquePerson` | 28,605 (6,442) | `x-ucar-upid` | uid, uidNumber, cn/sn/givenName, preferredName, middleName, nameSuffix, one `mail`, one `forwardEmail`, active, source, academicStatus (53%), typedPhone (47%), authType (17%), adminFor (6%), githubUser, labeledURI; `o`/`ou`/`title` on ~4% only | `users` 28,683 |
| └ position | `x-ucar-position` | 13,717 (2,748) | `x-ucar-positionId`, child of the person | organizationId (85%), positionNum, primary, position-type, positionTitle (73%), supervisor (72%), startDate (100%), endDate (18%), active, source | `user_organization` 24,957 |
| └ collaboration | `x-ucar-collaboration` | 19,614 (4,512) | `x-ucar-externalOrgId`, child of the person; no id of its own | adminOrg (100%), affiliationOrg (83%), `l` (94%), startDate (98%), endDate (9%), title (2%), active, source | `user_institution` 21,860 |
| `ou=allOrganizations` | `x-ucar-extendedOrganization` | 401 (171) | `x-ucar-organizationId`, SAM's `organization_id` | ou (acronym), o (name), parent DN, orgLevel and orgLevelCode (96%), leadPerson and authoritativePerson (73%), active | `organization` 402 (171) |
| `ou=externalOrgs` | `x-ucar-externalOrganization` | 1,412 | `x-ucar-externalOrgId`, SAM's `institution_id` | o, shortOrgName, c (100%), st (99%), stateProvCode and stateProvCategory, `l` (75%), postalCode (46%), street (20%), nsfOrgCode (330), externalOrgType (93%, four values) | `institution` 1,410 |
| `ou=allGroups` | `x-ucar-uniqueGroup` | 12,181 (5,413) | `gidNumber` | cn, groupName, tags (38 values), isOrg, isAlias, mail, uniqueMember (upid, group, org and service-account DNs), subMember, memberPolicy (17), adminFor (16) | `project.unix_gid` (5,920) and `adhoc_group` (310) |
| `ou=serviceAccounts` | `x-ucar-serviceAccount` | 74 (70) | `uid`, no upid | uidNumber, forwardEmail, serviceAccountType, contactPerson (4 of 74), description | 112 role logins, matched by username |
| `ou=usernames` | `x-ucar-username` | 40,156 (11,574) | `cn` | type (user 28,620, alias 10,226, mailmanlist, mailbox), owner DN, start, end, uidNumber | none; only 2 people ever held a second name |
| `ou=unix` | `x-ucar-unixAccount`, `x-ucar-unixGroup` | 6,510 / 3,560 | `uid` / `gidNumber` | a generated view: every account `/bin/tcsh`, `localhost`, gid 1000 or 100; 102 people have a second account under an old username with its own uidNumber (§ 6) | none; `users.unix_uid` follows the person entry |
| `ou=tags`, `ou=gidAllocations` | `x-ucar-groupTag`, `x-ucar-gidAllocation` | 41 / 1 | | tag names (5 are access branches), one gid block | `adhoc_group_tag`, `gid_allocation` |

`ou=people`, `ou=organizations` and `ou=groups` are active-only or derived views (member lists,
head counts) and carry nothing the full subtrees lack. `x-ucar-peid` and `eduPersonOrcid` are in
the schema and on no entry. Every entry carries `modifyTimestamp`; `createTimestamp` is the
2026-07-10 load on nearly all of them, so neither says since when.

### C.2 Field map, directory to SAM

| Record | Directory attribute | SAM column | Note |
|---|---|---|---|
| person | `x-ucar-upid` | `users.upid` | the key |
| | `uid`, `uidNumber` | `username`, `unix_uid` | equal on all 28,546 shared upids; the person entry, never `ou=unix` |
| | `givenName`, `sn`, `x-ucar-preferredName`, `x-ucar-middleName`, `x-ucar-nameSuffix` | `first_name`, `last_name`, `nickname`, `middle_name`, `name_suffix` | |
| | `mail`, `x-ucar-forwardEmail` | `email_address` rows, primary and not | one of each per person; SAM holds 53,811 rows for 28,683 users, the rest is history the directory lacks |
| | `x-ucar-typedPhone` ("Label: number") | `phone` + `phone_type` | the six labels equal SAM's six types |
| | `x-ucar-academicStatus` | `academic_status_id` | PDB people carry the code, Workday people the description on ~870 entries; one label ("University Research Staff", 6 people) matches no SAM row |
| | `x-ucar-authType` | `token_type` | |
| | `x-ucar-active` | `active` | the person flag wins over an affiliation's (§ 3.4) |
| position | `x-ucar-positionId` | `user_organization.idms_unique_name` | numeric PDB ids (12,023) and `P` + 17 or 23 digits from Workday (1,475); 12,207 SAM rows carry none |
| | `x-ucar-organizationId` | `organization_id` | NOT NULL in SAM; 2,050 entries have none, 6 current |
| | `x-ucar-startDate`, `x-ucar-endDate` | `start_date`, `end_date` | end compared by day: the directory has 23:59:59, midnight and 17:00 ends, SAM stores 23:59:59 |
| collaboration | `x-ucar-externalOrgId` | `user_institution.institution_id` | with the person, the whole key; SAM's row id has no directory twin |
| | `x-ucar-startDate`, `x-ucar-endDate` | `start_date`, `end_date` | 368 current entries have no start; ends carry the time of day they were set and are stored as sent |
| organization | `x-ucar-organizationId` | `organization_id` | same number both sides; `organization.idms_unique_name` is NULL on every row |
| | `ou`, `o` | `acronym`, `name` | |
| | `x-ucar-orgLevel`, `x-ucar-orgLevelCode` | `level`, `level_code` | 16 without a level, both sides |
| | `x-ucar-parent` (a DN) | `parent_org_id` | 184 entries have a parent under `ou=externalOrgs` (183 inactive, plus UCAR itself); SAM's parent is NULL for them |
| institution | `x-ucar-externalOrgId` | `institution_id` | |
| | `x-ucar-shortOrgName`, `o` | `acronym`, `name` | |
| | `c` + `st` (or `x-ucar-stateProvCode`) | `state_prov_id` | SAM has no country column; country rides on the state row, so a state equal to the country has no SAM home (35 rows) |
| | `x-ucar-externalOrgType` | `institution_type_id` | the four values are SAM's four rows |
| | `x-ucar-nsfOrgCode`, `street`, `l`, `postalCode` | `nsf_org_code`, `address`, `city`, `zip` | 330 NSF codes both sides |
| group | `gidNumber`, `cn` | `project.unix_gid` or `adhoc_group.unix_gid`, `group_name` | match by gid: 1,066 projcodes are not four letters and four digits |
| | `x-ucar-tag` | `adhoc_group_tag` | SAM keeps only the access-branch tags (17 of 38) |
| | `uniqueMember` | derived for projects; `adhoc_system_account_entry` | project membership flows SAM to directory |

### C.3 Directory-only fields

No SAM column, and none needed by the reconcile. Listed so nobody adds one by reflex:

- person: `x-ucar-authType` beyond its token mirror, `x-ucar-githubUser`, `x-ucar-adminFor`,
  `labeledURI`, office;
- position: type, title, `x-ucar-supervisor`, `x-ucar-primary`, `x-ucar-positionNum`;
- collaboration: `x-ucar-adminOrg` (the sponsoring NCAR org), `x-ucar-affiliationOrg`, `l`, `title`;
- organization: `x-ucar-leadPerson`, `x-ucar-authoritativePerson`, `x-ucar-adminFor`;
- institution: `x-ucar-stateProvCategory` (44 kinds);
- group: `x-ucar-memberPolicy`, `x-ucar-isOrg`, `x-ucar-isAlias`, `mail`, `x-ucar-subMember`.

### C.4 SAM-only columns

The directory can never source these, so the reconcile never writes them: `users.locked`,
`charging_exempt`, `deleted`, `deactivate`, `primary_gid`, `title` (for people), `contact_person_upid`
(the directory has `x-ucar-contactPerson` on 4 of 74 service accounts; SAM has a contact on its
role logins), `login_type_id`, `user_alias`, `institution.code`, `organization.description`.

### C.5 Population facts

- 409 active people hold no current position and no current collaboration; 25 hold both.
- 847 active people hold more than one current position: the 892 PDB/FI twin pairs, of which
  130 disagree on active and 758 on the start day.
- 173 inactive people hold an active position and 49 an active collaboration; 50 positions are
  active with a past end date.
- 25 SAM users are absent from the directory and 59 directory people from SAM, on a snapshot a
  few days old.
- Service accounts: 70 active in the directory, 73 active role logins in SAM; the difference
  is SAM's underscore ghosts and one directory account SAM lacks.

## Appendix D. Directory reads beyond the reconcile (scoped 2026-10-15)

The adapter stage 1 needs reads whole entries. These are the other consumers kept after a
wider list was considered, and the shape that serves them all.

- **Per-user view** (`sam-search user --directory`, and a panel on the user page): the current
  positions (organization, type, start, planned end), the sponsor org on each collaboration
  (`x-ucar-adminOrg`), and drift: the directory's active, uid, username, email and affiliations
  against SAM's rows, differences only. This view is the stage-1 report for one person, the way
  the host-provisioning check (`src/sam/provisioning.py`) is SAM against the host for one user.
- **Per-group view** (project and adhoc): directory members against SAM's derived list, the tag
  set against the allocations, and the group manager from `x-ucar-adminFor`, which SAM has no
  column for.
- **Wired, not shown:** supervisor, auth type, GitHub user, office and the rest of the person
  attributes. The adapter returns the entry; each display picks.
- **Out of scope, decided:** watching the upstream feeds through `modifiersName`, pre-flight
  checks before SAM writes, a duplicate lookup on `/register`, service-account audits.
- **Shape.** A `DirectoryReader` protocol with the card contract `TicketProvider` uses
  (`src/sam/integration/tickets/base.py`: `from_environment`, `configured`, `summary`, `check`)
  plus one `search`; a null reader when the directory URL is empty; one `UCARDirectory` adapter
  that owns the schema (attribute names, DN shapes, the label-to-code map) and returns typed
  records, so no consumer sees an attribute name. The check after each incoming sync PUT is the
  first consumer and the CLI flag the last (`DIRECTORY_CLIENT.md`); the nightly sweep is the
  same comparison in a loop. Anonymous bind until a hidden field is named; names and emails are never cached, counts are.
