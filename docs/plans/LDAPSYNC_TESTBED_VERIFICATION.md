# LDAP sync API: testbed read comparison and write round trip (handoff)

**Status:** done 2026-10-09; results in `LDAP_SYNC_API.md` § 10.5. Phase B ran as a
stub-on capture replayed over HTTP, because the daemon aborts its own stream at the first 400.
**Branch:** `ldapsync-api` (the implementation; this doc ships in its PR).
**Read first:** `docs/plans/LDAP_SYNC_API.md` § 10 (as built, deviations D1–D25),
`containers/ldap-pipeline/README.md` (the testbed, especially *Operating* and
*Choosing the SAM*), `containers/ldap-pipeline/out/HANDOFF.md` (untracked; the
testbed's running state).

## Goal

Prove, with the real `sam-ldap-syncd` daemon, that SAMuel can replace legacy Java SAM
behind one variable (`SAM_URL`):

- **A. Reads.** The daemon's picture of SAM loaded from SAMuel matches the one loaded
  from legacy, apart from the documented deviations and data age.
- **B. Writes.** The daemon's full update stream lands in SAMuel without rejections,
  and a second pass converges: after SAMuel has absorbed the stream, re-reading it
  leaves the daemon almost nothing to send.

Results go into `LDAP_SYNC_API.md` § 10.5–10.6; any fix is a commit on `ldapsync-api`.

## What is already proven (do not redo)

- Unit, query, HTTP and CLI tiers on MySQL and Postgres; full suites green.
- The testbed's captured corpus (`out/20261009T050041/sam-update-stub.log`, 1,346 PUTs)
  replayed through the manage layer against the local clone, rolled back: 1,344
  accepted, 2 rejected with legacy-known 400s. Script:
  scratchpad `replay_stub.py` from the 2026-10-09 session (pattern: load each two-line
  stub record, `Schema().load(body)`, call the `sync_*` function in a savepoint, print
  counts only).
- The daemon's `--test-connections` against the branch passed the 401 realm handshake
  and read `ldapsync/status` (401 then 200).

## Safety rules (these are not optional)

1. **The guard is the answer.** `bin/guard.sh` runs before every syncd container. If it
   refuses, stop and find out why. Never rerun with `--no-deps` after a refusal. On
   2026-10-09 exactly that sent one `GET ldapsync/status` to prod legacy, which 500s and
   mails SWEG.
2. **zsh does not word-split.** Write `-e NAME=value` flags literally, never through a
   variable. Before a guarded run, confirm what the container sees:
   `docker compose --profile test run --rm -T --no-deps -e SAM_URL=... --entrypoint env syncdTest | grep ^SAM_`.
3. **Writes only to SAMuel.** Prod is read-only (`SAM_READ_PROD=1`) and every write to
   it is stubbed. Phase B turns the stub off only with `SAM_URL` at
   `http://host.docker.internal:<port>`; the guard enforces it.
4. **Collect before any syncd restart or reset** (`bin/collect`): a restart folds
   `sam-log.jsl` into `sam-data.json` and truncates it.
5. **Everything under `out/` is PII.** Report counts and field names only, never values.
   `bin/compare-dumps` already prints only counts and field names.
6. **One prod download per capture.** A reset against prod reads all seven collections
   (`user` is 24 MB, ~60 s). Deliberate, never in a loop.

## Setup

1. Check out `ldapsync-api` (or staging, once merged) in this worktree.
2. **Refresh the local clone** so data age does not swamp the comparison:
   `make -C containers/sam-sql-dev clone` (reloads MySQL :3306 from prod; it is
   **unobfuscated**, see the memory note on the dev DB). Record the clone time.
3. **Serve the branch on a spare port**, not via samuel-dev (its container predates the
   branch): `scripts/dev_server_alt.sh "$PWD" 5051` in the background. Check the
   listener is new (`lsof -ti tcp:5051 -sTCP:LISTEN`, then its cwd) and that
   `curl -sI http://localhost:5051/api/protected/admin/ldapsync/status` answers 401 with
   `WWW-Authenticate: Basic realm="Realm"`. The script forces every outbound lever off and
   the `LDAPSYNC_*` levers default off, which is what both phases want.
4. The daemon authenticates as `admin` with prod's `SAM_AUTH_admin` from `secrets/sam.parm`;
   the unobfuscated clone holds that `api_credentials` row with `ROLE_API_ADMIN` (proven
   by the 2026-10-09 handshake). After a fresh clone, re-check with the curl above plus
   `-u admin:<pw>` only if the handshake later fails.
5. `bin/collect` the testbed's current state before touching it.

## Phase A: read comparison

Two captures of the daemon's picture of SAM, taken with `syncdInit`
(`--init-only -f`: download, write `sam-data.json`, apply nothing, send nothing).

1. `docker compose stop syncd`.
2. **Capture A, legacy:** reset the spool exactly as README *Operating* describes (empty
   `/var/data/syncd/`, `touch /var/data/.reset` owned by 303:303), then
   `docker compose --profile init run --rm syncdInit` with the default `.env`
   (`SAM_URL` prod, `SAM_READ_PROD=1`). `bin/collect`; note the `out/<ts>` as A.
3. **Capture B, SAMuel:** same reset, then run the guard and `syncdInit` with literal
   overrides, the second gated on the first:
   ```bash
   if docker compose --profile init run --rm -T -e SAM_URL=http://host.docker.internal:5051 guard \
        | grep -q "ok,"; then
     docker compose --profile init run --rm -T --no-deps \
        -e SAM_URL=http://host.docker.internal:5051 syncdInit
   fi
   ```
   (Check the guard service name and its "ok" line in `compose.yaml` / `bin/guard.sh`
   first; adjust the grep to the real text.) `bin/collect`; note it as B.
4. `bin/compare-dumps out/<A>/sam-data.json out/<B>/sam-data.json`.
5. Classify every differing field:
   - **Expected, by design** (§ 10.3): `projectGroup.tags` (D14: branch tags without a
     member; NULL-end allocations), `group.upids`/`rolenames` (D13: case-insensitive
     resolution), `gidAllocation.modifiedTime` (legacy always null), position `endDate`
     millisecond part (legacy `.999`; the daemon zeroes it, so it should not show).
   - **Data age:** sparse differences on a few records, consistent with the time
     between clone and capture.
   - **Port defect:** anything systematic (the same field on most records of a type),
     a type's record count off, or keys missing. Fix on the branch, add a test, redo B.
6. Record the per-type table in § 10.5.

## Phase B: write round trip

The daemon diffs its LDAP replica against SAMuel and PUTs the difference into SAMuel.
This **writes the local clone**; refresh it afterwards (`make clone`) if a pristine copy
matters.

1. `docker compose stop syncd`; `bin/collect`.
2. Edit the testbed `.env` (untracked): `SAM_URL=http://host.docker.internal:5051`,
   `SAM_UPDATES_STUB=` (empty), `SAM_READ_PROD=` (empty). Run the guard alone and read
   its line: it must say SAM writes go to the SAMuel URL, not the stub.
3. Reset the spool (README *Operating*) and `docker compose up -d syncd`. The daemon
   downloads from SAMuel, waits for the transformer's fresh add file, then PUTs the
   difference (expect roughly the seed diff, ~1,350 records).
4. Watch, counts only:
   - the branch server's log (`run — PUT /api/protected/admin/ldapsync/<type> → <code>`):
     tally codes per type;
   - `log-syncd.e`: "Pausing and continuing after exception" means a rejection the
     daemon will drop until its next reload; "Deferring propagation" is routine (the ~44
     unclaimed placeholder accounts).
   Expected rejections: the two legacy-known 400s (an institution acronym over 40
   characters; a upid held by another username). Any 500 is a port defect.
   Purge permits: with `LDAPSYNC_PURGE_ENABLED` off an existing row answers not purgeable
   and the daemon PUTs a tombstone; the recurring `userPurgePermit?unixUid=1000003`
   (`eipfdbreplsync`, uid differs between SAM and IdM) is real traffic.
5. `bin/collect`.
6. **Convergence:** stop syncd, reset again, start it. This time the daemon reads back
   what SAMuel stored. Tally the PUTs of this second pass. Near zero is the pass mark;
   each remaining class is either a known round-trip instability or a defect:
   - collaboration end dates at exactly midnight (stored as 23:59:59 by the date-range
     mixin, so they never round-trip),
   - an institution sent with a country but no matching state (legacy B9: SAM has no
     country column, so `country` reads back null),
   - the two rejected records (they are re-sent every pass),
   - anything else: investigate with the stub-free `sam-log.jsl` and the server log.
7. Optional: leave syncd running against SAMuel for the live `ldap-poll` stream
   (incremental mod files, the daily snapshot) and check the next morning.
8. **Restore the testbed** to prod-read/stubbed: put `.env` back (`SAM_URL` prod,
   `SAM_UPDATES_STUB=sam-update-stub.log`, `SAM_READ_PROD=1`), reset, start syncd. Stop
   the 5051 server. Update `out/HANDOFF.md` with what changed.

## Report

Update `docs/plans/LDAP_SYNC_API.md` § 10.5 (results) and § 10.6 (remaining cutover
items) on the branch, commit per fix, and summarize to Ben: phase A per-type
differences by class, phase B status-code tally, rejection list by template, and the
second-pass PUT count with its residual classes.

## Pointers

- Code: `src/webapp/api/ldapsync/`, `src/sam/queries/ldapsync.py`,
  `src/sam/manage/{ldapsync,purge,lifecycle,employment}.py`.
- Testbed: `containers/ldap-pipeline/` (`bin/guard.sh`, `bin/collect`,
  `bin/compare-dumps`, `compose.yaml`).
- Daemon reference and bug list: `docs/plans/SAM_LDAP_SYNCD_REFERENCE.md`.
