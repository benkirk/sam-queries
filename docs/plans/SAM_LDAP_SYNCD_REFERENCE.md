# sam-ldap-syncd — reference overview

**Status:** reference, 2026-09-25. Read-only survey of George's repos as cloned in
`legacy_sam/container_zoo/`; nothing here was run. Bugs in §4 are code-verified but
not reproduced in production unless a row says so.
**Companion:** `docs/plans/LDAP_SYNC_API.md` scopes serving this daemon from the new
SAM and holds the exact HTTP contract (§2 there); this document explains what the
daemon *is* and how it works, for a reader who knows SAM well and LDAP little.

Paths are relative to `legacy_sam/container_zoo/` unless they start with `java:`,
which means `legacy_sam/src/main/java/edu/ucar/cisl/sam/`.

---

## 0. How to read this

"The daemon" is `sam-ldap-syncd`, a Perl 5 program. It never talks to an LDAP
server directly on the input side. Two sibling containers do that work and hand it
plain files:

1. **sam-idms-ldap** keeps a live copy of the central UCAR directory.
2. **sam-ldap-transformer** turns that copy, and each change to it, into JSON lines.
3. **sam-ldap-syncd** reads the JSON lines, keeps its own in-memory picture of what
   SAM should contain, and pushes the differences to SAM over HTTP. It also reads two
   things back out of SAM (project groups and collaborator expiry dates) and writes
   them into an LDAP *staging* server, which is how SAM influences the directory.

§1 is the LDAP primer. §2 walks the pipeline hop by hop. §3 is the daemon feature by
feature. §4 is the bug list, with ready-to-file issue text in Appendix C.

---

## 1. LDAP in ten minutes, for SAM people

### 1.1 The vocabulary, mapped to database words

| LDAP word | Nearest SAM idea | Notes |
|---|---|---|
| **entry** | a row | One record. |
| **attribute** | a column | Many attributes are **multi-valued**: a group entry has one `uniqueMember` line per member, a group has several `x-ucar-tag` lines. Think "column that holds a list". |
| **DN** (distinguished name) | primary key + path | The entry's unique address, read **right to left** like a file path. `x-ucar-positionId=9001,x-ucar-upid=4242,ou=allPeople,dc=ucar,dc=edu` means: root `dc=ucar,dc=edu`, folder `ou=allPeople`, person `x-ucar-upid=4242`, and under that person a child entry `x-ucar-positionId=9001`. |
| **RDN** | the row's own key | The leftmost piece of the DN, the entry's name inside its parent. |
| **base DN** | the schema/database name | `dc=ucar,dc=edu` for everything here. |
| **ou=** | a folder / table | "organizational unit". The pipeline reads seven of them (§1.2). |
| **objectClass** | table definition | Says which attributes an entry MUST and MAY have. An entry can carry several. |
| **schema** | DDL | `ucarldap/schema/ucar.schema` defines every `x-ucar-*` attribute and class. |
| **operational attributes** | system columns | Maintained by the server (`entryUUID`, `entryCSN`, `modifyTimestamp`, …). The pipeline strips all but `modifyTimestamp`. |

### 1.2 The part of the UCAR tree the pipeline uses

| Folder | What lives there | SAM table it feeds |
|---|---|---|
| `ou=allPeople` | every person, active or not, keyed by `x-ucar-upid`; under each person, child entries for **positions** (internal staff roles) and **collaborations** (external affiliations) | `users`, `user_organization`, `user_institution`, `email_address`, `phone` |
| `ou=allGroups` | every group, active or not, keyed by `gidNumber`, **without** membership | `adhoc_group`, `adhoc_group_tag` |
| `ou=groups,ou=unix` | only active POSIX-tagged groups, keyed by name, **with** membership expanded to individual accounts | `adhoc_system_account_entry` |
| `ou=allOrganizations` | internal UCAR organizations (labs, sections) | `organization` |
| `ou=externalOrgs` | external institutions (universities, agencies) | `institution` |
| `ou=serviceAccounts` | role logins (shared accounts with a contact person) | `users` with `login_type = role_login` |
| `ou=gidAllocations` | blocks of GIDs reserved for a purpose, e.g. `ou=projects` | `gid_allocation` |

Why two group folders: `allGroups` is the authoritative list including inactive groups
but does not expand sub-groups; `groups,ou=unix` is the operational view with
members expanded. SAM takes the group's identity and tags from the first and its
membership from the second (`sam-ldap-transformer/transformer_rewrite.pl:130-137`).

### 1.3 The `x-ucar-*` attributes you will meet

| Attribute | Meaning | SAM column |
|---|---|---|
| `x-ucar-upid` | permanent person id | `users.upid` |
| `uid` / `uidNumber` | username / unix uid | `users.username` / `users.unix_uid` |
| `x-ucar-active` | TRUE/FALSE | `users.active`, `adhoc_group.active`, … |
| `x-ucar-startDate` / `x-ucar-endDate` | generalized time, e.g. `20250411195730-0600` | `start_date` / `end_date` on the affiliation rows |
| `x-ucar-externalOrgId` | institution id | `institution.institution_id` |
| `x-ucar-organizationId` | internal org id | `organization.organization_id` |
| `x-ucar-positionId` | id of a staff position | `user_organization.idms_unique_name` |
| `x-ucar-source` | who last wrote the value (`PDB`, `EIP`, `SAM:<ts>`) | — |
| `x-ucar-tag` | group tag, multi-valued (`hpc`, `glade`, …) | `adhoc_group_tag.tag` |
| `x-ucar-groupName` / `cn` | long name / short key of a group | `adhoc_group.group_name` |
| `gidNumber` | unix gid | `adhoc_group.unix_gid`, `project.unix_gid` |
| `uniqueMember` / `member` | member as a person DN (allGroups) / as an account DN (unix groups) | membership |
| `x-ucar-stateProvCode` / `st` / `c` | state code / state name / country | `institution.state_prov_id` |
| `x-ucar-nsfOrgCode`, `x-ucar-shortOrgName`, `x-ucar-externalOrgType` | NSF code, acronym, type | `institution.*` |
| `x-ucar-parent` | DN of a parent organization | `organization.parent_org_id` |
| `x-ucar-orgLevel` / `x-ucar-orgLevelCode` | hierarchy level | `organization.level` / `level_code` |
| `x-ucar-gids` | `startGid:endGid` | `gid_allocation.startGid/endGid` |
| `x-ucar-authType` | token type (`Duo`) | `users.token_type` |
| `x-ucar-typedPhone` | `Ucar Office: 303-555-0100` | `phone` |
| `mail`, `x-ucar-forwardEmail` | addresses | `email_address` |

### 1.4 Four sample entries (values invented)

A **person** in `ou=allPeople`:
```
dn: x-ucar-upid=4242,ou=allPeople,dc=ucar,dc=edu
objectClass: x-ucar-uniquePerson
x-ucar-upid: 4242
uid: jdoe
uidNumber: 70042
givenName: Jane
sn: Doe
mail: jdoe@example.edu
x-ucar-active: TRUE
x-ucar-source: PDB
```
A **position** (staff role) under that person:
```
dn: x-ucar-positionId=9001,x-ucar-upid=4242,ou=allPeople,dc=ucar,dc=edu
objectClass: x-ucar-position
x-ucar-organizationId: 29
x-ucar-positionTitle: SOFTWARE ENGINEER III
x-ucar-startDate: 20240101000000-0700
x-ucar-active: TRUE
```
A **collaboration** (external affiliation) under a person:
```
dn: x-ucar-externalOrgId=210,x-ucar-upid=4242,ou=allPeople,dc=ucar,dc=edu
objectClass: x-ucar-collaboration
x-ucar-externalOrgId: 210
o: EXAMPLE STATE UNIVERSITY
x-ucar-startDate: 20250301000000-0700
x-ucar-endDate: 20260930235959-0600
x-ucar-active: TRUE
```
A **group** in `ou=allGroups` (membership lives in the unix folder, not here):
```
dn: gidNumber=68058,ou=allGroups,dc=ucar,dc=edu
objectClass: x-ucar-uniqueGroup
gidNumber: 68058
cn: hdt
x-ucar-groupName: hdt
x-ucar-tag: hpc
x-ucar-tag: glade
x-ucar-active: TRUE
```

### 1.5 LDIF, and the two flavors that matter

