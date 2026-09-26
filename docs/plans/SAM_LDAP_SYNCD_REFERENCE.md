# sam-ldap-syncd — reference overview

**Status:** reference, 2026-09-25. Code read from George's repos as cloned in
`legacy_sam/container_zoo/`, and every claim checked against the production stack on
sam-app.ucar.edu: the running image is `sam-ldap-syncd` commit 6c0fd35 (2026-08-27), and the
scripts inside all three production images are byte-identical to the checkouts in
`~swes/<repo>` on that host. The SAM end of the wire was checked on 2026-09-26 on
sam-tomcat.ucar.edu, the legacy SAM host (§2.5): its access and application logs cover
every request the daemon has made since 2026-07-31. Bugs in §4 carry a production-status
column.
**Companion:** `docs/plans/LDAP_SYNC_API.md` scopes serving this daemon from the new
SAM and holds the exact HTTP contract (§2 there); this document explains what the
daemon *is* and how it works, for a reader who knows SAM well and LDAP little.

Paths are relative to `legacy_sam/container_zoo/` unless they start with `java:`,
which means `legacy_sam/src/main/java/edu/ucar/cisl/sam/`. Line numbers are those of
sam-ldap-syncd 6c0fd35, sweet-pl5 5884e33, sam-ldap-transformer 51c06fd, sam-idms-ldap
ec86baf and ucarldap 59fd1a8, the commits deployed in production.

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
| **operational attributes** | system columns | Maintained by the server (`entryUUID`, `entryCSN`, `modifyTimestamp`, …). The transformer strips all of them except `modifyTimestamp`, and strips `objectClass` as well (`sam-ldap-transformer/transformer_rewrite.pl:103-116`). |

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
membership from the second (`transformer_rewrite.pl:130-137`). The seven DN patterns
are `@DN_PATTERNS` at `transformer_rewrite.pl:93-101` (the script's own help text lists
only six, omitting gidAllocations).

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
| `x-ucar-source` | who last wrote the value (`PDB`, `FI`, `AD`, `GOOGLE`, `EIP`, `SAM:<ts>`) | — |
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

All of these are defined in `ucarldap/schema/ucar.schema`. The production replica holds
about 121,000 `x-ucar-source` values: PDB (112,715), FI (5,784), AD (2,698), GOOGLE (58),
EIP (3) and **none** written by SAM (§3.6, §3.7).

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
  `<timestamp>Z-1-slapcat` (the `1` is the slapd database number, `MAIN_DBNUM`).
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
   │ syncrepl (live replication, RID 167 in prod, filter (objectClass=*))
   ▼
sam-idms-ldap ── writes ──▶ auditlog.d/  <dumpTs>Z-1-slapcat, auditlog.ldif (+ <ts>Z-auditlog.ldif
   │                                     segments, folded into <dumpTs>Z-combined-auditlog.ldif)
   │ shared volume /data/tomcat-sam/prod/sam-ldap-syncd on sam-app.ucar.edu, mounted at /var/data
   ▼
sam-ldap-transformer ── writes ──▶ syncd/  <dumpTs>Z-full.ldif, <dumpTs>Z-inc0.ldif (portable LDIF),
   │                                       <dumpTs>Z-add.jsl (full), <ts>Z-mod.jsl (changes)
   ▼
sam-ldap-syncd ── HTTP PUT/GET/DELETE ──▶  https://sam.ucar.edu (VIP) ─▶ Apache proxies
        │                                  (prod-staticweb14/15) ─▶ Tomcat on sam-tomcat.ucar.edu:8443
        │                                  /api/protected/admin/{ldapsync,*Purge*,userlifecycle}
        └── ldapmodify ──▶ fdbstage.ucar.edu (LDAP staging: project groups, collaborator end dates)
```

### 2.1 sam-idms-ldap: the live copy

**In plain terms.** An OpenLDAP server inside a container logs in to the central
directory and asks for a copy of everything it is allowed to see, then keeps the
connection open and receives each change as it happens. That is **syncrepl**
("refresh, then persist"; `ucarldap/opt-ldifs/13synccons.sh:103-109`). Every change
it receives is also appended to its own audit log, and that log is the raw material
for the next hop.

- **What it copies.** Originally a whitelist of nine object classes
  (`SYNCREPL_CONSUMER_FILTER`); that line was commented out on 2026-08-10 ("relax sync
  filter", `sam-idms-ldap/Dockerfile:40`), so the default `(objectClass=*)`
  (`13synccons.sh:55`) applies and the replica copies everything the `citldapsam`
  account may read. The narrowing to the seven folders happens in the transformer.
  The filter is baked into `slapd.d` when the config directory is first bootstrapped
  (`ucarldap/README.md:16-21`, `sam-idms-ldap/entrypoint.sh:59-63,79-82`), so a filter
  change only takes effect on a freshly initialized replica. Production was
  re-bootstrapped on 2026-08-27 and runs the relaxed filter: the live consumer config
  reads `type=refreshAndPersist filter=(objectClass=*) bindmethod=sasl saslmech=PLAIN
  authcid=citldapsam`, and the replica holds about 147,800 entries.
- **Startup.** On every start `main()` first writes a dump and rotates the audit log
  (`ucarldap/entrypoint.sh:126,182-191`). It reads the provider's `contextCSN` (a
  replication high-water mark) into `auditlog.d/contextCSN.start`
  (`sam-idms-ldap/entrypoint.sh:201-216`); the lookup tries the provider with SASL,
  then falls back to an anonymous read of `SYNCREPL_PROVIDER_PUBLIC_REPLICA_URL`, and
  retries every 5 s without a timeout. If the fresh dump is empty, it marks
  `auditlog.d/INITIALIZING`, starts slapd, polls every 30 s until the local
  `contextCSN` has reached the target, waits until the audit log has been unchanged
  across one 30 s sleep, stops slapd, writes a full dump, rotates, and restarts for
  normal service (`ucarldap/entrypoint.sh:102-237`). The transformer waits for
  `INITIALIZING` to disappear (`ucarldap/scripts/ldif_log_processor.sh:164-171`).
- **Dumps are written only at container start**; there is no periodic dump. In
  production the only dump set is `20260827171853Z`, from the 2026-08-27 start. The
  audit log grows continuously; it is rotated by the *transformer* on every cycle and by
  the ldap entrypoint on every start (`ucarldap/scripts/rotate_auditlog.sh:54-63`,
  `ldif_log_processor.sh:206`). Rotation hard-links the current file to
  `<ts>Z-auditlog.ldif`, where `<ts>` is when that segment started.
- **Config** (`sam-ldap-syncd/prod/.env`): `SYNCREPL_PROVIDER_URL=ldaps://fdb.ucar.edu`,
  `SYNCREPL_CONSUMER_RID=167`, `SYNCREPL_PROVIDER_AUTHCID=citldapsam` (the password file is
  `LDAP_AUTH_<authcid>` in the secrets dir, `sam-idms-ldap/entrypoint.sh:184-192`),
  `SYNCREPL_PROVIDER_PUBLIC_REPLICA_URL=ldap://public-ldap.cloud.ucar.edu`,
  `LDAP_TLS_VERIFY_CLIENT=never` (becomes syncrepl `tls_reqcert`), `LDAP_LOG_LEVEL=256`
  (slapd `-d 256`), `MAIN_DBNUM=1`, `AUDITLOG_DIR=/var/data/auditlog.d`. Host ports
  9389/9636 map to 389/636. The container has no `TZ` and logs in UTC.
- **Steady state.** The provider connection drops and reconnects a few dozen times a day
  (`do_syncrepl: rid=167 rc -1 retrying` in the container log) while changes keep
  arriving and `contextCSN` stays current; this is benign.

### 2.2 sam-ldap-transformer: from LDIF to JSON lines

**In plain terms.** A polling loop that watches the audit log, waits for it to go
quiet, rotates it, and converts each new piece of LDIF into one JSON object per line
that the daemon can consume without knowing any LDAP. It is a plug-in
(`sam-ldap-transformer/transformer_impl.rc`, 211 lines) into a generic driver from ucarldap
(`ucarldap/scripts/ldif_log_processor.sh`). Its spool directory is the daemon's
`SYNCD_DIR`, so the intermediate LDIF files sit next to the `.jsl` files.

**Stage 1, server LDIF → portable LDIF** (`transform_slapd_ldif`,
`transformer_impl.rc:133-151`; `transformer_rewrite.pl:93-117`): keep only DNs under the
seven folders, strip the operational attributes and `objectClass`, and for a full dump
reorder so parents precede children. A dump becomes `<dumpTs>Z-full.ldif`; an audit
segment becomes `<ts>Z-inc1.ldif`, which is hard-linked to `<ts>Z-mod.ldif` for stage 2
and then appended to the dump's `<dumpTs>Z-inc0.ldif`. The consumed audit segment is
appended to `<dumpTs>Z-combined-auditlog.ldif` and deleted
(`ldif_log_processor.sh:492-509,558-578`). In steady state `syncd/` therefore holds one
`-full.ldif`, one growing `-inc0.ldif`, one `-add.jsl` and the `-mod.jsl` files; the
`-inc1`, `-add.ldif` and `-mod.ldif` names are transient.

