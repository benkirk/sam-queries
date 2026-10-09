# ldap-pipeline

A local copy of the identity pipeline that runs on sam-app.ucar.edu: **sam-idms-ldap**
(an OpenLDAP replica of the UCAR directory) → **sam-ldap-transformer** (audit log →
`.jsl` change files) → **sam-ldap-syncd** (pushes changes into SAM over
`/api/protected/admin/ldapsync/*`). It is built from the commits running in production
and configured like production, bugs included, so that SAMuel's port of those endpoints
can be tested against the real client before a cutover.

What the daemon does and why: `docs/plans/SAM_LDAP_SYNCD_REFERENCE.md`. What SAMuel has
to serve: `docs/plans/LDAP_SYNC_API.md`.

## The one rule: nothing leaves this testbed

Until `SAM_URL` points at our own webapp, every write is a line in a log file.
`bin/guard.sh` runs before `ldap` and `syncd` and refuses to start them unless:

| Target | Requirement |
|---|---|
| LDAP staging (prod: fdbstage.ucar.edu, reachable from a laptop) | `LDAPSTAGING_URL` names a `.invalid` host **and** `LDAPSTAGING_UPDATES_STUB` is set. Compose refuses to parse without the stub. |
| SAM | `SAM_UPDATES_STUB` is set, or `SAM_URL` is `host.docker.internal` / `localhost`. |
| Prod SAM (sam.ucar.edu) | reads only, and only with `SAM_READ_PROD=1`; never `syncdTest` (see below). |
| fdb.ucar.edu (the syncrepl provider) | the citldapsam password file exists and does not end in a newline (`ldapsearch -y` sends the whole file), so a bad copy never becomes a failed bind. |

Stubbed writes land in the spool directory as `sam-update-stub.log` and
`ldap-update-stub.log`, one `ISO8601 PUT to <path>:` line plus the JSON body per call.
That file is the production-shaped PUT stream the replay test in `LDAP_SYNC_API.md` §5
needs.

## Setup

**1. Clones.** The seven NCAR repos (sweet, sftp-server, sweet-pl5, ucarldap,
sam-idms-ldap, sam-ldap-transformer, sam-ldap-syncd) are private; this public repo holds
none of their code. `bin/build-images` reads them from `$ZOO_DIR`, by default
`legacy_sam/container_zoo` beside this checkout. It never checks anything out there: each
ref is `git archive`d into a scratch directory, so the clones can sit on any branch.

**2. Images.**

```bash
bin/build-images                       # all seven, the prod commits in pins
REF_SAM_LDAP_SYNCD=376b1a6 bin/build-images sam-ldap-syncd   # one repo at another ref
PIPELINE_TAG=pipeline-arm64 PIPELINE_PLATFORM=linux/arm64 bin/build-images  # native
```

Images are tagged `ghcr.io/ncar/<repo>:pipeline-local` and never pulled (`pull_policy:
never`); the real GHCR packages are private. Two deviations from a sam-app build, both
in the base layer only:

- **Platform.** Prod is amd64, and that is the default here (emulated on Apple silicon;
  the full build takes about 5 minutes). Both bases also publish arm64
  (`debian:bullseye-slim`, `osixia/openldap:1.5.0`), so a native build works: set
  `PIPELINE_TAG=pipeline-arm64` and `PIPELINE_PLATFORM=linux/arm64` in `.env`. The one
  x86-only piece is the AWS CLI that sweet vendors; for arm64 the script swaps in AWS's
  aarch64 zip and signature. The pipeline never runs it.
- **Debian archive.** Bullseye's security pool moved to archive.debian.org when its LTS
  ended, so sweet's `apt-get` fails as written. The script builds
  `debian:bullseye-slim-archive` with apt pointed there and passes it through sweet's
  existing `DEBIAN_QUALIFIER` build arg. Same package versions, different mirror.
  A rebuild on sam-app hits the same failure.