LDIF is the plain-text form of LDAP data: a record starts with `dn:`, each following
line is `attribute: value` (a double colon means base64), a blank line ends the
record. Two flavors flow through this pipeline:

- **A slapcat dump** is a full snapshot of the whole database, written by the
  `slapcat` tool, in internal order (children may precede parents). Files are named
  `<timestamp>Z-1-slapcat`.
- **An audit log** is a change journal. The `auditlog` overlay appends every write as
  a record with a `changetype:` of `add`, `modify`, `delete` or `modrdn`. A modify
  lists operations separated by `-`:
  ```
  dn: x-ucar-positionId=9001,x-ucar-upid=4242,ou=allPeople,dc=ucar,dc=edu
  changetype: modify
  replace: x-ucar-positionTitle
  x-ucar-positionTitle: SOFTWARE ENGINEER IV
  -
  ```

The rule the whole pipeline rests on: **the latest dump plus every later audit log
reconstructs the current directory** (`ucarldap/LDIF_Log_Processing_README.md:187-189`).

---

## 2. The pipeline end to end

```
fdb.ucar.edu (central directory)
   │ syncrepl (live replication, RID 167 in prod)
   ▼
sam-idms-ldap ── writes ──▶ auditlog.d/  <ts>Z-1-slapcat, auditlog.ldif
   │ shared volume /data/tomcat-sam/prod/sam-ldap-syncd on sam-app.ucar.edu
   ▼
sam-ldap-transformer ── writes ──▶ syncd/  <ts>Z-add.jsl (full), <ts>Z-mod.jsl (changes)
   │
   ▼
sam-ldap-syncd ── HTTP PUT/GET/DELETE ──▶ SAM  /api/protected/admin/{ldapsync,*Purge*,userlifecycle}
        │
        └── ldapmodify ──▶ fdbstage.ucar.edu (LDAP staging: project groups, collaborator end dates)
```

### 2.1 sam-idms-ldap: the live copy

**In plain terms.** An OpenLDAP server inside a container logs in to the central
directory and asks for a copy of everything it is allowed to see, then keeps the
connection open and receives each change as it happens. That is **syncrepl**
("refresh, then persist"; `ucarldap/opt-ldifs/13synccons.sh:103-109`). Every change
it receives is also appended to its own audit log, and that log is the raw material
for the next hop.

- **What it copies.** Originally a whitelist of nine object classes; the filter was
  commented out on 2026-08-10 ("relax sync filter", `sam-idms-ldap/Dockerfile:40`), so
  it now copies everything the `citldapsam` account may read, and the narrowing to
  the seven folders happens in the transformer. The filter is baked into `slapd.d` at
  first bootstrap, so a filter change only takes effect on a freshly initialized
  replica (`ucarldap/README.md:53-55`).
- **Startup.** It first reads the provider's `contextCSN` (a replication
  high-water mark) and saves it (`sam-idms-ldap/entrypoint.sh:200-216`). On an empty
  database it marks `auditlog.d/INITIALIZING`, starts slapd, waits until the local
  copy has caught up and the audit log has been quiet for 30 s, stops slapd, writes a
  full dump, and restarts for normal service (`ucarldap/entrypoint.sh:102-237`).
- **Dumps are written only at container start**; there is no periodic dump. The
  audit log grows continuously and is rotated by the *transformer*, not by slapd
  (`ucarldap/scripts/rotate_auditlog.sh:54-63`).
- **Config.** `SYNCREPL_PROVIDER_URL=ldaps://fdb.ucar.edu`, `SYNCREPL_CONSUMER_RID=167`,
  password file `LDAP_AUTH_citldapsam` under the secrets dir, `AUDITLOG_DIR=/var/data/auditlog.d`
  (`sam-ldap-syncd/prod/.env`). Host ports 9389/9636 map to 389/636.

### 2.2 sam-ldap-transformer: from LDIF to JSON lines

**In plain terms.** A polling loop that watches the audit log, waits for it to go
quiet, rotates it, and converts each new piece of LDIF into one JSON object per line
that the daemon can consume without knowing any LDAP. It is a plug-in
(`sam-ldap-transformer/transformer_impl.rc`) into a generic driver from ucarldap
(`ucarldap/scripts/ldif_log_processor.sh`).

**Stage 1, server LDIF → portable LDIF** (`transformer_impl.rc:275-293`,
`transformer_rewrite.pl:93-117`): keep only DNs under the seven folders, strip the
operational attributes, and for a full dump reorder so parents precede children.
A dump becomes `<ts>Z-full.ldif`; an audit log becomes `<ts>Z-inc1.ldif`.

**Stage 2, portable LDIF → `.jsl`** (`transformer_impl.rc:327-348`,
`transformer_rewrite.pl`): `<ts>Z-add.ldif` → `<ts>Z-add.jsl` (a full snapshot) and
`<ts>Z-mod.ldif` → `<ts>Z-mod.jsl` (changes). Each line is
`{"<op>": [<objId>, ...payload]}` with `op` one of `add`, `modify`, `delete`.

The object id comes from the DN shape (`transformer_rewrite.pl:476-518`):

| DN shape | objId |
|---|---|
| `x-ucar-upid=U,ou=allPeople` | `["person","U"]` |
| `x-ucar-externalOrgId=I,x-ucar-upid=U,ou=allPeople` | `["collaboration","U","I"]` |
| `x-ucar-positionId=P,x-ucar-upid=U,ou=allPeople` | `["position","U","P"]` |
| `uid=X,ou=serviceAccounts` | `["role","X"]` |
| `gidNumber=N,ou=allGroups` | `["group","N"]` |
| `cn=X,ou=groups,ou=unix` | `["activeGroup","X"]` |
| `x-ucar-organizationId=N,ou=allOrganizations` | `["organization","N"]` |
| `x-ucar-externalOrgId=N,ou=externalOrgs` | `["institution","N"]` |
| `ou=D,ou=gidAllocations` children | `["gidAllocations",<parent>,"D"]` |

Attribute maps (`transformer_rewrite.pl:139-231`) rename LDAP attributes to the JSON
keys the daemon expects; anything unmapped is dropped silently. Keys in UPPER CASE
are "pseudo-attributes" the daemon must resolve itself (they need a lookup or a
merge): person `EMAIL`, `FORWARD_EMAIL`, `PHONE`; collaboration/position
`START_DATE`, `END_DATE`, `ACTIVE`, `MODIFIED`, `ORGANIZATION_ID`; group `TAG`;
activeGroup `USERNAME`; institution `STATE_NAME`; organization `ORGANIZATION_ID`,
`PARENT_ORG`; role `CONTACT_PERSON_UPID`, `TITLE`; gidAllocations `GID_RANGE`. Value
transformers turn member DNs into `["user","jdoe"]`, parent DNs into
`["organization","271"]` or `["institution","1171"]`, and the `upid:instId` form
Fischer Identity writes into just the institution id (`fixInstitutionId`, `:462-468`).

Representations: a **modify** is `{"modify":[objId,"replace","PHONE",[...], "add",
"END_DATE",[...]]}`; a modify touching only unmapped attributes produces no line; a
**rename** becomes a modify that replaces the key attribute; a **delete** is
`{"delete":[objId]}` with no payload, and deletes of `activeGroup` and
`gidAllocations` are dropped on purpose (`:433-442`).

**Cadence and handshake.** Poll every `PROCESSING_LOOP_SLEEP_SECS` (60 s) until the
audit log is non-empty, then wait until it has been unchanged for 10 s, rotate,
convert, and process `*-add.ldif`/`*-mod.ldif` in timestamp order
(`ldif_log_processor.sh:178-215,351-376,540-609`). A **reset** (the daemon touches
`SYNCD_RESET_FILE`) makes the driver rebuild its queue as "latest dump + all later
changes", which produces a fresh `*-add.jsl`; the transformer deletes the reset file
after that add file is processed (`transformer_impl.rc:310-343`). A named pipe
`.FIFO` in the spool dir lets the daemon block efficiently; the transformer unblocks
it after writing a non-empty file (`transformer_rewrite.pl:328-330`). The 2026-08-10
"fix empty output file bug" commit made a file whose records all map to nothing count
as success with no output instead of a fatal error (`transformer_rewrite.pl:343-384`).

### 2.3 sam-ldap-syncd

The subject of §3.

### 2.4 Deployment