**Stage 2, portable LDIF → `.jsl`** (`process_output_ldif`, `transformer_impl.rc:185-206`;
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
| `uid=X,ou=accounts,ou=unix` (member values only) | `["user","X"]` |
| `x-ucar-organizationId=N,ou=allOrganizations` | `["organization","N"]` |
| `x-ucar-externalOrgId=N,ou=externalOrgs` | `["institution","N"]` |
| `ou=D,ou=gidAllocations` children | `["gidAllocations",<parent>,"D"]` |

Attribute maps (`%ATTR_MAPS`, `transformer_rewrite.pl:139-231`) rename LDAP attributes to
the JSON keys the daemon expects; anything unmapped is dropped silently. Keys in UPPER
CASE are "pseudo-attributes" the daemon must resolve itself (they need a lookup or a
merge): person `EMAIL`, `FORWARD_EMAIL`, `PHONE`; collaboration `START_DATE`, `END_DATE`,
`ACTIVE`, `MODIFIED`; position the same four plus `ORGANIZATION_ID`; group `TAG`;
activeGroup `USERNAME`; institution `STATE_NAME`; organization `ORGANIZATION_ID`,
`PARENT_ORG`; role `CONTACT_PERSON_UPID`, `TITLE`; gidAllocations `GID_RANGE`. Value
transformers turn member DNs into `["user","jdoe"]`, parent DNs into
`["organization","271"]` or `["institution","1171"]`, and the `upid:instId` form
Fischer Identity writes into just the institution id (`fixInstitutionId`, `:462-468`).

Representations: a **modify** is `{"modify":[objId,"replace","PHONE",[...], "add",
"END_DATE",[...]]}`; a modify touching only unmapped attributes produces no line; a
**rename** becomes a modify that replaces the key attribute; a **delete** is
`{"delete":[objId]}` with no payload, and deletes of `activeGroup` and
`gidAllocations` are dropped on purpose (`:433-441`).

**Cadence and handshake.** Poll every `PROCESSING_LOOP_SLEEP_SECS` (60 s) until the
audit log is non-empty, then wait until it has been unchanged for 10 s, rotate,
convert, and process `*-add.ldif`/`*-mod.ldif` in timestamp order
(`ldif_log_processor.sh:178-215,351-376,540-609`). A **reset** (the daemon touches
`SYNCD_RESET_FILE`) makes the driver rebuild its queue as "latest dump + all later
changes" (`ldif_log_processor.sh:217-229,386-400`), which produces a fresh `*-add.jsl`;
the transformer deletes the reset file only after that add file has been processed
successfully (`transformer_impl.rc:168-174,197-201`). A named pipe `.FIFO` in the spool
dir lets the daemon block efficiently; the transformer unblocks it after writing a
non-empty file (`transformer_rewrite.pl:328-330`). A file whose records all map to
nothing counts as success with no output and no unblock (`translate_file`,
`transformer_rewrite.pl:333-384`, commit 51c06fd).

### 2.3 sam-ldap-syncd

The subject of §3.

### 2.4 Deployment

- **Host** `sam-app.ucar.edu`, rootless podman as user `swes`. `sam-app/REPOS` gives the
  image build order and `sam-app/PROD_SERVICES` lists `amie-sam-mediator/prod` and
  `sam-ldap-sync/prod` (the directory is actually `sam-ldap-syncd`).
- **One compose file runs all three containers**: `sam-ldap-syncd/prod/docker-compose.yml`
  (`ldap`, `transformer`, `syncd`, plus profile-only helpers `ldapDebug`, `syncdadmin`,
  `syncdInit`, `syncdTest`, `syncdDump`, `syncdGendoc`). They share
  `/data/tomcat-sam/prod/sam-ldap-syncd` mounted at `/var/data` (`auditlog.d/`, `ldap/`,
  `slapd.d/`, `syncd/`, `.reset`, the daily logs, `reset.sh`, `README.txt`) and the
  read-only secrets tree `/data/tomcat-sam/secrets` at `/run/secrets`. Two ordinary bridge
  networks: `ldap` is alone on `prod_ldap`, `transformer` and `syncd` share `prod_syncd`;
  the containers exchange data only through the shared files. The syncd container runs
  `bin/syncd -vvvvv --redirect`; syncd and transformer have `TZ=America/Denver`.
- **Ownership.** Inside the containers the daemon runs as `tomcat-sam` (uid 303), slapd as
  `openldap`, and the entrypoints as root. Through rootless uid mapping the host sees
  `syncd/*.jsl`, `sam-data.json` and the logs as `tomcat-sam`; `auditlog.d/`, `ldap/` and
  `slapd.d/` as uid 100910; and the slapcat dumps, `combined-auditlog.ldif` and
  `syncd/*-inc0.ldif` as `swes` (container root). `README.txt` and `reset.sh` in the data
  dir describe the required ownership and how to wipe the state from a debug container.
- **Secrets.** The sweet entrypoint flattens `/run/secrets` into `SECRETS_DIR=/tmp/private/secrets`
  (`LDAP_AUTH_citldapsam`, `LDAPSTAGING_AUTH_citldapsam`, `sam.parm`) and its
  `load-runtime-parms` step ingests every `*.parm` file into the container's parameter
  store `PARM_DB=/tmp/parmdb`. That is where the SAM password lives: `/tmp/parmdb/SAM_AUTH_admin`
  (mode 600, plus `SAM_AUTH_{amie,dasg,dav,ldap,mssg,ssg}` from the same file). The daemon
  reads it through `Sweet::getParm` (environment first, then `PARM_DB`,
  `sweet-pl5/lib/Sweet.pm:260-273`); only the *staging* password is read as a file from
  `SECRETS_DIR` (`bin/syncd:525-538`).
- **Restart policy: none.** A user systemd unit exists (`prod/prod-sam-ldap-syncd.service`:
  `ExecStartPre=podman-compose down`, `ExecStart=service-start.sh`, `ExecStop=podman-compose
  stop`, `Restart=on-failure`). `service-start.sh` runs `opsmonitor.sh` once and then at
  `MONSCHED=01:00,05:00,09:00,13:00,17:00,21:00` through `sweet/bin/run-at`
  (`prod/service.env`, which also sets `EMAIL_ADDR`); `opsmonitor.sh` bounces the whole
  stack (`down; up -d`) when the syncd container is missing or any compose container is
  not "Up", and mails on a non-zero rc. In production the unit is **installed but disabled
  and inactive**: between 2026-08-14 13:42 and 2026-08-17 16:59 the combination of
  `service-start.sh:40-42` (exit on any non-zero opsmonitor rc, including the warning after
  a successful bounce) and `Restart=on-failure` restarted the stack in a loop (1,608 monitor
  runs, ~1,040 daemon starts per day in the syncd log), and commit 037ae81 (2026-08-17)
  left `opsmonitor.sh:67-69` with a bash syntax error, so it cannot run at all. There is no
  swes crontab and the containers have `RestartPolicy=no`. **A daemon that aborts stays
  down until someone notices.** `prod/notes` sketches a replacement `syncmon-*` service
  that has not been written.
- **Test** (`test/.env`): RID 166, `SAM_URL=https://test-sam.ucar.edu:443`, staging
  writes stubbed via `LDAPSTAGING_UPDATES_STUB=ldap-update-stub.log`; its unit uses
  `Restart=always` and `MONSCHED=01:00,06:00`.
- A second compose file, `sam-idms-ldap/prod/docker-compose.yml`, defines standalone
  `ldap` and `ldapx` services on the same host ports and RID 167, on an external network
  `shared-ldap` that does not exist on the host; it is a parked experiment.

### 2.5 The SAM end: sam-tomcat.ucar.edu

- **Host and service.** `sam.ucar.edu` is a VIP (128.117.225.232) in front of two Apache
  reverse proxies, `prod-staticweb14/15.ucar.edu` (128.117.224.190/.191), which forward to
  Tomcat's only connector, `sam-tomcat.ucar.edu:8443` (APR, HTTPS). Tomcat 9.0.58
  (Ubuntu), OpenJDK 11, `-Duser.timezone=America/Denver`, `CATALINA_BASE=/tomcat/tomcat-sam`,
  unit `tomcat-sam.service` (Puppet-managed, `Restart=on-failure`, waits for the database
  `sam-sql.ucar.edu:3306`). The deployed WAR is SAM Web Application **2.0.4**, built
  2026-08-10 14:56 and installed at 09:28 that morning; Tomcat restarted on Jul 30,
  Aug 10, Aug 17, Aug 31 and Sep 14 without the daemon ever receiving a 404 (§4.2, N2).
- **Logs.** Tomcat writes to FIFOs in `/var/log/tomcat/`; syslog-ng copies them to
  `/tomcat/tomcat-sam/logs/access.log` (logrotate daily to `access.log-YYYYMMDD.gz`, about
  30 days on disk; the NetApp `/tomcat/.snapshot/weekly.*` copies reach back to 2026-08-01)
  and `catalina.out` (rotated at each restart, kept). Logback writes `sam.log` (30 days),
  `sam-xras-actions.log` and `sam-login.log`, and its EMAIL appender **mails every ERROR
  line to sweg-notify@ucar.edu**, so each rejected PUT is also a mail. All are readable by
  group `tomcat-sam`; `var/sam.complete.properties` (database credentials) is not.
- **Reading the daemon in the access log.** The pattern is
  `%{x-forwarded-for}i %h %l %u %t "%m %U%q %H" %s %b %D "%{User-Agent}i"`: the first
  column is the real client (`128.117.177.140` = sam-app), `%h` the proxy, `%u` always
  `-` (the credential is not recorded), `%D` milliseconds. The daemon is `libwww-perl/6.52`.
  Its first request in each process is a 401 (LWP learns the realm once), answered with
  the same request plus credentials within 0–5 s: 26 such pairs between 2026-07-31 and
  2026-09-26, 14 of them heading a full download (`GET institution, organization, user,
  projectGroup, groupTag, group, gidAllocation`, in that order, in under a minute). Those
  14 date the rebuilds from SAM: Aug 10 15:00 and 15:16 (the 5cb852d deploy), Aug 11 14:25
  and 14:31, Aug 17 21:11–21:51 (eight starts of the restart loop; six `user` downloads
  cut off by the client at 11–14 MB, logged 200 with a `ClientAbortException` in
  `sam.log`), Aug 27 10:07 and 11:18 (the 6c0fd35 deploy; the second is the running
  process).
- **What 57 days of traffic look like.** 14,233 family requests: `PUT user` 13,566,
  `PUT group` 458, `PUT institution` 55, `PUT organization` 50, 11 `userPurgePermit`
  reads, **no DELETE of any kind**, **no `userlifecycle/*` request**, no `projectGroup`
  or `groupTag` read outside the 14 startups, one `ldapsync/status` (a curl on Sep 3,
  answered 500). A quiet day is 8–62 requests; the add-file replay of Aug 10 was 6,329
  PUTs in 27 minutes at up to 347 per minute. Legacy answers a `PUT user` in 151 ms on
  average, the `user` collection (24.3 MB) in 30–35 s, `projectGroup` (1.03 MB) in 10–14 s.
  The full table and the error breakdown are in `LDAP_SYNC_API.md` §1 and §2.5.

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
| `-S`, `--snapshot` | touch `.trigger-snapshot`; with a daemon running exit 0; with no daemon, take the lock, run the initializer and exit, which rewrites the dump only if the journal had entries or a reset occurred and otherwise leaves the trigger for the next daemon |
| `-d`, `--display-dump` | show dump metadata; `-v` adds the dump, `-vv` the log |
| `-t`, `--test-connections` | test SAM and staging. The exit code reflects only the staging test: `SamClient::testConnection` puts its `return 1` inside a Try::Tiny catch block (`lib/SamClient.pm:183-191`), so the SAM half always reports success |
| `-s`, `--show-config`, `-h` | print and exit |

The staging client is constructed before the `-d`/`-t`/`-s` branches (`:359-381`) and
throws a BUG if the staging url, user or password file is missing.

Parameters come from the environment; each default is a `_DFLT` line in the image's
`lib/Constants.rc` (`processParmDefs`, `bin/syncd:459-482`: the default is read from the
constants file and overridden only by the environment variable of the **same name without
`_DFLT`**). Setting a `_DFLT` name in `.env` therefore does nothing (bug 13), and the
`CONSTANTS=` line in `.env` is inert too (`lib/Constants.pm:9-10` locates the `.rc` file
next to itself).

| Env var | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `/var/data` | logs; parent of a relative `SYNCD_DIR` |
| `SYNCD_DIR` | `syncd` | working dir; the process `chdir`s here |
| `SYNCD_LOOP_WAIT_SECS` | 60 | intended block time in the main loop (never applies, bug N15) |
| `SYNCD_PROJECTS_CRON` | `0 4,6,8,10,12,14,16,18 * * * *` | project-group push schedule |
| `SYNCD_LIFECYCLE_CRON` | `0 22 * * * *` | lifecycle push schedule |
| `SYNCD_USER_DEACTIVATION_GRACE_PERIOD_HOURS` | 24 | passed to SAM's pending-deactivations query |
| `SYNCD_RESET_FILE` | none | transformer reset flag; prod `/var/data/.reset` |
| `SYNCD_SAMUPDATE_DEBUG_LOG` | none | per-update disposition log, written only if the file already exists; prod `samupdate-dbg.log` |
| `SAM_URL`, `SAM_USER` | `https://sam.ucar.edu`, none | prod `https://sam.ucar.edu:443`, `admin`. `SAM_USER` must be set in env; the constants-file value is lost (bug N16) |
| `SAM_HTTP_LOG_LEVEL_MAX` | 6 | verbosity cap for the HTTP client; prod 4 |
| `SAM_RETRY_WAIT_DFLT` / `SAM_RETRY_WAIT_MAX` | 30 / 300 s | HTTP retry sleep and the cap applied to a server `Retry-After` (constants `SAM_RETRY_WAIT_TIME_DFLT/_MAX`) |
| `SAM_UPDATES_STUB` | none | append PUTs to a file instead of sending (DELETEs still go out, bug N8) |
| `LDAPSTAGING_URL`, `LDAPSTAGING_USER` | none | prod `ldaps://fdbstage.ucar.edu`, `citldapsam` |
| `LDAPSTAGING_UPDATES_STUB` | none | append LDIFs to a file instead of running `ldapmodify` |
| `SECRETS_DIR` | `/run/secrets` | password files `LDAPSTAGING_AUTH_<user>`; prod `/tmp/private/secrets` |
| `PARM_DB` | `/tmp/parmdb` (sweet) | parameter store holding `SAM_AUTH_<user>` (§2.4) |
| `HTTPS_CA_DIR` / `HTTPS_CA_FILE` | none | enables TLS verification; prod `/etc/ssl/certs` |
| `SERVICE` | none | service label used in log prefixes |

Constants-only (not overridable): `SYNCD_SAM_DATA=sam-data.json`,
`SYNCD_SAM_LOG=sam-log.jsl`, `SAM_API_BASE=api/protected/admin`, every `SAM_*_RPATH`.

Process facts: pid file `syncd.pid` held with `flock`, never removed or truncated
(`bin/syncd:621-648`); after initialization the cron scheduler is started with
`$cron->run(detach=>1)` (`:429`), a forked process that forks again for each job
(`:580-591`); exit codes 0 success, 1 bad argument, 2 busy. The production process tree is
`syncd` (pid 1), two `dated-file-writer` pipelines for `--redirect`, and the scheduler,
whose process title reads `Schedule::Cron MainLoop - next: <date> 22:00:00`.

### 3.2 State on disk (in `SYNCD_DIR`)

| File | What it is |
|---|---|
| `sam-data.json` | one-line JSON snapshot of the whole in-memory database: `[latestUpdateFile, {type→{key→record}}, {idMaps}]` (`lib/IMDBCore.pm:38-45`). Written atomically (`.tmp` then rename) after every `*-add.jsl`, after the first `*-mod.jsl` of a new UTC day, on `--snapshot`, on reset, at startup when the journal had entries, and when the picture is invalidated (`lib/Synchronizer.pm:360-364,409-410,417-425,601-615,693-705`). Writing it truncates the log. 23 MB in production |
| `sam-log.jsl` | the update journal since the last snapshot, one JSON array per line: `["addOrUpdate",type,key,{rec}]`, `["delete",type,key,{tombstone}]`, `["no-op",type,key,null]`, `["checkpoint","<input file>",null,null]` (`lib/SamUpdate.pm:99-140`). Unrelated to the transformer's `.jsl` files |
| `sam-data.json.bad` | the snapshot renamed aside when the daemon decides its picture is corrupt; forces a rebuild from SAM |
| `syncd.pid` | flock-based pid file |
| `.trigger-snapshot`, `.trigger-projectGroup-update`, `.trigger-lifecycle-update` | created by `-S`, `-p`, `-l`; the main loop polls for them |
| `../.reset` | the transformer reset flag (§2.2) |
| `2*-add.jsl`, `2*-mod.jsl` | input. When a new add file arrives everything older is **deleted** on the next directory scan (`lib/IDMSFileManager.pm:159-163`). On the first-mod-of-a-new-day snapshot the daemon tries to concatenate the applied mod files into the newest one (`consolidateUpdateFiles`, `:205-255`); that routine has never succeeded (bug N20), so every mod file since the last add file is retained: 1,212 of them plus 30 zero-byte `*-mod.jsl.new` leftovers in production on 2026-09-25. Nothing is archived |
| `samupdate-dbg.log` | the optional disposition log (§3.10); 36 MB in production |
| `$DATA_DIR/log-YYYY-MM-DD-syncd.{o,e}` | daemon logs, rolled daily by `dated-file-writer`, never pruned by the software (July's logs were moved to `oldlogs/` by hand) |

**Restart.** If either the snapshot or the log is missing, the daemon unlinks both,
downloads every collection from SAM, writes a fresh snapshot with an empty checkpoint,
and waits for a new full dump from the transformer (`lib/Synchronizer.pm:379-412,485-503`).
Otherwise it loads the snapshot, replays the log, and re-applies any input file newer
than the last checkpoint, including the one in progress when it died (`:533-553`, then
`getUpdateFilenames` in the main loop `:234-235`). Records are full-state and idempotent,
so a replay cannot double-apply to SAM.

### 3.3 The in-memory database

The daemon keeps a complete copy of what SAM should contain, called the IMDB, so
that a fragmentary LDAP change ("this person's phone changed") can be turned into a
complete record to PUT. One manager per type, all singletons (`lib/SamDataManager.pm:141-161`;
registration order `lib/IMDB.pm:457-475`):

| SAM type | Key | Extra id maps | From SAM / to SAM / purge | Prod count 2026-09-25 |
|---|---|---|---|---|
| institution | `institutionId` | — | yes / yes / yes | 1,394 |
| organization | `organizationId` | `idmsOrgNameToId` (+`NEXT_ID`), `orgAcronymToId` | yes / yes / yes | 401 (all < 1000) |
| user | `unixUid` | `usernameUpidToId` (username and upid share one map) | yes / yes / yes | 28,565 (72 role logins) |
| collaboration, position | `upid:<instId>` or `upid:<idmsUniqueName>`, stored **inside** the user record | position: `positionIdmsUniqueNameToPositionId` | travel with the user | 12,543 positions mapped |
| group | `posixGid` | `groupKeyToId` | yes / yes / yes | 12,165 (4,352 tagged) |
| gidAllocation | `startGid` | — | yes / yes / no | 1 (99000–99999) |
| projectGroup | `posixGid` (incl. 78426) | `hpcMembersToRefcnt` (`upids`, `rolenames` refcount maps) | yes / no / no | 5,833 |
| groupTag | `name` | — | yes / no / no | 5 |

Record shapes as they sit in the snapshot: user `active, chargingExempt, collaborations[],
deleted, emails[{email,primary}], firstname, institutionIds[], lastname, locked, nickname,
orgIds[], phones[{extPhoneType,phoneNumber}], positions[], preferredName, typeOfLogin,
unixUid, upid, userName`; collaboration `collaborationId, endDate, institutionId,
startDate, upid`; position `endDate, organizationId, positionId, startDate, upid`; group
`active, key, name, posixGid, rolenames[], tags[], upids[], usernames[]`; projectGroup the
same minus `usernames` plus `lastModified`; institution `acronym, city, country, deleted,
institutionId, institutionType, name, state, zip`; organization `acronym, active,
deleted, level, levelCode, name, organizationId, parentOrgAcronym`.

Applying a change (`lib/IDMSUpdateManager.pm:331-388,425-512`): for an add, start from
defaults, overlay the existing record, apply the simple attributes, run the per-type
"modifiers" for the pseudo-attributes; for a modify, apply the modifiers to the existing
record in place; then `retainSamOnlyData`, normalize, store. A modify for an unknown key
raises the "my picture is corrupt" exception (`IMDBStateException`, `:369-375`), which
renames the snapshot aside and rebuilds from SAM (§3.4).

A **full dump** is handled by copy-then-diff (`lib/IDMSDumpSynchronizer.pm:109-174`):
deep-copy the current records, apply the whole dump to the live IMDB without sending
anything, then for each uploadable type in registration order (institution, organization,
user, group, gidAllocation) push adds and updates for records that differ, and deletes for
records missing from the dump, deletes in reverse dependency order within the type. A dump
with suspiciously few records fails a per-type sanity check keyed by IDMS type (`:57-67`):
person ≥ 5000, collaboration ≥ 5000, position ≥ 5000, group ≥ 1000, activeGroup ≥ 1000,
institution ≥ 500, organization ≥ 100, role ≥ 10, gidAllocations ≥ 1. A failure is a BUG
exception (`:289-293`) that **aborts the daemon**, and because the check runs per type
during the apply phase, earlier types have already been applied to the live IMDB.

### 3.4 Reading IDMS changes

**Discovery.** Files matching `^20[0-9].*-(add|mod).jsl$`, sorted by name (a
timestamp). The daemon walks back to the newest add file and takes it plus every
later mod file newer than the checkpoint (`lib/IDMSFileManager.pm:79-80,129-165`). No add
file at all forces a transformer reset (`lib/Synchronizer.pm:236-238`).

**Main loop** (`lib/Synchronizer.pm:195-271`): reset check, transformer sync, snapshot
trigger, project/lifecycle triggers, collect files, apply. An add file goes to the
dump synchronizer; a mod file is applied one record at a time and each resulting
change is pushed immediately. On an exception: BUG or INPUT types abort the daemon
(`:248-251`; the "ABORTING on " format string has no `%s`, so the exception itself is not
printed); the corrupt-picture exception invalidates the IMDB and then, unless `-a` is set,
sleeps 300 s like any other error and rebuilds on the next pass (`:257-267`); anything
else sleeps 300 s and continues (`:266`).

**What "changed" means**: the normalized old and new records are compared with
`eq_deeply`; equal is a no-op, different is an addOrUpdate; a record that stops being
"updatable" becomes a delete; a record that is not updatable before or after is not even
journaled (`lib/SamDataManager.pm:681-782`).

**Per-type transformation rules** (IDMS → SAM):

| Type | Rule |
|---|---|
| institution | straight copy; `STATE_NAME` fills `state` if the code is missing (`lib/SamInstitutionData.pm:153-165`). Tombstone (`:105-138`): name cut to 80 + " INACTIVE", acronym cut to 40 + " --x", `nsfOrgCode` and `institutionType` dropped, `deleted=true` |
| organization | `ORGANIZATION_ID` matching `^[1-9][0-9]{0,9}$` is used as-is; any other string (a Workday/Fischer id) is looked up in `idmsOrgNameToId`, and if absent the daemon **allocates** `NEXT_ID` (1000, then max+1) (`lib/IDMSOrganization.pm:62-111`, `lib/SamOrganizationData.pm:170-204`). No such id exists in production yet. `PARENT_ORG` becomes `parentOrgAcronym` only if the parent is already known. Tombstone (`:415-450`): `active=false, deleted=true`, name cut to 90 + " INACTIVE", acronym cut to 15 + " --x", `level`, `levelCode`, `parentOrgAcronym` dropped |
| person → user | `typeOfLogin=user_login`; `EMAIL`/`FORWARD_EMAIL` become `emails[{email,primary}]` (primary first, deduplicated, `lib/SamUserData.pm:429-455`); `PHONE` "type: number" strings become `phones[]`; `chargingExempt` and `locked` forced false by `falseNormalizer` (`lib/SamUserData.pm:69,71`); `academicStatus` description mapped to code (`lib/IDMSPerson.pm:44-154`, `lib/AcademicStatus.pm:61-80`) |
| role → user | keyed by userName, `typeOfLogin=role_login`; names copied from the contact person; `TITLE` cut to 45 chars (`lib/IDMSRole.pm:77-120`) |
| collaboration, position | edit `user.collaborations[]` / `user.positions[]` and return the whole user as the changed record. The `ACTIVE`, `MODIFIED`, `START_DATE`, `END_DATE` shadow attributes derive `startDate`/`endDate` (active with a past end clears it; inactive with no end keeps an existing end, else uses MODIFIED). Same employer with non-overlapping dates appends a new row; an inverted range is fixed and logged (`lib/IDMSUserEmployment.pm:225-300`, `lib/SamUserEmploymentData.pm:332-420,502-541`). Collaborations without institution or start date are dropped (`lib/SamCollaborationData.pm:188-191`). An LDAP **delete** of a collaboration or position entry is logged as rejected "to preserve historical data" but still returned as a whole-user delete (bug N17, `lib/IDMSUserEmployment.pm:254-269,307-308`) |
| interim accounts | a person whose `userName` is missing or equals the upid string (a Fischer placeholder) is kept in the IMDB and journal but **not sent** to SAM (`isPropagationDeferred`, `lib/SamUserData.pm:252-264`; PR #1, generalized in 5cb852d). 42 such users in the production snapshot; the log line is "Deferring propagation of user addOrUpdate (key=N): userName matched upid - unclaimed account" |
| user tombstone | `active=0, deleted=true`, emails and phones cleared, open affiliations end-dated now (`lib/SamUserData.pm:388-412`) |
| group | `TAG` filtered to tags that are access branches (`filterTags`, `lib/SamGroupData.pm:369-382`); membership from `activeGroup` `USERNAME`; `normalize` resolves usernames to `upids[]` (user logins) and `rolenames[]` (role logins), dropping names it does not know with "SamGroupData::normalize: no user for username X" (`:322-357`). A group with no accessBranch tag, gid 1000 (`ncar`), gid 78426, or any project's gid is "not updatable" and never sent (`:238-261`); a group of which only the `activeGroup` object has been seen has `active` undefined and is deferred (`:301-309`). An `activeGroup` delete empties the membership of a known group and deletes a still-deferred one (`lib/IDMSActiveGroup.pm:144-163`) |
| gidAllocations | only `domain=projects`; `GID_RANGE "a:b"` → `startGid`/`endGid` (`lib/IDMSGidAllocation.pm:79-121`) |

**Dates** are normalized to epoch milliseconds UTC with the milliseconds zeroed
(`lib/SamDataUtil.pm:74-117`; commit 6c0fd35 made the fraction of a
`YYYYMMDDhhmmss.f` string always `000`). Accepted: epoch seconds or ms (12–13 digit values
are truncated to whole seconds), and `YYYYMMDDhhmmss[.f](Z|±hh[mm])`. A timestamp without a
zone is a BUG and aborts the daemon (`:111`); an unrecognized format is a plain `die`,
which the main loop treats as a 300 s pause and retry. Output to LDAP and the lifecycle
expiry use container-local time, which the base image pins to `America/Denver`
(`sweet/Dockerfile:42,84`).

### 3.5 Pushing to SAM

`lib/SamUpdatePropagator.pm:74-189` decides per change:

1. **no-op / checkpoint** → journal only (`:87-93`).
2. **deferred** (interim account, incomplete group) → journal, INFO "Deferring
   propagation", no HTTP (`:94-102`).
3. **addOrUpdate** → `PUT /ldapsync/<type>` with the full record. The read-back
   GET that was meant to capture SAM-assigned ids runs only when `willSamModify` is true,
   which it never is (bug 7, `:132-142`).
4. **delete** → `GET /<type>PurgePermit`; if `purgeable`, `DELETE /<type>Purge`,
   otherwise `PUT` the tombstone record (`:167-189`).

The HTTP rules (challenge-response Basic auth with realm `Realm`; 404 retried
forever with "temp SAM init failure" logged for the first four; 500s whose message says
the connection was refused, closed or reset retried; other 300–500 answers thrown; JSON
bodies required) are in `LDAP_SYNC_API.md` §2.1. A thrown answer reaches the main loop,
which logs "Pausing and continuing after exception", sleeps a literal 300 s
(`lib/Synchronizer.pm:266`) and replays from the last checkpoint. The consequence that
matters here: the IMDB is updated **before** the PUT (`:571-576`) and the journal is
appended only after success (`SamUpdatePropagator.pm:118`), so the replay compares the
record as a no-op and it is never retried (bug 15). The server side shows what those
answers were (`LDAP_SYNC_API.md` §2.5): in September 2026 two 400s (Sep 1, the "Upid N
matches username N" placeholder-account rejections tracked in the operations notes) and
three 500s (Sep 2, 18, 26, each `Synchronization UserOrganization object for user upid N
had unknown id: M`, a SAM position id the daemon holds that SAM has since replaced, bug
N21); in August 70 of those position-id 500s, mostly on Aug 9–10 under the earlier image,
and four institution 500s for an acronym longer than the 40-character column (bug N22).
Each rejected record is seen once per process, and again after the next rebuild from SAM.

### 3.6 Project groups → LDAP staging

**In plain terms.** Every SAM project is also a unix group (`project.unix_gid`).
SAM is the authority for who is on a project, so the daemon periodically asks SAM for
every project as a group (`GET /ldapsync/projectGroup`), compares each with the group
it last saw from LDAP, and sends the differences to the staging directory so that the
central directory's group membership follows SAM (`lib/ProjectGroupUpdater.pm:138-220`).

- SAM's response per project: `posixGid`, `key` (lowercase projcode), `name`,
  `active`, `upids` (active members on live allocations, lead and admin always),
  `rolenames`, `tags` (access branches, `exclude-from-google`, `auto-renewed-project`),
  `lastModified`. Server rules are in `LDAP_SYNC_API.md` P1a. Every fetch is a full
  fetch: the incremental watermark reads a `lastModifiedDate` key that SAM does not send
  (`:179-181`, bugs 4 and 5), and the group-tag refresh loads an undefined variable
  (`:161-162`, bug 6), so the tags stay as loaded at daemon start.
- A pseudo-group **`all-hpc-users`** (gid 78426) is computed locally as the
  reference-counted union of every active project group's members plus every active
  access-branch ad-hoc group's members (`lib/HpcMembers.pm:165-297`,
  `lib/SamProjectGroupData.pm:69-77,155-220`). It is never sent to SAM, only to staging.
  In the 2026-09-25 snapshot it has 4,886 upids and 44 role names.
- The comparison is against the **IMDB's LDAP-derived copy**, not a live LDAP
  query. Diffed attributes: `active`, `upids`, `rolenames`, `tags`
  (`lib/StagingTransformers.pm:58-69`). A new group produces an `add` LDIF (`objectClass
  x-ucar-uniqueGroup`, `gidNumber`, `uniqueIdentifier`, `cn`, `x-ucar-groupName`,
  `x-ucar-active`, `x-ucar-tag`, `x-ucar-source: SAM:<ts>`) with no members; a changed
  group produces up to two `modify` LDIFs, one for attributes and one for `uniqueMember`
  lines of the form `x-ucar-upid=N,ou=allPeople,…` or `uid=R,ou=serviceAccounts,…`
  (`transformGroup`, `lib/StagingTransformers.pm:202-259`).
- Design note in the source: staging has high latency and no confirmation, so the
  job fires and forgets; the next run notices any remaining difference again
  (`lib/ProjectGroupUpdater.pm:33-38`).
- The production directory replica contains no group or person attribute with
  `x-ucar-source: SAM:*`, so no write from this job (or §3.7) has become visible in
  fdb. The SAM server explains why: the scheduled job has never fetched
  `ldapsync/projectGroup` (every read on record belongs to a daemon start, §2.5), so it
  dies before it has anything to send and fdbstage has received nothing from it.

### 3.7 Collaborator lifecycle → LDAP staging

**In plain terms.** An external collaborator's directory entry carries an end date,
after which the directory (and everything downstream) drops them. SAM knows better
than the directory how long a collaborator is still needed: as long as they hold a
live project membership, contract role, or data on disk. So the daemon asks SAM for
each collaborator's *nominal expiry* and, if it is later than the directory's end
date, pushes the later date to staging. It **only ever extends**
(`lib/LifecycleUpdater.pm:25-40,177-220`; `lib/StagingTransformers.pm:293-296`).

- SAM's answer (`GET /userlifecycle/collabexpiryupdates`): rows with `unixUid`,
  `activeCollaborator`, `nominalExpiry` (`yyyy-MM-dd`), among others. The server
  computes the expiry as the latest end over contracts (PI/monitor), led/admin
  projects and their allocations, current memberships' allocations, and disk holdings,
  each plus a 90-day grace (`LDAP_SYNC_API.md` P1d).
- The daemon skips a row when it is not active, the user is unknown, the user has no
  currently active collaboration in the IMDB, or the directory's end date is already
  later; otherwise it queues a modify replacing `x-ucar-endDate` (and
  `x-ucar-source: SAM:<ts>`) on the collaboration entry (`:189-210`).
- After the collaborator pass, the same job is meant to **finish deactivations**:
  ask SAM for users marked inactive for more than the grace period
  (`pendingdeactivations/{hours}`) and tell SAM to close each one (`deactivate/{user}`).
  That half dies at its first statement (bugs 3, 2, 8, N2, in a chain). The visible
  result in SAM: users stamped `users.deactivate` by `PUT ldapsync/user` since
  2026-08-10 accumulate while staying `active=1` — 140 of them on 2026-09-25, holding
  724 open `account_user` rows — and none is ever finished. The SAM server has never
  received a `collabexpiryupdates`, `pendingdeactivations` or `deactivate` request
  (§2.5), so neither half of this job has ever run to its first HTTP call.

### 3.8 The LDAP staging client

`lib/UpdateStagingClient.pm` does not use an LDAP library. It runs the `ldapmodify`
command as a subprocess with a SASL bind and a password file
(`ldapmodify -U $LDAPSTAGING_USER -y $SECRETS_DIR/LDAPSTAGING_AUTH_<user> -H $LDAPSTAGING_URL`,
`:91`; in production the file is under `/tmp/private/secrets`) and feeds the LDIF on
stdin (`:243-275`). LDIFs are queued in a hash keyed by DN (`:186-187`), so a group whose
attributes and members both change keeps only the second LDIF (bug 10); the queue is
never emptied after servicing, so every DN ever queued in a process is re-sent on each
later run and the "of Y" count is doubled (bug N3, `:203-236`). One `ldapmodify` runs per
DN. `--test-connections` runs one authenticated `ldapsearch` on `ou=allGroups` and looks
up three permission groups anonymously (`:136-165`). With `LDAPSTAGING_UPDATES_STUB`
set, the command and LDIF are appended to that file instead (`:281-301`). A sample modify:

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
a detached scheduler process that forks a child per firing. In production the scheduler
is alive (`Schedule::Cron MainLoop - next: … 22:00:00` in `podman top`) and runs the
`Constants.rc` schedule, because the "disable" lines in `prod/.env:72-74` set the `_DFLT`
names that nothing reads (bug 13). Three traps:

- Schedule::Cron 1.01 treats a sixth field as **seconds**, so `0 22 * * * *` means
  "every second of the 22:00 minute": 60 dispatches per slot (bug 13). Watched on
  2026-09-25, the scheduler's process title advanced `next: … 22:00:01`, `22:00:07`,
  `22:00:12`, `22:00:17` and a `Schedule::Cron Dispatched job 1` child appeared and exited
  within a few seconds each time, so each firing dies almost at once (bugs 3 and N4).
- The children hold a copy of the IMDB **as it was when the daemon started**; live
  updates never reach them (bug N4). Triggered runs (`-p`, `-l`, the trigger files)
  run in the main process against the live IMDB, so they behave differently.
- The detached scheduler points STDIN and STDOUT at `/dev/null`, joins STDERR to
  STDOUT and `chdir`s to `/` (`Schedule/Cron.pm:936-964`). Nothing the jobs print
  reaches `log-syncd.e`, and relative paths such as the trigger files resolve in `/`.
  The only trace of the jobs in the daemon log is the endpoint configuration dumped at
  startup. The SAM server settles what they do: nothing reaches it. In 57 days of access
  logs there is no `GET projectGroup` outside a daemon start and no
  `GET collabexpiryupdates` at all, so every child dies before its first request.

### 3.10 Logging and observability

- Levels (`sweet-pl5/lib/Vprint.pm:142-148`): 0 CRITICAL, 1 ERROR, 2 WARN, 3 NOTICE,
  4 INFO, 5 DEBUG, 6 TRACE. The table in the repo's `Troubleshooting.md:49-50` is
  off by one. Timestamps (`2026-09-25T19:59:31-06`) are added by `dated-file-writer`
  in the redirect path.
- A healthy start: "Dump/update file missing - resetting IMDB..." or "Reading data from
  dump file", "Loading data from SAM..." / "Loading <type> records from SAM...",
  "HttpClient GET: URL=…", "GET status: 200 OK", "Loaded N <type> records", "SAM data
  loaded, writing dump file...", "Loading logfile updates", "Initialization complete",
  "Cron pid=N", "Telling transformer to reset"; a restart with a non-empty journal adds
  "Rewriting dump file...", and `-S` adds "Snapshot requested, writing dump file...".
  Per mod file (DEBUG): "N update files
  found...", "Applying file X...", "File X processed", "Update files applied", and per
  record "PUT returned N". Per dump (INFO): "IDMSDumpSynchronizer propagating N <type>
  updates / deleting N obsolete / ignored N", then "Auditing alternate IDs". Staging
  runs (invisible in production, §3.9) would print " Loaded latest N projectGroup
  records" and "Successfully ran ldapmodify for X of Y".
- Trouble signs: "Deferring propagation of" (routine, placeholder accounts), "Pausing
  and continuing after exception:" followed by the exception dump ("PUT response:
  HTTP/1.1 400 Bad Request", "put(api/protected/admin/ldapsync/user,admin) failed", or
  the daily "Not a GLOB reference at …/IDMSFileManager.pm line 266" of bug N20),
  "ABORTING on ", "temp SAM init failure" (a 404 being retried), "IMDBStateException"
  followed by "INVALIDATING IMDB",
  "SamGroupData::normalize: no user for username X", "Delete obsolete LDAP Position".
- `SYNCD_SAMUPDATE_DEBUG_LOG` (only if the file pre-exists, `lib/SamUpdateDebugLog.pm:79-80`)
  writes, per input file, "Processing"/"Ending" lines and, per record, `add ->` or
  `modify ->` with `old:`/`new:` dumps tagged NULL UPDATE, MOOT DELETE, GOOD DELETE, NOT
  UPDATABLE, WAS UPDATABLE, NO CHANGE, CHANGED, CREATED, and a `DEACTIVATING!` marker
  (`lib/SamDataManager.pm:681-766`).
- No metrics, no status endpoint. Health = `podman ps`, `podman top`, the log, and
  `--display-dump`. The production daemon runs at ~99 % of one CPU continuously (bug N15).
- From the SAM side (§2.5): `/tomcat/tomcat-sam/logs/access.log*` gives every request
  with its status and timing, and `sam.log` gives the reason for every 4xx/5xx
  (`ApiController:handleUnexpectedException` followed by `SamExceptionHandler` with the
  stack). `grep libwww-perl access.log` is the quickest way to see whether the daemon is
  alive and pushing.

### 3.11 Tests and CI

24 `lib/*.t` files run in the order listed in `lib/test.cf`, via `./run-all-tests`
(a `runtests` docker image; `Testing-Support.md`), with fixtures in `lib/Test*Data.pm`
and a `MockSamClient`. A CircleCI config exists; there is no GitHub Actions workflow.
**Not covered:** `Synchronizer`, `SamClient`, `UpdateStagingClient`, `ProjectGroupUpdater`,
`LifecycleUpdater`, `IDMSUserEmployment` (its test was deleted in 05490d4, 2026-08-27),
`IDMSCollaboration`, `IDMSPosition`, `IDMSActiveGroup`, `SamCollaborationData`,
`IDMSGidAllocation`. One assertion is a no-op (`IDMSDumpSynchronizer.t:202`,
`ok(@$x = 0)`), and the six "Synchronizer exception bug fix" commits of 2026-08-17 landed
in production one at a time, which suggests the suite is not run before deploys.

### 3.12 Operating it

| Task | How |
|---|---|
| Force a full resync from LDAP | create `SYNCD_RESET_FILE`, or delete `sam-data.json`/`sam-log.jsl` and restart (`DatabaseReinitialization.md`; its claim that `--init-only -f` works while a daemon runs is wrong, the code exits BUSY) |
| Rebuild the picture from SAM | delete the snapshot or the log; the daemon downloads everything and waits for a new add file |
| Take a snapshot now | `syncd -S` |
| Force a project-group or lifecycle push | `syncd -p` / `syncd -l` (runs in the main process on live data) |
| Inspect the picture | `syncd -d -v` (jq output) |
| Check connectivity | `syncd -t`; compose profile `syncdTest` (exit code covers staging only, §3.1) |
| Wipe all state | `reset.sh` in the data dir, from `docker-compose run ldap --debug` |
| Known data repairs | `support_notes` (2026-07-17 runbook with manual SQL), `LoggedInconsistencies.md` (what the daemon logs and what to do; its advice to delete duplicate LDAP positions triggers bug N17) |
| Restart after an abort | by hand: `podman start prod_syncd_1`; nothing automatic exists (§2.4) |

---

## 4. Bugs to report upstream

All in NCAR/sam-ldap-syncd unless noted. `gh issue list` shows #2–#5 open (data and
operations topics) and PR #1 merged; none covers the rows below. Severity: **blocks**
(feature unusable or daemon aborts/hangs), **loss** (silent data loss), **wrong** (wrong
data or behavior), **cosmetic** (latent or minor). The **Prod** column says what the
production host shows: *observed* (the effect is visible there), *latent* (the code path
exists but the triggering data or event has not occurred), *invisible* (the effect happens
in the detached cron children, whose output is discarded), *observed (server)* (seen in
the SAM host's logs, §2.5).

**Root cause shared by several rows:** no module under `lib/` has `use strict` or
`use warnings`; the Dockerfile's `PERL5OPT="-Mv5.20 -Mstrict -Mwarnings … -Mautodie
-MCarp::Always"` (`Dockerfile:74`) only applies to `bin/syncd` because pragmas are
file-scoped. Misspelled or undeclared variables silently become package globals (rows 3,
6, 7, N3, N7, N13, N20; also `SamClient.pm:145,558-559`, `Synchronizer.pm:497,690`,
`SamUpdatePropagator.pm:134`).

### 4.1 Observed in production

| id | Title | Sev | Prod | Where | Effect | Fix |
|---|---|---|---|---|---|---|
| N15 | main loop polls continuously instead of blocking | wrong (load) | observed: syncd at ~99 % CPU since 2026-08-27, 29 CPU-days | `sweet-pl5/lib/FileUtil/SpoolDirectory.pm:310-351` | `getFilenames` blocks on the FIFO only when no matching file exists (`:324,335`); the add file is always present, so `SYNCD_LOOP_WAIT_SECS` never applies and `runSyncToSamLoop` spins | block when no file is newer than the checkpoint |
| N20 | daily mod-file consolidation always dies | wrong (leak) | observed: 1,212 `*-mod.jsl` and 30 `*-mod.jsl.new` retained; one 300 s pause per day | `lib/IDMSFileManager.pm:205-276`; caller `lib/Synchronizer.pm:629` | `_copyFile` does `print $outHandle $line` on a `FileUtil::Handle` object ("Not a GLOB reference at line 266"); `$inHandle` is undeclared; the catch runs `unlink $tmpfile` (undeclared, should be `$tmpname`) so the `.new` file stays. The snapshot has already been written, so nothing is lost, but no mod file is ever removed until the next add file | use the handle's print method (or `$outHandle->{fh}`), declare the variables, unlink `$tmpname` |
| 13 | prod `.env` cron "disable" is inert; sixth cron field is seconds | wrong (load) | observed: scheduler on the shipped 22:00 schedule, dispatching one short-lived child per second through the 22:00 minute (`podman top`, 2026-09-25); observed (server): none of those children has ever made a request to SAM | `bin/syncd:459-482,195-205`; `prod/.env:72-74`; `lib/Constants.rc:10-11` | `.env` sets `SYNCD_*_CRON_DFLT`, which nothing reads; Schedule::Cron 1.01 documents an optional sixth "seconds" column, so the shipped `0 22 * * * *` fires every second of 22:00 → 60 forked jobs, same at every even hour 04–18 for projects. Had the pin worked it would have silently re-enabled the jobs in December | set `SYNCD_*_CRON` in `.env`; make the sixth field `0` in `Constants.rc` |
| 15 | a rejected PUT is lost until the next rebuild from SAM | loss | observed (server): two 400s and three 500s in September 2026, each seen once; the Aug 27 institution rejection recurred three minutes after each of that day's two rebuilds from SAM | `lib/Synchronizer.pm:568-579,266`; `lib/SamUpdatePropagator.pm:104-118` | the IMDB is updated before the PUT; after the 300 s pause the file replays as a no-op; the next snapshot persists the unsent state; a later full dump diffs against it and does not resend; only a rebuild from SAM (snapshot or journal missing) resends it | restore the old record (or invalidate the IMDB) before re-throwing |
| N4 | cron children run on the IMDB as of daemon start | wrong | invisible; observed (server): no child has reached SAM, so they die even before the stale data would matter | `bin/syncd:416-431,580-591`; `Schedule/Cron.pm:936-964` | groups created after startup are pushed as `changetype: add` forever (fails "Already exists"), so their membership never syncs; lifecycle ignores collaborations added since startup; the detached scheduler discards its output and runs in `/` | have `cronDispatcher` only touch the trigger files so the main process does the work |

### 4.2 The deactivation chain (each fix exposes the next)

| id | Title | Sev | Prod | Where | Effect | Fix |
|---|---|---|---|---|---|---|
| 3 | `$samClient` undefined in `_serviceSamLifecycle` | blocks | observed indirectly: 140 users pending deactivation since 2026-08-10, none finished | `lib/LifecycleUpdater.pm:289-305` | dies after the collaborator pass; pending deactivations never finish; IDMS-inactive users stay active in SAM | `my $samClient = $self->{'samClient'};` |
| 2 | path key `'deactivation'` vs registered `'deactivate'` | blocks | latent | `lib/SamClient.pm:367` vs `:95`; throws at `:390-393` | BUG exception once row 3 is fixed (in a cron child it kills the child; from a trigger it aborts the daemon) | use `'deactivate'` |
| 8 | grace hours appended as `?24` instead of `/24` | blocks | latent | `lib/SamClient.pm:216-217`; `lib/Constants.rc:37`; `lib/LifecycleUpdater.pm:295` | requests `/pendingdeactivations/?24`, a 404, retried forever (N2); 1–9 hour grace fails the `^[1-9][0-9]+$` check | build the path with the hours as a segment |
| N2 | 404 retried forever | blocks | latent | `lib/SamClient.pm:515-523` | any real 404 hangs the caller silently after four log lines (30 s between tries, `Retry-After` honored up to 300 s) | cap retries, then `throwError(404, …)` |

### 4.3 Other defects

| id | Title | Sev | Prod | Where | Effect | Fix |
|---|---|---|---|---|---|---|
| N5 | main loop keeps a stale IMDB object after a rebuild | loss | latent (last invalidation 2026-08-17) | `lib/Synchronizer.pm:202` vs `:400-407` | after the corrupt-picture path, `_checkForReset` builds a new IMDB into `$self->{imdb}` but the loop keeps the old one; the next dump diffs against frozen data; the snapshot write persists the invalidated state | re-read `$self->{imdb}` each iteration |
| N17 | deleting a position or collaboration entry in LDAP can purge the user | loss | latent | `lib/IDMSUserEmployment.pm:254-269,307-308`; `lib/SamDataManager.pm:702-712` | the delete is logged "rejected" but still returned as a delete of the **whole user**; the propagator asks SAM for a purge permit and purges if allowed, else PUTs the unchanged user; the journal records a user delete, so a replay removes the user from the IMDB. `LoggedInconsistencies.md` recommends exactly this LDAP deletion | return a no-op for employment deletes |
| N14 | `all-hpc-users` erodes on every restart | wrong | invisible (needs a restart to measure; 4,886 upids in the current snapshot) | `lib/SamProjectGroupData.pm:94,171-179`; `lib/HpcMembers.pm:192-205,265-266` | the pseudo-group is looked up by name in a map keyed by gid, so every snapshot load re-adds an empty default and decrements every member's refcount; users in exactly one group drop out, and that is pushed to staging | look up by `$ALL_HPC_USERS_GID` |
| N6 | Fischer (hex-id) organizations are re-allocated on every full dump | wrong | latent (no such organization exists yet; `idmsOrgNameToId` holds only `NEXT_ID`) | `lib/IDMSOrganization.pm:77-83` | `idmsUniqueName` is set only on the non-allocating branch, so the map never learns an allocated org; each dump allocates a new `organizationId` and tombstones the old; positions cannot resolve the org; a later modify raises the corrupt-picture exception. Regression from 5cc97ae (2026-06-26) | also set `idmsUniqueName` in the allocation branch |
| 9 | `purgePermit` "no path → purgeable" branch unreachable; `isPurgeable()` never consulted | blocks (latent) | latent | `lib/SamClient.pm:313-316,390-393`; `lib/SamUpdatePropagator.pm:179`; `lib/SamGidAllocationData.pm:75-78,95-103` | a gidAllocation missing from a dump → `_getPath` throws BUG → abort; the delete is a no-op so it recurs on every add file | `return unless isPurgeable()` before the permit call |
| 14 | snapshot load rejects any empty type map | blocks (restart) | latent (every collection is non-empty today; projectGroup always holds 78426) | `lib/SamDataUtil.pm:290-305`; `lib/SamDataManager.pm:796-803` | a legitimately empty `groupTag` or `gidAllocation` collection, or an empty id-map sub-hash, makes startup fail until a full rebuild | require a hash, not a non-empty one |
| 6 | `$groupTags` fetched, `$groupTag` loaded | wrong | invisible | `lib/ProjectGroupUpdater.pm:161-162` | tags are never refreshed after init; new access branches never make groups updatable | use `$groupTags` |
| 10 | staging queue keyed by DN | wrong | invisible | `lib/UpdateStagingClient.pm:186-187`; `lib/StagingTransformers.pm:254-260` | a group whose attributes and membership both change loses the attribute LDIF | queue as a list |
| N3 | staging queue never cleared | wrong | invisible | `lib/UpdateStagingClient.pm:203-236` | every DN ever queued in the process is re-sent each service; a stale `replace` can undo newer directory state; the count is doubled; `return $rc` inside `try` only leaves the block and the outer `$rc` is an undeclared global | delete the entry on success |
| N7 | `$maxLen` vs `$maxlen` in tombstones | wrong | latent (no tombstoned orgs or institutions in the snapshot) | `lib/SamOrganizationData.pm:435-437`; `lib/SamInstitutionData.pm:124-126` | acronyms longer than 11 (org) or 36 (institution) chars become `" --x"`, so tombstoned records collide on acronym | fix the name |
| N8 | `SAM_UPDATES_STUB` does not stub DELETE | loss (test/dev) | latent (stub unset in prod) | `lib/SamClient.pm:334-350,458-460,480-503` | "non-destructive" stub mode still purges in SAM, and `purgePermit` logs to the stub but still sends the GET | add the stub check to `_delete` and `purge` |
| N10 | group normalize drops unknown usernames | wrong (transient) | observed at every init ("no user for username X") | `lib/SamGroupData.pm:338-345`; `lib/SamUserData.pm:195-197` | members not yet in the IMDB (ordering, rename, case) are dropped until the next full dump; lookup is exact-case | keep unresolved names; compare case-insensitively |
| N21 | stale SAM position ids are sent until the next full reload | loss | observed (server): 73 × `PUT user` → 500 "Synchronization UserOrganization object for user upid N had unknown id: M" since 2026-08-01 (62 on Aug 10; one each on Sep 2, 18, 26) | `lib/SamUserEmploymentData.pm` (`positionIdmsUniqueNameToPositionId`), `lib/SamUpdatePropagator.pm` | the IMDB learns SAM-assigned `positionId`s only at a full load; when SAM replaces a `user_organization` row afterwards the daemon keeps PUTting the dead id, legacy 500s, and the whole user update is dropped (bug 15) | send `idmsUniqueName` and let SAM match, or drop the `positionId`s and retry once on that 500; server side, match tolerantly (`LDAP_SYNC_API.md` P1c) |
| N22 | live institution records are not length-checked | wrong | observed (server): 4 × `PUT institution` → 500 "Data too long for column 'acronym'" (Aug 18, 20, 27 ×2) | `lib/SamInstitutionData.pm` (`:105-138` truncates only the tombstone) | an acronym longer than 40 characters is sent as-is; the database rejects it and the record is dropped until the next rebuild, then rejected again | truncate to the column widths (40 acronym, 128 name) on the live path too; server side, answer 400 |
| 1 + 7 | `getSAMObject` defined twice (second drops `$id`); `user->` bareword makes `willSamModify` always false | cosmetic, **fix together** | latent | `lib/SamClient.pm:236,260`; `lib/SamUserData.pm:289,295` | the read-back after PUT never runs, so SAM-assigned position ids are never learned; fixing 7 alone turns every user PUT with a new affiliation into a BUG abort via 1 (`SamUpdatePropagator.pm:143-147`) | delete the second definition; use `$user` |
| 4 + 5 | `since` sent without a name; reads `lastModifiedDate` but SAM sends `lastModified` | cosmetic (perf) | invisible | `lib/SamClient.pm:215-221`; `lib/ProjectGroupUpdater.pm:179-181` | the watermark stays undef, so `since` is never sent and every project-group fetch is full. **Do not "fix"**: SAM's `lastModified` is the project's modified time, which membership changes do not bump, so a working watermark would miss them | delete the incremental logic |
| N9 | `$ex->isa` on plain-string exceptions | cosmetic (latent) | latent | `lib/Synchronizer.pm:247`; `lib/Misc.pm:182-197` | a `die` with a string starting with a non-identifier character kills the catch block itself | `blessed($ex) && $ex->isa(...)` |
| N16 | `SAM_USER` default lost | cosmetic | no effect (prod sets `SAM_USER=admin`) | `bin/syncd:177,494-507`; `lib/Constants.rc:17` | the parameter's undef default overwrites the constants value, so the env var is mandatory | read the constant as the default |
| N11 | a torn last line in `sam-log.jsl` blocks startup | cosmetic | latent | `lib/Misc.pm:140`; `lib/SamFileManager.pm:248` | a crash mid-append leaves an unparsable line and the daemon will not start | skip an unparsable final line |
| N18 | `_orderTypedIDMSRecs` reads fields that are not on the object | cosmetic | latent | `lib/IDMSDumpSynchronizer.pm:215-248` | the numeric-before-string ordering never happens (the fields live in `->{data}`) | read from `->data` |
| N12 | minor bundle | cosmetic | — | `lib/IMDB.pm:436` (`$latestFileUpdate` typo); `bin/syncd:631` (`throwSysException` does not exist); `:250` ("ABORTING on " lacks `%s`); pid file never truncated; `--snapshot` with no daemon writes no dump; `getUpdateFileType` never returns `mod`; `-t` ignores the SAM result; `Troubleshooting.md` level table off by one; `DatabaseReinitialization.md` vs BUSY exit | — | — |
| N13 | `numArrayNormalizer` shares a global `@outArr` | cosmetic (latent) | latent | `lib/SamDataUtil.pm:269` | harmless only because callers deep-copy | `my @outArr` |
| N19 | tests not run before deploy | process | — | `IDMSDumpSynchronizer.t:202`; six "Synchronizer exception bug fix" commits on 2026-08-17 | a no-op assertion; typo fixes landed in production one at a time | run `./run-all-tests` in CI on every push |

### 4.4 Candidates examined and set aside

- **`filterTags` returning the manager object**: refuted. `lib/SamGroupData.pm:369-382`
  ends with `return @adHocTags;` at 6c0fd35 and in the running image, was introduced that
  way in 5cb852d, and its test (`SamGroupData.t:117-120`) matches. Production groups carry
  plain string tags (`hpc` 4,342, `hpc-data` 2,288, `hpc-dev` 250) and no "Can't store
  CODE items" message appears in 48 days of logs.
- **gidAllocation re-PUT every dump** (SAM adds `gidAllocationId`/`creationTime`):
  refuted, `createSamData` merges the existing record so they compare equal
  (`lib/IDMSUpdateManager.pm:430-434`).
- **Tombstone `active:0` as integer**: harmless, the Java field is primitive
  `boolean` and Jackson coerces 0 (`java:service/idservice/model/domain/IdServiceUser.java:15`).
- **`activeCollaborator` key mismatch**: Jackson 2.x accepts an `is` getter returning
  boxed `Boolean`, so the property is `activeCollaborator` and matches the Perl.
  Whether the collaborator extension actually produces directory writes cannot be told
  from sam-app (§3.6 last bullet, Appendix B).
- **Container timezone**: the base image sets `TZ=America/Denver`
  (`sweet/Dockerfile:42,84`), so 23:59:59 local is Mountain as intended.
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
| full / inc0 / inc1 / combined-auditlog | the transformer's portable-LDIF forms of the dump, of all changes since the dump, of one audit segment, and the raw audit segments folded together (§2.2) |
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
| sam-tomcat | `sam-tomcat.ucar.edu`, the host running legacy Java SAM behind the `sam.ucar.edu` VIP and the `prod-staticweb14/15` Apache proxies (§2.5) |
| parmdb | the sweet base image's parameter store (`/tmp/parmdb`), loaded from `*.parm` secrets files at container start; holds `SAM_AUTH_<user>` |
| syncrepl | OpenLDAP replication: initial refresh then a persistent change stream |
| slapcat / audit log | full dump / change journal in LDIF (§1.5) |

Modules under `sam-ldap-syncd/lib/`:

| Module | Purpose |
|---|---|
| `Synchronizer` | main loop, init/reset/replay, snapshot writes, job entry points |
| `IMDB`, `IMDBCore`, `IMDBStateException` | in-memory database facade, core storage and validation, the corrupt-picture exception |
| `IDMSFileManager`, `SamFileManager` | input discovery/pruning/consolidation; snapshot and journal I/O |
| `IDMSUpdate`, `SamUpdate` | an input record; a journal/propagation record |
| `IDMSDumpSynchronizer` | copy-then-diff for add files, with the sanity thresholds |
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
`support_notes` (runbook), `PodmanNotes.md`, `prod/SERVICE_README.txt` and `prod/notes`
(the systemd unit and the unbuilt monitor), `ucarldap/LDIF_Log_Processing_README.md`
(the file lifecycle), `sam-ldap-transformer/tbin/test-0*.sh` (before/after examples of
LDIF becoming JSON), `sweet/Parmdb-Store.md` (how secrets reach the daemon).

---

## Appendix A — sanitization note

The fixtures under `ucarldap/scripts/test/testdata/` and
`sam-ldap-transformer/tbin/` contain real people's names, emails, phone numbers,
upids and uids, and so do the production logs and snapshot. Every sample in this
document uses invented values or counts. Do not paste fixture or log excerpts into
issues or shared documents.

## Appendix B — what cannot be seen from sam-app, and what sam-tomcat answered

Everything in §2–§4 was checked on the daemon host; the SAM host (§2.5) then answered
part of what was left:

- **SAM's side of the wire.** The 401 challenge and its `realm="Realm"` were observed
  live; the `errorMessage` envelope, the 400-versus-500 mapping and every rejection since
  2026-08-01 come from `sam.log` and the deployed classes; the `collabexpiryupdates` key
  set was introspected with the WAR's own Jackson. Still from the source only: the
  200-empty answer for an unknown entity, because authentication precedes routing and the
  admin password is not readable by either operator account.
- **What the staging jobs do before they die.** Answered: nothing reaches SAM (no job
  request in 57 days), so no `ldapmodify` was ever queued. fdbstage's logs are no longer
  needed for that question.
- **N14** (all-hpc-users erosion) still needs a daemon restart and a before/after count of
  `.[1].projectGroup["78426"].upids | length` in `sam-data.json`.
- **The pending-deactivation 404** (bug 8) is still inferred from the URL shape; the
  request has never been made.
- **The `admin` credentials row** (`api_credentials` + `ROLE_API_ADMIN`) needs the
  database on `sam-sql.ucar.edu`; the Tomcat host's properties file is readable only by
  the service account.

## Appendix C — issue bodies, ready to file (NCAR/sam-ldap-syncd)

Each block is a title line followed by a body. File the §4.1 set first.

**N15 — main loop busy-polls instead of blocking on new files**
`FileUtil::SpoolDirectory::getFilenames` (sweet-pl5, `:310-351`) blocks only when no file
matches. The add file always matches in steady state, so `SYNCD_LOOP_WAIT_SECS` never
applies and `runSyncToSamLoop` spins. Production `prod_syncd_1` has run at ~99 % of one
CPU since it started on 2026-08-27 (29 CPU-days by 2026-09-25). Fix: block when no file
is newer than the checkpoint.

**N20 — daily mod-file consolidation always fails with "Not a GLOB reference"**
`IDMSFileManager::_copyFile` (`lib/IDMSFileManager.pm:258-276`) writes with
`print $outHandle $line`, but `$outHandle` is a `FileUtil::Handle` object, so Perl dies
"Not a GLOB reference at …/IDMSFileManager.pm line 266". `consolidateUpdateFiles`
(`:205-255`) wraps this, and its catch block runs `unlink $tmpfile`, an undeclared
variable (the temp name is `$tmpname`), so the `<file>.new` output is left behind.
The routine runs once per UTC day after the snapshot write (`lib/Synchronizer.pm:629`),
throws, and the main loop logs "Pausing and continuing after exception" and sleeps 300 s.
Nothing is lost (the snapshot and checkpoint are already written), but no mod file is
ever removed until the next add file: production holds 1,212 `*-mod.jsl` and 30 empty
`*-mod.jsl.new` files from 2026-08-27 to 2026-09-25. Fix: use the handle's own write
method, declare `$inHandle`, unlink `$tmpname`, and add `use strict`.

**13 — prod cron "disable" has no effect; six-field entries fire every second**
`prod/.env:72-74` sets `SYNCD_LIFECYCLE_CRON_DFLT` / `SYNCD_PROJECTS_CRON_DFLT`.
`bin/syncd:459-482` reads the env vars `SYNCD_LIFECYCLE_CRON` / `SYNCD_PROJECTS_CRON`
and takes defaults from `lib/Constants.rc` inside the image, so the `.env` lines are
ignored and the jobs run on the shipped schedule. Schedule::Cron's optional sixth field
is seconds, so `0 22 * * * *` fires every second of 22:00: on the production host the
scheduler's process title advances `next: … 22:00:01`, `22:00:02`, … through the minute
and forks a `Dispatched job 1` child each time (observed 2026-09-25 with `podman top`). Fix: set the non-`_DFLT` names in `.env`, and make the
sixth field `0` in `Constants.rc` (or drop it). Note the pin, had it worked, would have
re-enabled the jobs in December.

**N4 — cron jobs run against the IMDB as it was at daemon start**
`bin/syncd:429` runs the scheduler with `detach=>1` and each job is forked from it
(`:580-591`), so `syncProjects`/`syncLifecycle` see the IMDB snapshot the daemon
started with. Project groups created after startup diff as "new" every run and are
pushed as `changetype: add`, which fails with "Already exists" once the entry exists,
so their membership never syncs. The detached scheduler also redirects its stdio to
`/dev/null` and `chdir`s to `/`, hiding these errors and mis-resolving relative paths.
Fix: have `cronDispatcher` only touch the trigger files (`.trigger-projectGroup-update`,
`.trigger-lifecycle-update`) so the main loop runs the job on live data.

**15 — a rejected SAM update is dropped permanently**
The IMDB is updated before the PUT (`lib/Synchronizer.pm:568-579`). When SAM answers
3xx/4xx/500 the propagator re-throws (`lib/SamUpdatePropagator.pm:104-118`), the loop
sleeps 300 s (`Synchronizer.pm:266`) and re-applies the same file, which now compares as
a no-op. The next snapshot write persists the unsent record and truncates the journal; a
later full dump diffs against the already-updated IMDB and does not resend. Production
drops a handful of records a month this way. Fix: on propagate failure restore the
previous record (or raise `IMDBStateException`) before re-throwing.

**N14 — `all-hpc-users` loses members on every snapshot load**
`lib/SamProjectGroupData.pm:171-179` checks `$projectGroups->{'all-hpc-users'}` but the
map is keyed by gid (`:94`), so the check never matches and `attachData` re-adds the
empty default record on every load, which runs `adjustMembers(old=full, new=[])` and
decrements every member's refcount (`lib/HpcMembers.pm:192-205`). Users present in
exactly one group drop out of gid 78426 and that is pushed to staging. Fix: look up
`$ALL_HPC_USERS_GID`. Check: compare `.[1].projectGroup["78426"].upids | length` in
`sam-data.json` across a restart.

**N6 — organizations with Fischer ids are re-allocated on every full dump**
`lib/IDMSOrganization.pm:77-79` allocates an `organizationId` for a non-numeric IDMS id
but does not record `idmsUniqueName` on the record (only `:83` does, on the other
branch), so `idmsOrgNameToId` never learns it. Each add file allocates a new id and
tombstones the previous one; positions referencing the org cannot resolve it; a later
`modify` raises `IMDBStateException`. Regression from 5cc97ae (2026-06-26). No such
organization has reached production yet. Fix: set `idmsUniqueName` in the allocation
branch.

**3 / 2 / 8 / N2 — deferred deactivation never completes (chain)**
(3) `lib/LifecycleUpdater.pm:295` uses `$samClient`, never assigned in
`_serviceSamLifecycle`; the job dies after the collaborator pass.
(2) `lib/SamClient.pm:367` asks `_getPath` for key `deactivation`; the table at `:95`
registers `deactivate`; `_getPath` throws a BUG (`:390-393`).
(8) `SAM_USERLIFECYCLE_PENDINGDEACTIVATIONS_API_RPATH` ends in `/` and the hours are
appended via the bare-`?` "since" path (`SamClient.pm:216-217`), producing
`/pendingdeactivations/?24`; the server expects `/pendingdeactivations/24`, answers
404, and (N2) `SamClient.pm:515-523` retries 404 forever. Hours 1–9 also fail the
`^[1-9][0-9]+$` check.
Result: users SAM has stamped `users.deactivate` are never closed (140 pending on
2026-09-25, accumulating since 2026-08-10). Fix all four together and add the missing
`use strict`.

**N5 — main loop keeps a stale IMDB after an in-loop rebuild**
`lib/Synchronizer.pm:202` captures `$imdb` once; `_checkForReset` (`:400-407`) builds a
new one into `$self->{imdb}`. Subsequent dumps diff against the frozen object and the
snapshot write persists the invalidated state. Fix: re-read `$self->{imdb}` each pass.

**N17 — deleting an LDAP position/collaboration entry can purge the SAM user**
`lib/IDMSUserEmployment.pm:254-269` logs the delete as rejected but `:307-308` returns
`('delete', userBefore, user)`, which `SamDataManager::makeSamUpdate` (`:702-712`)
turns into a delete of the whole user; the propagator then requests a purge permit
and purges if SAM allows. `LoggedInconsistencies.md` advises exactly this LDAP
deletion. Fix: return a no-op (or an update) for employment deletes.

**9 — gidAllocation delete aborts the daemon**
`_propagateDelete` calls `purgePermit` for every type; `_getPath` has no gidAllocation
purge path and throws instead of returning undef (`lib/SamClient.pm:313-316,390-393`),
so the `return 1 if undefined` branch is unreachable and `isPurgeable()` is never
consulted. A gidAllocation absent from a dump aborts the daemon, and the delete is a
no-op so it recurs. Fix: consult `isPurgeable()` before the permit call.

**14 — snapshot load rejects an empty collection**
`lib/SamDataUtil.pm:290-305` requires every type map (and every id-map sub-hash) to be
non-empty; an empty but valid `groupTag` or `gidAllocation` list fails startup until a
full rebuild. Fix: require a hash reference only.

**6 — group tags never refreshed**
`lib/ProjectGroupUpdater.pm:161-162` fetches `$groupTags` but loads `$groupTag`
(undef). New access-branch tags never make groups updatable. Fix: use `$groupTags`.

**10 / N3 — staging LDIF queue overwrites and never clears**
`lib/UpdateStagingClient.pm:186-187` keys the queue by DN, so a group's member LDIF
overwrites its attribute LDIF; `:203-236` never deletes served entries, so every DN
queued in the process is re-sent on each service (a stale `replace` can undo newer
directory state), the request count is doubled, and `return $rc` only leaves the
`try` block. Fix: queue as a list; delete on success; return the code.

**N7 — `$maxLen` vs `$maxlen` in tombstones**
`lib/SamOrganizationData.pm:435-437` and `lib/SamInstitutionData.pm:124-126` truncate to
`$maxLen` (undefined), so acronyms over 11 (organization) or 36 (institution) characters
become `" --x"` and tombstoned records collide. Fix the variable name and add `use strict`.

**N8 — `SAM_UPDATES_STUB` still sends DELETE**
`_put` honors the stub (`lib/SamClient.pm:458-460`) but `_delete` (`:480-503`) and
`purge` (`:334-350`) do not, so stub mode purges real records; `purgePermit` (`:317-319`)
logs to the stub and sends the GET anyway. Fix: add the stub check to `_delete`.

**N10 — unknown usernames silently dropped from groups**
`lib/SamGroupData.pm:338-345` drops any member not yet in the IMDB (file ordering,
renames, case differences; the lookup is exact-case) until the next full dump. Fix:
keep unresolved names and match case-insensitively.

**1 / 7 — read-back after PUT is dead; fix together**
`lib/SamClient.pm` defines `getSAMObject` twice (`:236`, `:260`); the second wins and
ignores `$id`. `lib/SamUserData.pm:289,295` iterate `@{user->{...}}` (a bareword), so
`willSamModify` is always false and the read-back never runs, which is why SAM-assigned
position ids are never learned. Fixing 7 alone makes every user PUT with a new
affiliation hit 1 and abort. Fix both, add `use strict`.

**4 / 5 — incremental project-group fetch: delete rather than fix**
`since` would be sent as a bare `?<ms>` (`lib/SamClient.pm:215-221`), but the watermark
reads `lastModifiedDate` where SAM sends `lastModified` (`lib/ProjectGroupUpdater.pm:179-181`),
so it stays undefined and every fetch is full. Do not make it incremental: SAM's
`lastModified` is the project's modified time, which membership changes do not bump,
so a working watermark would miss them. Recommend removing the incremental path.

**N9 — string exceptions crash the catch block**
`lib/Synchronizer.pm:247` and `Misc::exceptionHasType` (`lib/Misc.pm:182-197`) call
`->isa` on the caught value; a plain-string `die` beginning with a non-identifier
character makes the catch block die. Fix: guard with `blessed($ex)`.

**N21 — stale SAM position ids are re-sent until the next full reload**
The IMDB records SAM-assigned `positionId`s only when it loads `ldapsync/user`. When SAM
replaces a `user_organization` row afterwards, the next update for that person carries
the old id and legacy answers 500 "Synchronization UserOrganization object for user upid
N had unknown id: M"; the daemon drops the whole user update (bug 15). The SAM host's
`sam.log` shows 73 such rejections since 2026-08-01, 62 of them on 2026-08-10 and one
each on Sep 2, 18 and 26. Fix: send `idmsUniqueName` and let SAM match the row, or on
that 500 clear the position ids and retry once.

**N22 — institution acronym not truncated on the live path**
`SamInstitutionData` cuts the acronym to 40 only when building a tombstone; a live record
goes out unchanged and the database rejects anything longer ("Data too long for column
'acronym'", four 500s on 2026-08-18, 08-20 and twice on 08-27, once after each rebuild
from SAM). Fix: apply the column widths (40 acronym, 128 name) on the live path.

**N16 / N11 / N12 / N13 / N18 / N19 — minor**
`SAM_USER` default lost (`bin/syncd:494-507`); torn last journal line blocks startup
(`lib/Misc.pm:140`); assorted typos and dead code (`lib/IMDB.pm:436`, `bin/syncd:631`,
`bin/syncd:250` "ABORTING on " without `%s`, `getUpdateFileType`, `--snapshot` without a
daemon, `--test-connections` ignoring the SAM result); global `@outArr`
(`lib/SamDataUtil.pm:269`); `_orderTypedIDMSRecs` reads absent fields
(`lib/IDMSDumpSynchronizer.pm:215-248`); a no-op assertion and no test run in CI before
deploys. One issue for the bundle is fine.