**3. Secrets.** `bin/init-secrets` creates `secrets/` (git-ignored, 0700) with the files
the testbed makes itself: the local slapd's three passwords and a dummy staging password.
Two real credentials are copied from sam-app. Both files belong to `tomcat-sam`
(uid 100302, inside swes's subuid range), so swes reads them through `podman unshare`,
which touches no container. Redirect rather than paste: the citldapsam file has no
trailing newline, so a terminal copy picks up the shell prompt.

```bash
ssh sam-app.ucar.edu "sudo -n -iu swes podman unshare cat /data/tomcat-sam/secrets/LDAP_AUTH_citldapsam" > secrets/LDAP_AUTH_citldapsam
ssh sam-app.ucar.edu "sudo -n -iu swes podman unshare cat /data/tomcat-sam/secrets/sam.parm" > secrets/sam.parm
```

`sam.parm` holds seven SAM API credentials; syncd sends only `SAM_AUTH_admin`, and the
rest load into each container's parameter store as they do on sam-app. sam-app's test
and prod stacks share one secrets directory, so it is the same credential whichever SAM
it is sent to. Hybrid mode (below) needs no LDAP credential at all.

**4. Environment.** `cp env.example .env`. The values match sam-app's prod `.env`
except the lines marked `LOCAL`: RID 168 (165–167 are dev, test and prod), `SAM_URL`,
the two stub files, and the dead staging URL. The inert `SYNCD_*_CRON_DFLT` lines are
kept on purpose, so the cron behavior matches prod (NCAR/sam-ldap-syncd#6).

## Running it

Hybrid mode, `env.example`'s default: the replica is seeded from prod's own stage-1 data
and carried forward by `ldap-poll` ("Choosing the LDAP source" says why there is no
syncrepl from a laptop).

```bash
rsync sam-app.ucar.edu:/data/tomcat-sam/prod/sam-ldap-syncd/auditlog.d/<ts>Z-1-slapcat seed/
rsync sam-app.ucar.edu:/data/tomcat-sam/prod/sam-ldap-syncd/auditlog.d/<ts>Z-combined-auditlog.ldif seed/
bin/ldif-scrub seed/<dump> seed/dump.ldif          # the seven subtrees, no password attributes
bin/ldif-scrub seed/<audit> seed/audit.ldif
bin/ldif-fold seed/dump.ldif seed/audit.ldif seed/folded.ldif   # the directory as of the copy
RESET=1 docker compose --profile init run --rm volume-init      # lay out /var/data
docker compose up -d ldap; docker compose stop ldap  # bootstraps slapd.d, then idles in INITIALIZING
docker compose --profile seed run --rm ldap-seed    # slapadd, offline; RESEED=1 to replace
docker compose up -d ldap transformer syncd ldap-poll
bin/collect                                        # stub logs + syncd logs -> out/<ts>/
```

| Stage | Watch |
|---|---|
| ldap | `ldapsearch -x -H ldap://127.0.0.1:19389 -b dc=ucar,dc=edu -s one dn`; `auditlog.d/INITIALIZING` gone |
| transformer | `<ts>Z-add.jsl` and `-mod.jsl` files in the spool (`docker compose --profile tools run --rm shell -c 'ls -la /var/data/syncd'`) |
| syncd | `log-syncd.e`: seven GETs at start (institution … gidAllocation), then the stub log grows |

`syncd` runs `-vvvvv --redirect` as in prod, so its output goes to `log-syncd.{o,e}` in
the data volume, not `docker compose logs`. Like prod, nothing restarts a container that
exits, except `ldap-poll` (`unless-stopped`). Docker Desktop pauses with the Mac, so an
overnight run needs the machine awake (`caffeinate -is`, on AC, lid open).

### Operating

```bash
docker ps --filter name=ldap-pipeline --format '{{.Names}}\t{{.Status}}'
docker compose logs --since 1h ldap-poll | tail     # one line per poll: errors=0, watermark advancing
docker compose --profile tools run --rm -T shell -c \
  'grep -c " to api" /var/data/syncd/sam-update-stub.log; tail -3 /var/data/log-syncd.e'
```

`docker compose stop` pauses everything; the `data` volume keeps the replica, the poller
watermark (`ldap-poll.json`), the spool and syncd's state, and `docker compose up -d ldap
transformer syncd ldap-poll` resumes. A restarted syncd reloads `sam-data.json`, folds
`sam-log.jsl` into it and truncates the log, so run `bin/collect` first. A **reset**,
prod's `reset.sh -s` (stop syncd, empty `/var/data/syncd/`, `touch /var/data/.reset` owned by
tomcat-sam, start), makes the transformer rebuild `add.jsl` from the latest dump and
syncd download SAM afresh and diff the two: one prod read and the whole seed-diff PUT
stream again. `bin/collect` copies into `out/<ts>/` the stub logs, syncd's logs,
`sam-data.json` and `sam-log.jsl` (the daemon's own per-change log; it holds the live
stream only, while the stub log also holds the seed diff).

`sam-data.json` is the daemon's in-memory copy of SAM, rewritten after every applied file,
so it already reflects the PUTs the stub swallowed. To capture SAM *before* a PUT stream,
reset with `syncdInit` in between: empty `/var/data/syncd/`, `touch /var/data/.reset`, `docker
compose --profile init run --rm syncdInit` (downloads and writes the dump, applies
nothing), `bin/collect`, then `docker compose up -d syncd`. The diff of each PUT body
against that dump is the field-level change the port has to make.

### Choosing the SAM

| `SAM_URL` | Use |
|---|---|
| `https://sam.ucar.edu:443` + `SAM_READ_PROD=1` | legacy reads, writes stubbed. The guard refuses until the flag is set |
| `http://host.docker.internal:5050` | SAMuel (`docker compose up samuel-dev`); the only target that may take writes |
| `https://test-sam.ucar.edu:443` | unusable: 404 on every path (2026-10-08), and the daemon retries a 404 forever, silently |

Reading from prod has a cost. A syncd start that finds no `/var/data/syncd/sam-data.json` and
`sam-log.jsl` downloads all seven collections (`user` is 24 MB and ~30 s of Tomcat time);
a restart that finds them reloads the dump instead. So a reset is deliberate, never in a
loop (see "Operating"). Never run
`syncdTest` there: `ldapsync/status` answers 500 on prod, and legacy mails every 500 to
SWEG. The guard enforces both.

SAMuel's local MySQL (port 3306, loaded by `make clone` in `containers/sam-sql-dev/`) is
not obfuscated, and the identity tables are copied whole, so the LDAP diff against it is
real. Check before a run: `organization.name` should read like `CISL`, not `Research
Division B`. Everything under `out/` and the data volume is PII: never commit it.

### Choosing the LDAP source

**citldapsam from fdb** (`compose.yaml` alone) is prod's path, but fdb answers it from a
laptop with `invalid credentials` (rc 49, 2026-10-08, byte-identical secret), presumably
a host restriction. Stop `ldap` at the first rc 49: it retries every 60 s.

**Anonymous syncrepl is not available:** ldap.ucar.edu rejects the sync control as
critical for anonymous clients (`ldapsearch -E sync=ro` hides this, since it sends the
control as non-critical).

**Hybrid** (`COMPOSE_FILE=compose.yaml:compose.hybrid.yaml` in `.env`) runs the replica
with no syncrepl and seeds it from prod's own stage-1 data, the recipe under "Running it".
Without that line `compose.yaml` alone is prod's path: `docker compose up -d ldap` starts a
full refresh from fdb, `docker compose logs -f ldap` shows INITIALIZING clearing.

`ldap-poll` carries the seed forward: every `POLL_INTERVAL_SECS` (300) it reads the
entries ldap.ucar.edu modified since its watermark and writes the differences into the
replica as cn=admin under the relax control, so upstream's `modifyTimestamp` and
`modifiersName` survive and slapd's audit log records them as it records syncrepl's writes
in prod. Every `POLL_SWEEP_SECS` (3600) a DN/`entryUUID` listing (about 7 MB) catches
deletes and renames. It never removes an attribute anonymous readers cannot see, so a
seeded `x-ucar-contactPerson` stays put; a service account added after the seed arrives
without one. The subtree roots carry each replica's own `entryUUID`, so entries are
matched by UUID, then by DN. A sweep whose upstream listing is empty for any subtree is
abandoned (slapd answers a missing root the way it answers an empty one), and one that
would delete more than `POLL_MAX_DELETES` (200) applies none of its deletes. State:
`/var/data/ldap-poll.json`; log: `docker compose logs ldap-poll`. `entryCSN` cannot be
set, even under relax, so local CSNs are local.

Never copy the `-0-slapcat` dump: it is prod's cn=config, citldapsam password included.
Prod's replica copies the whole tree, password hashes too, so scrub before anything else.
The fold is a snapshot, not a replay: syncd diffs it against SAM's current state and
PUTs only real mismatches. Measured against an anonymous snapshot taken minutes later,
the folded seed matched entry for entry; anonymous readers lack only
`x-ucar-contactPerson` (service accounts and positions), `x-ucar-peid`, `x-ucar-office`
and `telephoneNumber`, of which the transformer maps only the service-account
`x-ucar-contactPerson`.

## Profiles

| Profile | Services |
|---|---|
| (default) | `ldap`, `transformer`, `syncd`, and the guards |
| `init` | `volume-init`, `syncdInit` (`--init-only -f`) |
| `test` | `syncdTest` (`--test-connections`) |
| `dump` | `syncdDump` (`--display-dump`) |
| `debug` | `syncdadmin` (bash), `ldapDebug` (`--debug`) |
| `tools` | `shell` (root on the data volume; used by `bin/collect`) |

## Files

| File | |
|---|---|
| `pins` | the prod commits, read by `bin/build-images` |
| `compose.yaml` | sam-app's prod compose, adapted as its header says |
| `env.example` | prod `.env`, with the `LOCAL` lines |
| `bin/guard.sh` | the checks above |
| `bin/volume-init.sh` | sam-app's `reset.sh`: directory layout and ownership |
| `compose.hybrid.yaml`, `bin/synccons-off.sh`, `bin/ldap-seed.sh` | hybrid stage 1: a no-syncrepl image variant and the offline seed |
| `bin/ldif-scrub`, `bin/ldif-fold` | cut prod's dump and audit log to the seven subtrees; fold one into the other |
| `bin/ldap-poll`, `bin/ldiflib.py` | the hybrid poller and the LDIF code it shares with `ldif-fold` (Python 3.7, the ldap image's) |
| `bin/init-secrets`, `bin/collect` | setup and collection |

## Toward upstream fixes

The same harness builds George's repos at any ref (`REF_<REPO>=`), so a fix to one of
the defects in NCAR/sam-ldap-syncd#6 can be shown working end to end before it is
proposed. `pins` changes only when sam-app is redeployed.