- **Host** `sam-app.ucar.edu`, rootless podman as user `swes`; files owned by
  `tomcat-sam` (303). `sam-app/REPOS` gives the image build order and
  `sam-app/PROD_SERVICES` lists `amie-sam-mediator/prod` and `sam-ldap-sync/prod`.
- **One compose file runs all three containers**: `sam-ldap-syncd/prod/docker-compose.yml`
  (`ldap`, `transformer`, `syncd`, plus profile-only helpers `syncdInit`, `syncdTest`,
  `syncdDump`, `syncdadmin`). They share `/data/tomcat-sam/prod/sam-ldap-syncd`
  mounted at `/var/data` (`auditlog.d/`, `ldap/`, `slapd.d/`, `syncd/`, `.reset`) and
  the read-only secrets dir `/run/secrets`. The networks are internal; the containers
  communicate only through the shared files.
- **systemd** user unit `prod/prod-sam-ldap-syncd.service`: `ExecStartPre` runs
  `podman-compose down`, `ExecStart` runs `service-start.sh`, which runs
  `opsmonitor.sh` once and then at 01,05,09,13,17,21 h (`prod/service.env`).
  `opsmonitor.sh` bounces the stack if the syncd container is missing or not "Up" and
  emails `EMAIL_ADDR`. There is no compose `restart:` policy, so **a daemon that
  aborts stays down until the next opsmonitor tick, up to four hours.**
- **Test** (`test/.env`): RID 166, `SAM_URL=https://test-sam.ucar.edu`, staging
  writes stubbed via `LDAPSTAGING_UPDATES_STUB`.
- A second compose file, `sam-idms-ldap/prod/docker-compose.yml`, defines a
  standalone ldap service on the same host ports; it cannot run beside the syncd
  stack and reads as a parked experiment.

---

## 3. The daemon, feature by feature

Paths in this section are relative to `sam-ldap-syncd/`.

### 3.1 Entry point, modes, parameters

`bin/syncd` runs in the foreground as the container command (no daemonizing, no
signal handlers). Options select a mode (`bin/syncd:79-136,233-256`):

| Option | What it does |
|---|---|
| `-v` (repeatable) | verbosity; prod runs `-vvvvv` = DEBUG |
| `-r`, `--redirect` | send output to `$DATA_DIR/log-<date>-syncd.{o,e}` with hard links `log-syncd.{o,e}` |
| `-i`, `--init-only` [`-f`] | build the in-memory database and exit; `-f` first deletes `sam-data.json` and `sam-log.jsl`. Exits BUSY if a daemon holds the pid lock |
| `-a`, `--abort-on-exception` | any exception in the loop is fatal (dev compose uses it) |
| `-l`, `--lifecycle-update`, `-p`, `--project-update` | touch a trigger file and exit |
| `-S`, `--snapshot` | touch `.trigger-snapshot`; with no daemon running, initializes instead |
| `-d`, `--display-dump` | show dump metadata; `-v` adds the dump, `-vv` the log |
| `-t`, `--test-connections` | test SAM and staging; exit code is the OR of both |
| `-s`, `--show-config`, `-h` | print and exit |

Parameters come from the environment; each default is a `_DFLT` line in the image's
`lib/Constants.rc` (`bin/syncd:459-482`). **Setting a `_DFLT` name in `.env` does
nothing** (bug 13):

| Env var | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `/var/data` | logs; parent of a relative `SYNCD_DIR` |
| `SYNCD_DIR` | `syncd` | working dir; the process `chdir`s here |
| `SYNCD_LOOP_WAIT_SECS` | 60 | intended block time in the main loop (see bug N15) |
| `SYNCD_PROJECTS_CRON` | `0 4,6,8,10,12,14,16,18 * * * *` | project-group push schedule |
| `SYNCD_LIFECYCLE_CRON` | `0 22 * * * *` | lifecycle push schedule |
| `SYNCD_USER_DEACTIVATION_GRACE_PERIOD_HOURS` | 24 | passed to SAM's pending-deactivations query |
| `SYNCD_RESET_FILE` | none | transformer reset flag; prod `/var/data/.reset` |
| `SYNCD_SAMUPDATE_DEBUG_LOG` | none | per-update disposition log, written only if the file already exists |
| `SAM_URL`, `SAM_USER` | `https://sam.ucar.edu`, none | `SAM_USER` must be set in env; the constants-file value is lost (bug N16) |
| `SAM_RETRY_WAIT_DFLT` / `_MAX` | 30 / 300 s | HTTP retry sleeps |
| `SAM_UPDATES_STUB` | none | append PUTs to a file instead of sending (DELETEs still go out, bug N8) |
| `LDAPSTAGING_URL`, `LDAPSTAGING_USER` | none | prod `ldaps://fdbstage.ucar.edu`, `citldapsam` |
| `LDAPSTAGING_UPDATES_STUB` | none | append LDIFs to a file instead of running `ldapmodify` |
| `SECRETS_DIR` | `/run/secrets` | password files `SAM_AUTH_<user>`, `LDAPSTAGING_AUTH_<user>` |

Constants-only (not overridable): `SYNCD_SAM_DATA=sam-data.json`,
`SYNCD_SAM_LOG=sam-log.jsl`, `SAM_API_BASE=api/protected/admin`, every `SAM_*_RPATH`.

Process facts: pid file `syncd.pid` held with `flock`, never removed
(`bin/syncd:621-648`); the cron scheduler runs **detached** in a forked process, and
each job is forked again from it (`bin/syncd:429,580-591`); exit codes 0 success,
1 bad argument, 2 busy.

### 3.2 State on disk (in `SYNCD_DIR`)

| File | What it is |
|---|---|
| `sam-data.json` | one-line JSON snapshot of the whole in-memory database: `[latestUpdateFile, {type→{key→record}}, {idMaps}]` (`lib/IMDBCore.pm:38-45`). Written atomically (`.tmp` then rename) after every `*-add.jsl`, after the first `*-mod.jsl` of a new UTC day, on `--snapshot`, and on reset. Writing it truncates the log |
| `sam-log.jsl` | the update journal since the last snapshot, one JSON array per line: `["addOrUpdate",type,key,{rec}]`, `["delete",type,key,{tombstone}]`, `["no-op",...]`, `["checkpoint","<input file>",null,null]` (`lib/SamUpdate.pm:133-139`). Unrelated to the transformer's `.jsl` files |
| `sam-data.json.bad` | the snapshot renamed aside when the daemon decides its picture is corrupt; forces a rebuild from SAM |
| `syncd.pid` | flock-based pid file |
| `.trigger-snapshot`, `.trigger-projectGroup-update`, `.trigger-lifecycle-update` | created by `-S`, `-p`, `-l`; the main loop polls for them |
| `../.reset` | the transformer reset flag (§2.2) |
| `2*-add.jsl`, `2*-mod.jsl` | input. When a new add file arrives everything older is **deleted**; at each snapshot the applied mod files are concatenated into the newest one (`lib/IDMSFileManager.pm:159-163,205-255`). Nothing is archived |
| `$DATA_DIR/log-YYYY-MM-DD-syncd.{o,e}` | daemon logs, rolled daily, never pruned |

**Restart.** If either the snapshot or the log is missing, the daemon downloads every
collection from SAM, writes a fresh snapshot with an empty checkpoint, and waits for a
new full dump from the transformer (`lib/Synchronizer.pm:379-412,485-503`). Otherwise it
loads the snapshot, replays the log, and re-applies any input file newer than the last
checkpoint, including the one in progress when it died (`:533-553`). Records are
full-state and idempotent, so a replay cannot double-apply to SAM.

### 3.3 The in-memory database

The daemon keeps a complete copy of what SAM should contain, called the IMDB, so
that a fragmentary LDAP change ("this person's phone changed") can be turned into a
complete record to PUT. One manager per type, all singletons (`lib/SamDataManager.pm:141-161`):

| SAM type | Key | Extra id maps | From SAM / to SAM / purge |
|---|---|---|---|
| institution | `institutionId` | — | yes / yes / yes |
| organization | `organizationId` | `idmsOrgNameToId` (+`NEXT_ID`), `orgAcronymToId` | yes / yes / yes |
| user | `unixUid` | `usernameUpidToId` (username and upid share one map) | yes / yes / yes |
| collaboration, position | `upid:<instId>` or `upid:<idmsUniqueName>`, stored **inside** the user record | position: `positionIdmsUniqueNameToPositionId` | travel with the user |
| group | `posixGid` | `groupKeyToId` | yes / yes / yes |
| gidAllocation | `startGid` | — | yes / yes / no |
| projectGroup | `posixGid` (incl. 78426) | `hpcMembersToRefcnt` | yes / no / no |
| groupTag | `name` | — | yes / no / no |

Applying a change (`lib/IDMSUpdateManager.pm:331-388,425-510`): start from defaults,
overlay the existing record, apply the simple attributes, run the per-type
"modifiers" for the pseudo-attributes, normalize, store. A modify for an unknown key
raises the "my picture is corrupt" exception (`IMDBStateException`), which renames the
snapshot aside and rebuilds from SAM.

A **full dump** is handled by copy-then-diff (`lib/IDMSDumpSynchronizer.pm:109-174`):
deep-copy the current records, apply the whole dump to the live IMDB without sending
anything, then for each type in order (institution, organization, user, group,
gidAllocation) push adds and updates for records that differ, and deletes for records
missing from the dump, deletes in reverse key order within the type. A dump with
suspiciously few records is refused (`:57-67`: people ≥ 5000, groups ≥ 1000,
institutions ≥ 500, organizations ≥ 100, roles ≥ 10).

### 3.4 Reading IDMS changes

**Discovery.** Files matching `^20[0-9].*-(add|mod).jsl$`, sorted by name (a
timestamp). The daemon walks back to the newest add file and takes it plus every
later mod file newer than the checkpoint (`lib/IDMSFileManager.pm:129-165`). No add file
at all forces a transformer reset.

**Main loop** (`lib/Synchronizer.pm:195-271`): reset check, transformer sync, snapshot
trigger, project/lifecycle triggers, collect files, apply. An add file goes to the
dump synchronizer; a mod file is applied one record at a time and each resulting
change is pushed immediately. On an exception: BUG or INPUT types abort the daemon;
the corrupt-picture exception rebuilds; anything else sleeps 300 s and continues.

**What "changed" means**: the normalized old and new records are compared with
`eq_deeply`; equal is a no-op, different is an addOrUpdate; a record that stops being
"updatable" becomes a delete (`lib/SamDataManager.pm:681-782`).

**Per-type transformation rules** (IDMS → SAM):

| Type | Rule |
|---|---|
| institution | straight copy; `STATE_NAME` fills `state` if the code is missing (`lib/SamInstitutionData.pm:153-165`). Tombstone: name + " INACTIVE", acronym + " --x", `deleted=true` |
| organization | `ORGANIZATION_ID` that is 1–10 decimal digits is used as-is; any other string (a Workday/Fischer id) is looked up in `idmsOrgNameToId`, and if absent the daemon **allocates** the next id ≥ 1000 (`lib/SamOrganizationData.pm:100-116,170-204,251-263`). `PARENT_ORG` becomes `parentOrgAcronym` only if the parent is already known. Tombstone: `active=false, deleted=true` |
| person → user | `typeOfLogin=user_login`; `EMAIL`/`FORWARD_EMAIL` become `emails[{email,primary}]` (primary first, deduplicated); `PHONE` "type: number" strings become `phones[]`; `chargingExempt` and `locked` forced false; `academicStatus` description mapped to code (`lib/IDMSPerson.pm:44-154`, `lib/AcademicStatus.pm`) |
| role → user | keyed by userName, `typeOfLogin=role_login`; names copied from the contact person; `TITLE` cut to 45 chars (`lib/IDMSRole.pm:44-140`) |
| collaboration, position | edit `user.collaborations[]` / `user.positions[]` and return the whole user as the changed record. The `ACTIVE`, `MODIFIED`, `START_DATE`, `END_DATE` shadow attributes derive `startDate`/`endDate` (active with a past end clears it; inactive with no end uses MODIFIED). Same employer with non-overlapping dates appends a new row; an inverted range is fixed and logged (`lib/IDMSUserEmployment.pm:225-309`, `lib/SamUserEmploymentData.pm:332-541`). Collaborations without institution or start date are dropped |
| interim accounts | a person whose `userName` is missing or equals the upid string (a Fischer placeholder) is kept in the IMDB and journal but **not sent** to SAM (`lib/SamUserData.pm:252-265`; PR #1, then generalized) |
| user tombstone | `active=0, deleted=true`, emails and phones cleared, open affiliations end-dated now (`lib/SamUserData.pm:390-415`) |
| group | `TAG` filtered to tags that are access branches (`lib/SamGroupData.pm:369-382`); membership from `activeGroup` `USERNAME`; `normalize` resolves usernames to `upids[]` (user logins) and `rolenames[]` (role logins), dropping names it does not know (`:322-357`). A group with no accessBranch tag, gid 1000 (`ncar`), gid 78426, or any project's gid is "not updatable" and never sent (`:238-261`). An `activeGroup` delete empties the membership rather than deleting the group |
| gidAllocations | only `domain=projects`; `GID_RANGE "a:b"` → `startGid`/`endGid` (`lib/IDMSGidAllocation.pm:79-121`) |

**Dates** are normalized to epoch milliseconds UTC with the milliseconds zeroed
(`lib/SamDataUtil.pm:74-117`). Accepted: epoch seconds or ms, and
`YYYYMMDDhhmmss[.f](Z|±hhmm)`. A timestamp without a zone is a BUG and aborts the
daemon. Output to LDAP and the lifecycle expiry use container-local time, which the
base image pins to `America/Denver` (`sweet/Dockerfile:42`).

### 3.5 Pushing to SAM

`lib/SamUpdatePropagator.pm:74-189` decides per change:

1. **no-op / checkpoint** → journal only.
2. **deferred** (interim account, incomplete group) → journal, log "Deferring
   propagation", no HTTP.
3. **addOrUpdate** → `PUT /ldapsync/<type>` with the full record. The read-back
   GET that was meant to capture SAM-assigned ids never runs (bug 7).
4. **delete** → `GET /<type>PurgePermit`; if `purgeable`, `DELETE /<type>Purge`,
   otherwise `PUT` the tombstone record.

The HTTP rules (challenge-response Basic auth with realm `Realm`, 404 retried
forever, 300–500 logged then a 300 s pause, JSON bodies required) are in
`LDAP_SYNC_API.md` §2.1. The consequence that matters here: the IMDB is updated
**before** the PUT, so after a rejected PUT and the 300 s pause the same file is
re-applied against the already-updated picture, compares as a no-op, and the record
is never retried (bug 15).

### 3.6 Project groups → LDAP staging

**In plain terms.** Every SAM project is also a unix group (`project.unix_gid`).
SAM is the authority for who is on a project, so the daemon periodically asks SAM for
every project as a group (`GET /ldapsync/projectGroup`), compares each with the group
it last saw from LDAP, and sends the differences to the staging directory so that the
central directory's group membership follows SAM (`lib/ProjectGroupUpdater.pm:138-220`).

- SAM's response per project: `posixGid`, `key` (lowercase projcode), `name`,
  `active`, `upids` (active members on live allocations, lead and admin always),
  `rolenames`, `tags` (access branches, `exclude-from-google`, `auto-renewed-project`),
  `lastModified`. Server rules are in `LDAP_SYNC_API.md` P1a.
- A pseudo-group **`all-hpc-users`** (gid 78426) is computed locally as the
  reference-counted union of every active project group's members plus every active
  access-branch ad-hoc group's members (`lib/HpcMembers.pm:165-297`,
  `lib/SamProjectGroupData.pm:69-77,155-220`). It is never sent to SAM, only to staging.
- The comparison is against the **IMDB's LDAP-derived copy**, not a live LDAP
  query. Diffed attributes: `active`, `upids`, `rolenames`, `tags`. A new group
  produces an `add` LDIF (`objectClass x-ucar-uniqueGroup`, `gidNumber`,
  `uniqueIdentifier`, `cn`, `x-ucar-groupName`, `x-ucar-active`, `x-ucar-tag`,
  `x-ucar-source: SAM:<ts>`) with no members; a changed group produces up to two
  `modify` LDIFs, one for attributes and one for `uniqueMember` lines of the form
  `x-ucar-upid=N,ou=allPeople,…` or `uid=R,ou=serviceAccounts,…`
  (`lib/StagingTransformers.pm:221-260`).
- Design note in the source: staging has high latency and no confirmation, so the
  job fires and forgets; the next run notices any remaining difference again
  (`lib/ProjectGroupUpdater.pm:30-37`).

### 3.7 Collaborator lifecycle → LDAP staging

**In plain terms.** An external collaborator's directory entry carries an end date,
after which the directory (and everything downstream) drops them. SAM knows better
than the directory how long a collaborator is still needed: as long as they hold a
live project membership, contract role, or data on disk. So the daemon asks SAM for
each collaborator's *nominal expiry* and, if it is later than the directory's end
date, pushes the later date to staging. It **only ever extends**
(`lib/LifecycleUpdater.pm:25-40,177-222`).

- SAM's answer (`GET /userlifecycle/collabexpiryupdates`): rows with `unixUid`,
  `activeCollaborator`, `nominalExpiry` (`yyyy-MM-dd`), among others. The server
  computes the expiry as the latest end over contracts (PI/monitor), led/admin
  projects and their allocations, current memberships' allocations, and disk holdings,
  each plus a 90-day grace (`LDAP_SYNC_API.md` P1d).
- The daemon skips a row when it is not active, the user is unknown, the user has no
  currently active collaboration in the IMDB, or the directory's end date is already
  later; otherwise it queues a modify replacing `x-ucar-endDate` (and
  `x-ucar-source: SAM:<ts>`) on the collaboration entry (`:186-213`).
- After the collaborator pass, the same job is meant to **finish deactivations**:
  ask SAM for users marked inactive for more than the grace period
  (`pendingdeactivations/{hours}`) and tell SAM to close each one (`deactivate/{user}`).
  That half never runs (bugs 3, 2, 8, in a chain). The visible result in the 2026-09-24
  snapshot: 143 users stamped `users.deactivate`, all still active, none finished.

### 3.8 The LDAP staging client

`lib/UpdateStagingClient.pm` does not use an LDAP library. It runs the `ldapmodify`
command as a subprocess with a SASL bind and a password file
(`ldapmodify -U $LDAPSTAGING_USER -y /run/secrets/LDAPSTAGING_AUTH_<user> -H $LDAPSTAGING_URL`,
`:91`) and feeds the LDIF on stdin (`:243-275`). LDIFs are queued per DN and one
`ldapmodify` is run per DN when the queue is serviced (`:178-236`).
`--test-connections` runs one authenticated `ldapsearch` on `ou=allGroups` and looks
up three permission groups anonymously (`:136-165`). With `LDAPSTAGING_UPDATES_STUB`
set, the command and LDIF are appended to that file instead. A sample modify:

```
dn: x-ucar-externalOrgId=886,x-ucar-upid=4242,ou=allPeople,dc=ucar,dc=edu
changetype: modify
replace: x-ucar-endDate
x-ucar-endDate: 20260930235959-0600
-
replace: x-ucar-source
x-ucar-source: SAM:20260930235959-0600
-
```

### 3.9 Scheduling

Two `Schedule::Cron` entries, "project" and "lifecycle" (`bin/syncd:540-561`), run by
a detached scheduler process that forks a child per firing. Two traps:

- The scheduler's sixth field is **seconds**, so `0 22 * * * *` means "every second
  of the 22:00 minute": up to 60 children per slot (bug 13).
- The children hold a copy of the IMDB **as it was when the daemon started**; live
  updates never reach them (bug N4). Triggered runs (`-p`, `-l`, the trigger files)
  run in the main process against the live IMDB, so they behave differently.

### 3.10 Logging and observability

- Levels (`sweet-pl5/lib/Vprint.pm:142-148`): 0 CRITICAL, 1 ERROR, 2 WARN, 3 NOTICE,
  4 INFO, 5 DEBUG, 6 TRACE. The table in the repo's `Troubleshooting.md:49-50` is
  off by one. Timestamps are added by `dated-file-writer` in the redirect path.
- A healthy run: at startup "Reading data from dump file", "Loading logfile
  updates", "Initialization complete"; per mod file (DEBUG) "N update files found",
  "Applying file X", "File X processed"; per dump (INFO) "IDMSDumpSynchronizer
  propagating N <type> updates / deleting N obsolete / ignored N", then "Auditing
  alternate IDs"; staging "Loaded N projectGroup records", "Successfully ran
  ldapmodify for X of Y".
- Trouble signs: "Deferring propagation of", "Pausing and continuing after
  exception", "ABORTING on", "Can't store CODE items", "temp SAM init failure"
  (a 404 being retried).
- `SYNCD_SAMUPDATE_DEBUG_LOG` (only if the file pre-exists) writes one tagged line
  per record: NULL UPDATE, MOOT DELETE, GOOD DELETE, NOT UPDATABLE, WAS UPDATABLE,
  NO CHANGE, CHANGED, CREATED, and a `DEACTIVATING!` marker.
- No metrics, no status endpoint. Health = `opsmonitor.sh` and `--display-dump`.

### 3.11 Tests and CI

24 `lib/*.t` files run in the order listed in `lib/test.cf`, via `./run-all-tests`
(a `runtests` docker image; `Testing-Support.md`), with fixtures in `lib/Test*Data.pm`
and a `MockSamClient`. CircleCI config exists. **Not covered:** `Synchronizer`,
`SamClient`, `UpdateStagingClient`, `ProjectGroupUpdater`, `LifecycleUpdater`, the
employment/position/collaboration builders, `IDMSGidAllocation`. One shipped test
(`SamGroupData.t:117-120`) would fail against current code (bug N1), and one
assertion is a no-op (`IDMSDumpSynchronizer.t:202`, `ok(@$x = 0)`), which suggests
the suite is not run before deploys.

### 3.12 Operating it

| Task | How |
|---|---|
| Force a full resync from LDAP | create `SYNCD_RESET_FILE`, or delete `sam-data.json`/`sam-log.jsl` and restart (`DatabaseReinitialization.md`; note its doc says `--init-only -f` works while a daemon runs, but the code exits BUSY) |
| Rebuild the picture from SAM | delete the snapshot or the log; the daemon downloads everything and waits for a new add file |
| Take a snapshot now | `syncd -S` |
| Force a project-group or lifecycle push | `syncd -p` / `syncd -l` (runs in the main process on live data) |
| Inspect the picture | `syncd -d -v` (jq output) |
| Check connectivity | `syncd -t`; compose profile `syncdTest` |
| Known data repairs | `support_notes` (2026-07-17 runbook with manual SQL), `LoggedInconsistencies.md` (what the daemon logs and what to do; its advice to delete duplicate LDAP positions triggers bug N17) |
| Restart cadence | systemd unit + `opsmonitor.sh` at 01,05,09,13,17,21 h |

---

## 4. Bugs to report upstream

All in NCAR/sam-ldap-syncd unless noted. `gh issue list` shows #2–#5 open (data and
operations topics) and PR #1 merged; none covers the rows below. "Verify" means the
code says so but production has not been observed. Severity: **blocks** (feature
unusable or daemon aborts/hangs), **loss** (silent data loss), **wrong** (wrong data
or behavior), **cosmetic** (latent or minor).

**Root cause shared by several rows:** no module under `lib/` has `use strict` or
`use warnings`; the Dockerfile's `PERL5OPT=-Mstrict -Mwarnings` only applies to
`bin/syncd` because pragmas are file-scoped. Misspelled variables silently become
package globals (rows 3, 6, 7, N7, N13).

### 4.1 Check these in production first

| id | Title | Sev | Where | Effect | Fix |
|---|---|---|---|---|---|
| N1 | `filterTags` returns the manager object instead of the tag list | blocks (verify) | `lib/SamGroupData.pm:369-382`; caller `lib/IDMSGroup.pm:157` | any LDAP group with `x-ucar-tag` gets `tags=[<object>]`; deep-copying it fails ("Can't store CODE items"), the loop pauses 5 min and retries the same file forever. Its own test would fail. **Counter-evidence:** the 2026-09-24 snapshot shows user affiliation rows re-stamped daily, so the loop is not jammed; either tagged groups are not reaching the daemon or the copy survives. Grep the log for the message | `return @adHocTags;` |
| 13 | prod `.env` cron "disable" is inert; sixth cron field is seconds | wrong (load) | `bin/syncd:459-482`; `prod/.env:69-74`; `lib/Constants.rc:10-11` | `.env` sets `SYNCD_*_CRON_DFLT`, which nothing reads; the shipped `0 22 * * * *` fires every second of 22:00 → up to 60 forked jobs, same at every even hour 04–18 for projects. If the pin worked it would silently re-enable in December | set `SYNCD_*_CRON` in `.env`; make the sixth field `0` in `Constants.rc` |
| N4 | cron children run on the IMDB as of daemon start | wrong | `bin/syncd:429,580-591` | groups created after startup are pushed as `changetype: add` forever (fails "Already exists"), so their membership never syncs; lifecycle ignores collaborations added since startup; the detached scheduler sends its output to `/dev/null`, so its errors vanish | have `cronDispatcher` only touch the trigger files so the main process does the work |
| 15 | a rejected PUT is lost for good | loss | `lib/Synchronizer.pm:568-631`; `lib/SamUpdatePropagator.pm:104-118` | the IMDB is updated before the PUT; after the 300 s pause the file replays as a no-op; the next snapshot persists the unsent state; a later full dump diffs against it and does not resend | restore the old record (or invalidate the IMDB) before re-throwing |
| N14 | `all-hpc-users` erodes on every restart | wrong | `lib/SamProjectGroupData.pm:171-179` | the pseudo-group is looked up by name in a map keyed by gid, so every snapshot load re-adds an empty default and decrements every member's refcount; users in exactly one group drop out, and that is pushed to staging | look up by `$ALL_HPC_USERS_GID` |
| N6 | Fischer (hex-id) organizations are re-allocated on every full dump | wrong | `lib/IDMSOrganization.pm:77-80` | `idmsUniqueName` is not recorded when an id is allocated, so the map never learns the org; each dump allocates a new `organizationId` and tombstones the old; positions cannot resolve the org; a later modify raises the corrupt-picture exception | also set `idmsUniqueName` in the allocation branch |
| N15 | main loop polls continuously instead of blocking | wrong (verify: CPU) | `sweet-pl5/lib/FileUtil/SpoolDirectory.pm:310-351` | `getFilenames` only blocks when no matching file exists; the add file is always present, so `SYNCD_LOOP_WAIT_SECS` never applies | block when no file is newer than the checkpoint |

### 4.2 The deactivation chain (each fix exposes the next)

| id | Title | Sev | Where | Effect | Fix |
|---|---|---|---|---|---|
| 3 | `$samClient` undefined in `_serviceSamLifecycle` | blocks | `lib/LifecycleUpdater.pm:289-303` | dies after the collaborator pass; pending deactivations never finish; IDMS-inactive users stay active in SAM | `my $samClient = $self->{'samClient'};` |
| 2 | path key `'deactivation'` vs registered `'deactivate'` | blocks | `lib/SamClient.pm:366` vs `:95`; throws at `:389-393` | BUG exception (aborts the daemon from the main loop) once row 3 is fixed | use `'deactivate'` |
| 8 | grace hours appended as `?24` instead of `/24` | blocks | `lib/SamClient.pm:215-221`; `lib/Constants.rc:37`; `lib/LifecycleUpdater.pm:295` | requests `/pendingdeactivations/?24`, a 404, retried forever (N2); 1–9 hour grace fails the `^[1-9][0-9]+$` check | build the path with the hours as a segment |
| N2 | 404 retried forever | blocks | `lib/SamClient.pm:515-523` | any real 404 hangs the caller silently after four log lines | cap retries, then `throwError(404, …)` |

### 4.3 Other defects

| id | Title | Sev | Where | Effect | Fix |
|---|---|---|---|---|---|
| N5 | main loop keeps a stale IMDB object after a rebuild | loss | `lib/Synchronizer.pm:202` vs `:403-407` | after the corrupt-picture path, `_checkForReset` builds a new IMDB but the loop keeps the old one; the next dump diffs against frozen data; the snapshot write persists the invalidated state | re-read `$imdb` each iteration |
| N17 | deleting a position or collaboration entry in LDAP can purge the user | loss | `lib/IDMSUserEmployment.pm:254-269,307-308`; `lib/SamDataManager.pm:702-713` | the delete is logged "rejected" but still returned as a delete of the **whole user**; the propagator asks SAM for a purge permit and purges if allowed, else PUTs the unchanged user; the journal records a user delete, so a replay removes the user from the IMDB. `LoggedInconsistencies.md` recommends exactly this LDAP deletion | return a no-op for employment deletes |
| 9 | `purgePermit` "no path → purgeable" branch unreachable; `isPurgeable()` never consulted | blocks (latent) | `lib/SamClient.pm:313-316,389-393`; `lib/SamUpdatePropagator.pm:179`; `lib/SamGidAllocationData.pm:75-103` | a gidAllocation missing from a dump → `_getPath` throws BUG → abort; recurs on every add file | `return unless isPurgeable()` before the permit call |
| 14 | snapshot load rejects any empty type map | blocks (restart) | `lib/SamDataUtil.pm:290-305` | a legitimately empty `groupTag` or `gidAllocation` collection makes startup fail until a full rebuild | require a hash, not a non-empty one |
| 6 | `$groupTags` fetched, `$groupTag` loaded | wrong | `lib/ProjectGroupUpdater.pm:161-162` | tags are never refreshed after init; new access branches never make groups updatable | use `$groupTags` |
| 10 | staging queue keyed by DN | wrong | `lib/UpdateStagingClient.pm:178-188`; `lib/StagingTransformers.pm:221-260` | a group whose attributes and membership both change loses the attribute LDIF | queue as a list |
| N3 | staging queue never cleared | wrong | `lib/UpdateStagingClient.pm:203-236` | every DN ever queued in the main process is re-sent each service; a stale `replace` can undo newer directory state; the count is doubled; the return value is lost inside `try` | delete the entry on success |
| N7 | `$maxLen` vs `$maxlen` in the organization tombstone | wrong | `lib/SamOrganizationData.pm:435-437` (also `lib/SamInstitutionData.pm:126`) | acronyms longer than 11 chars become `" --x"`, so tombstoned orgs collide on acronym | fix the name |
| N8 | `SAM_UPDATES_STUB` does not stub DELETE | loss (test/dev) | `lib/SamClient.pm:334-350,480-503` | "non-destructive" stub mode still purges in SAM | add the stub check to `_delete` |
| N10 | group normalize drops unknown usernames | wrong (transient) | `lib/SamGroupData.pm:338-345` | members not yet in the IMDB (ordering, rename, case) are dropped until the next full dump; lookup is exact-case | keep unresolved names; compare case-insensitively |
| 1 + 7 | `getSAMObject` defined twice (second drops `$id`); `user->` bareword makes `willSamModify` always false | cosmetic, **fix together** | `lib/SamClient.pm:236,260`; `lib/SamUserData.pm:292,298` | the read-back after PUT never runs, so SAM-assigned position ids are never learned; fixing 7 alone turns every user PUT with a new affiliation into a BUG abort via 1 | delete the second definition; use `$user` |
| 4 + 5 | `since` sent without a name; reads `lastModifiedDate` but SAM sends `lastModified` | cosmetic (perf) | `lib/SamClient.pm:215-221`; `lib/ProjectGroupUpdater.pm:179-181` | every project-group fetch is full. **Do not "fix"**: SAM's `lastModified` is the project's modified time, which membership changes do not bump, so a working watermark would miss them | delete the incremental logic |
| N9 | `$ex->isa` on plain-string exceptions | cosmetic (latent) | `lib/Synchronizer.pm:247`; `lib/Misc.pm` `exceptionHasType` | a `die` with a string starting with a non-identifier character kills the catch block itself | `blessed($ex) && $ex->isa(...)` |
| N16 | `SAM_USER` default lost | cosmetic | `bin/syncd:501-507`; `lib/Constants.rc:17` | the parameter's undef default overwrites the constants value, so the env var is mandatory | read the constant as the default |
| N11 | a torn last line in `sam-log.jsl` blocks startup | cosmetic | `lib/Misc.pm:140` | a crash mid-append leaves an unparsable line and the daemon will not start | skip an unparsable final line |
| N18 | `_orderTypedIDMSRecs` reads fields that are not on the object | cosmetic | `lib/IDMSDumpSynchronizer.pm:215-248` | the numeric-before-string ordering never happens | read from `->data` |
| N12 | minor bundle | cosmetic | `lib/IMDB.pm:436` (`$latestFileUpdate` typo); `bin/syncd:631` (`throwSysException` does not exist); pid file never truncated; `--snapshot` with no daemon writes no dump; `getUpdateFileType` never returns `mod`; `Troubleshooting.md` level table off by one; `DatabaseReinitialization.md` vs BUSY exit | — | — |
| N13 | `numArrayNormalizer` shares a global `@outArr` | cosmetic (latent) | `lib/SamDataUtil.pm:269` | harmless only because callers deep-copy | `my @outArr` |
| N19 | tests not run before deploy | process | `lib/SamGroupData.t:117-120`; `IDMSDumpSynchronizer.t:202`; six "Synchronizer exception bug fix" commits on 2026-08-17 | a failing shipped test and a no-op assertion; typo fixes landed in production one at a time | run `./run-all-tests` in CI on every push |

### 4.4 Candidates examined and set aside

- **gidAllocation re-PUT every dump** (SAM adds `gidAllocationId`/`creationTime`):
  refuted, `createSamData` merges the existing record so they compare equal
  (`lib/IDMSUpdateManager.pm:430-434`).
- **Tombstone `active:0` as integer**: harmless, the Java field is primitive
  `boolean` and Jackson coerces 0 (`java:service/idservice/model/domain/IdServiceUser.java:15`).
- **`activeCollaborator` key mismatch**: Jackson 2.x accepts an `is` getter returning
  boxed `Boolean`, so the property is `activeCollaborator` and matches the Perl.
  Confirm with one `curl` of `/userlifecycle/collabexpiryupdates`; if confirmed, the
  collaborator extension works whenever the 22:00 job fires, subject to N4.
- **Container timezone**: the base image sets `TZ=America/Denver`
  (`sweet/Dockerfile:42`), so 23:59:59 local is Mountain as intended.
- **Organization ids ≥ 1000 colliding with SAM's**: no collision, the daemon is the
  only allocator (`Organization.hbm.xml` has no generator) and `NEXT_ID` is recomputed
  as max+1 after each load. The real defect is N6.
- **Replay double-applying to SAM**: cannot, records are full-state and idempotent;
  at worst one update is re-sent after a crash between the PUT and the journal write.

---

## 5. Glossary and file index

| Term | Meaning |
|---|---|
| IMDB | the daemon's in-memory copy of what SAM should contain (§3.3); snapshot `sam-data.json`, journal `sam-log.jsl` |
| add file / mod file | `<ts>Z-add.jsl` full snapshot from LDAP / `<ts>Z-mod.jsl` incremental changes |
| checkpoint | journal line naming the last input file fully applied |
| reset file | `SYNCD_RESET_FILE`; asks the transformer for a fresh full snapshot |
| tombstone | the record PUT to SAM when a deleted LDAP object cannot be purged (marks it inactive/deleted) |
| purge permit | SAM's answer to "may I hard-delete this?"; false when SAM still references it |
| interim account | a Fischer placeholder person whose username equals the upid; kept but never sent |
| access branch | a SAM `access_branch` name (`hpc`, `hpc-data`, `hpc-dev`); only groups tagged with one are synced |
| role login | a shared account (`login_type = role_login`) with a contact person |
| project group | a SAM project exposed as a unix group with SAM's members; pushed to staging |
| all-hpc-users | gid 78426, the computed union of every HPC group's members; staging only |
| staging service | `fdbstage.ucar.edu`, the LDAP server that accepts SAM's writes for the central directory |
| syncrepl | OpenLDAP replication: initial refresh then a persistent change stream |
| slapcat / audit log | full dump / change journal in LDIF (§1.5) |

Modules under `sam-ldap-syncd/lib/`:

| Module | Purpose |
|---|---|
| `Synchronizer` | main loop, init/reset/replay, snapshot writes, job entry points |
| `IMDB`, `IMDBCore`, `IMDBStateException` | in-memory database facade, core storage and validation, the corrupt-picture exception |
| `IDMSFileManager`, `SamFileManager` | input discovery/pruning; snapshot and journal I/O |
| `IDMSUpdate`, `SamUpdate` | an input record; a journal/propagation record |
| `IDMSDumpSynchronizer` | copy-then-diff for add files |
| `SamUpdatePropagator`, `SamClient`, `MockSamClient` | push to SAM; HTTP client; test double |
| `IDMSUpdateManager` + `IDMSInstitution`, `IDMSOrganization`, `IDMSPerson`, `IDMSRole`, `IDMSGroup`, `IDMSActiveGroup`, `IDMSGidAllocation`, `IDMSUserEmployment` (`IDMSCollaboration`, `IDMSPosition`) | IDMS → SAM record builders per LDAP type |
| `SamDataManager` + `Sam{Institution,Organization,User,Group,GroupTag,GidAllocation,ProjectGroup,UserEmployment,Collaboration,Position}Data` | per-type storage, normalization, id maps, tombstones |
| `SamCollaboratorExpiryData` | unused |
| `HpcMembers` | refcounted `all-hpc-users` membership |
| `ProjectGroupUpdater`, `LifecycleUpdater` | the two SAM → staging jobs |
| `UpdateStagingClient`, `StagingTransformers` | `ldapmodify` runner; SAM record → LDIF |
| `SamDataUtil`, `Misc`, `AcademicStatus`, `Constants(.pm/.rc)` | normalizers and dates; helpers; code map; defaults |
| `SamUpdateDebugLog` | optional per-record disposition log |
| `TestData`, `Test*Data`, `*.t`, `test.cf` | fixtures and tests |

Repo documents worth reading next: `Overview.md` (containers and env vars),
`DatabaseReinitialization.md`, `Troubleshooting.md`, `LoggedInconsistencies.md`,
`support_notes` (runbook), `PodmanNotes.md`, `ucarldap/LDIF_Log_Processing_README.md`
(the file lifecycle), `sam-ldap-transformer/tbin/test-0*.sh` (before/after examples of
LDIF becoming JSON).

---

## Appendix A — sanitization note

The fixtures under `ucarldap/scripts/test/testdata/` and
`sam-ldap-transformer/tbin/` contain real people's names, emails, phone numbers,
upids and uids. Every sample in this document uses invented values. Do not paste
fixture excerpts into issues or shared documents.

## Appendix B — what was not verified

Nothing was executed. Rows marked "verify" in §4 rest on reading the code; the
strongest claims (N1, N15, 13's per-second firing, the `activeCollaborator`
serialization) each have a one-command check named in their row or in §4.4.

## Appendix C — issue bodies, ready to file (NCAR/sam-ldap-syncd)

Each block is a title line followed by a body. File the §4.1 set first.

**N1 — `SamGroupData::filterTags` returns the manager object, not the tag list**
`lib/SamGroupData.pm:369-382` ends by returning `$self` (the manager) instead of
`@adHocTags`. `IDMSGroup.pm:157` stores the result as `tags`. Deep-copying a manager
that holds normalizer code refs fails with "Can't store CODE items", which the main
loop treats as a retryable error: 5 minute pause, same file, forever. The shipped
`SamGroupData.t:117-120` fails against this code. Introduced in 5cb852d. Fix: return
the array. Please confirm in prod logs whether the message appears; if the copy does
not fail on your Perl, the fallback effect is that `isUpdatable` returns false for
every tagged group and ad-hoc group sync stops.

**13 — prod cron "disable" has no effect; six-field entries fire every second**
`prod/.env:69-74` sets `SYNCD_LIFECYCLE_CRON_DFLT` / `SYNCD_PROJECTS_CRON_DFLT`.
`bin/syncd:459-482` reads the env vars `SYNCD_LIFECYCLE_CRON` / `SYNCD_PROJECTS_CRON`
and takes defaults from `lib/Constants.rc` inside the image, so the `.env` lines are
ignored and the jobs run on the shipped schedule. Schedule::Cron's sixth field is
seconds, so `0 22 * * * *` fires every second of 22:00 (a forked child each time).
Fix: set the non-`_DFLT` names in `.env`, and make the sixth field `0` in
`Constants.rc` (or drop it). Note the pin, had it worked, would have re-enabled the
jobs in December.

**N4 — cron jobs run against the IMDB as it was at daemon start**
`bin/syncd:429` runs the scheduler with `detach=>1` and each job is forked from it
(`:580-591`), so `syncProjects`/`syncLifecycle` see the IMDB snapshot the daemon
started with. Project groups created after startup diff as "new" every run and are
pushed as `changetype: add`, which fails with "Already exists" once the entry exists,
so their membership never syncs. The detached scheduler also redirects its stdio to
`/dev/null`, hiding these errors. Fix: have `cronDispatcher` only touch the trigger
files (`.trigger-projectGroup-update`, `.trigger-lifecycle-update`) so the main loop
runs the job on live data.

**15 — a rejected SAM update is dropped permanently**
The IMDB is updated before the PUT (`lib/Synchronizer.pm:594-617`). When SAM answers
3xx/4xx/500 the propagator re-throws (`lib/SamUpdatePropagator.pm:104-118`), the loop
sleeps 300 s and re-applies the same file, which now compares as a no-op. The next
snapshot write persists the unsent record and truncates the journal; a later full
dump diffs against the already-updated IMDB and does not resend. Fix: on propagate
failure restore the previous record (or raise `IMDBStateException`) before re-throwing.

**N14 — `all-hpc-users` loses members on every snapshot load**
`lib/SamProjectGroupData.pm:171-179` checks `$projectGroups->{'all-hpc-users'}` but the
map is keyed by gid, so the check never matches and `attachData` re-adds the empty
default record on every load, which runs `adjustMembers(old=full, new=[])` and
decrements every member's refcount. Users present in exactly one group drop out of
gid 78426 and that is pushed to staging. Fix: look up `$ALL_HPC_USERS_GID`. Check:
compare `.[1].projectGroup["78426"].upids | length` in `sam-data.json` across a restart.

**N6 — organizations with Fischer ids are re-allocated on every full dump**
`lib/IDMSOrganization.pm:77-80` allocates an `organizationId` for a non-numeric IDMS id
but does not record `idmsUniqueName` on the record, so `idmsOrgNameToId` never learns
it. Each add file allocates a new id and tombstones the previous one; positions
referencing the org cannot resolve it; a later `modify` raises `IMDBStateException`.
Regression from 5cc97ae. Fix: set `idmsUniqueName` in the allocation branch.

**N15 — main loop busy-polls instead of blocking on new files**
`FileUtil::SpoolDirectory::getFilenames` (sweet-pl5) blocks only when no file matches.
The add file always matches in steady state, so `SYNCD_LOOP_WAIT_SECS` never applies
and `runSyncToSamLoop` spins. Please check the container's CPU. Fix: block when no
file is newer than the checkpoint.

**3 / 2 / 8 / N2 — deferred deactivation never completes (chain)**
(3) `lib/LifecycleUpdater.pm:295` uses `$samClient`, never assigned in
`_serviceSamLifecycle`; the job dies after the collaborator pass.
(2) `lib/SamClient.pm:366` asks `_getPath` for key `deactivation`; the table at `:95`
registers `deactivate`; `_getPath` throws a BUG (`:389-393`).
(8) `SAM_USERLIFECYCLE_PENDINGDEACTIVATIONS_API_RPATH` ends in `/` and the hours are
appended via the bare-`?` "since" path (`SamClient.pm:215-221`), producing
`/pendingdeactivations/?24`; the server expects `/pendingdeactivations/24`, answers
404, and (N2) `SamClient.pm:515-523` retries 404 forever. Hours 1–9 also fail the
`^[1-9][0-9]+$` check.
Result: users SAM has stamped `users.deactivate` are never closed (143 pending in the
2026-09-24 snapshot). Fix all four together and add the missing `use strict`.

**N5 — main loop keeps a stale IMDB after an in-loop rebuild**
`lib/Synchronizer.pm:202` captures `$imdb` once; `_checkForReset` (`:403-407`) builds a
new one into `$self->{imdb}`. Subsequent dumps diff against the frozen object and the
snapshot write persists the invalidated state. Fix: re-read `$self->{imdb}` each pass.

**N17 — deleting an LDAP position/collaboration entry can purge the SAM user**
`lib/IDMSUserEmployment.pm:254-269` logs the delete as rejected but `:307-308` returns
`('delete', userBefore, user)`, which `SamDataManager::makeSamUpdate` (`:702-713`)
turns into a delete of the whole user; the propagator then requests a purge permit
and purges if SAM allows. `LoggedInconsistencies.md` advises exactly this LDAP
deletion. Fix: return a no-op (or an update) for employment deletes.

**9 — gidAllocation delete aborts the daemon**
`_propagateDelete` calls `purgePermit` for every type; `_getPath` has no gidAllocation
purge path and throws instead of returning undef (`lib/SamClient.pm:313-316,389-393`),
so the `return 1 if undefined` branch is unreachable and `isPurgeable()` is never
consulted. A gidAllocation absent from a dump aborts the daemon, and the delete is a
no-op so it recurs. Fix: consult `isPurgeable()` before the permit call.

**14 — snapshot load rejects an empty collection**
`lib/SamDataUtil.pm:290-305` requires every type map to be non-empty; an empty but
valid `groupTag` or `gidAllocation` list fails startup until a full rebuild. Fix:
require a hash reference only.

**6 — group tags never refreshed**
`lib/ProjectGroupUpdater.pm:161-162` fetches `$groupTags` but loads `$groupTag`
(undef). New access-branch tags never make groups updatable. Fix: use `$groupTags`.

**10 / N3 — staging LDIF queue overwrites and never clears**
`lib/UpdateStagingClient.pm:178-188` keys the queue by DN, so a group's member LDIF
overwrites its attribute LDIF; `:203-236` never deletes served entries, so every DN
queued in the main process is re-sent on each service (a stale `replace` can undo
newer directory state), the request count is doubled, and `return $rc` only leaves
the `try` block. Fix: queue as a list; delete on success; return the code.

**N7 — `$maxLen` vs `$maxlen` in tombstones**
`lib/SamOrganizationData.pm:435-437` and `lib/SamInstitutionData.pm:126` truncate to
`$maxLen` (undefined), so acronyms over 11 characters become `" --x"` and tombstoned
records collide. Fix the variable name and add `use strict`.

**N8 — `SAM_UPDATES_STUB` still sends DELETE**
`_put` honors the stub (`lib/SamClient.pm:458-460`) but `_delete` (`:480-503`) does not,
so stub mode purges real records. Fix: add the stub check to `_delete`.

**N10 — unknown usernames silently dropped from groups**
`lib/SamGroupData.pm:338-345` drops any member not yet in the IMDB (file ordering,
renames, case differences; the lookup is exact-case) until the next full dump. Fix:
keep unresolved names and match case-insensitively.

**1 / 7 — read-back after PUT is dead; fix together**
`lib/SamClient.pm` defines `getSAMObject` twice (`:236`, `:260`); the second wins and
ignores `$id`. `lib/SamUserData.pm:292,298` iterate `@{user->{...}}` (a bareword), so
`willSamModify` is always false and the read-back never runs, which is why SAM-assigned
position ids are never learned. Fixing 7 alone makes every user PUT with a new
affiliation hit 1 and abort. Fix both, add `use strict`.

**4 / 5 — incremental project-group fetch: delete rather than fix**
`since` is sent as a bare `?<ms>` (`lib/SamClient.pm:215-221`) and the watermark reads
`lastModifiedDate` where SAM sends `lastModified` (`lib/ProjectGroupUpdater.pm:179-181`).
Every fetch is full. Do not make it incremental: SAM's `lastModified` is the
project's modified time, which membership changes do not bump, so a working watermark
would miss them. Recommend removing the incremental path.

**N9 — string exceptions crash the catch block**
`lib/Synchronizer.pm:247` and `Misc::exceptionHasType` call `->isa` on the caught
value; a plain-string `die` beginning with a non-identifier character makes the catch
block die. Fix: guard with `blessed($ex)`.

**N16 / N11 / N12 / N13 / N18 / N19 — minor**
`SAM_USER` default lost (`bin/syncd:501-507`); torn last journal line blocks startup
(`lib/Misc.pm:140`); assorted typos and dead code (`lib/IMDB.pm:436`, `bin/syncd:631`,
`getUpdateFileType`, `--snapshot` without a daemon); global `@outArr`
(`lib/SamDataUtil.pm:269`); `_orderTypedIDMSRecs` reads absent fields
(`lib/IDMSDumpSynchronizer.pm:215-248`); the shipped test suite fails and is not run in
CI before deploys. One issue for the bundle is fine.
