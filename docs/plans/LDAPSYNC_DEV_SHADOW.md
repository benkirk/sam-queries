# LDAP sync API: shadowing production on samuel-dev

**Status:** plan, 2026-10-09. #773 (`08794fbb`), #774 (`b9fc3efd`) and the levers PR #776
(`4ee06e00`) are on `staging`; this branch carries the testbed changes. **Read first:** `LDAP_SYNC_API.md` § 10,
`LDAPSYNC_TESTBED_VERIFICATION.md` (safety rules), `containers/ldap-pipeline/README.md`,
and the #774 comment on the lifecycle job
(https://github.com/benkirk/sam-queries/pull/774#issuecomment-6087867176).

## Goal

Run the real `sam-ldap-syncd` (the #774 build) against `samuel-dev` for days, with the
purge and restore levers on and lifecycle off, while legacy keeps serving production: the
same directory changes land in both, and a daily comparison of the daemon's picture of
each shows only the known residual classes. The exit is **five clean weekdays, one of them
after a `make refresh-dev` re-baseline**; that is the evidence for flipping `SAM_URL` on
sam-app.

## Settled (2026-10-09)

- **Auth needs nothing on dev.** The blueprint passes `roles=('ROLE_API_ADMIN',)` and no
  Permission, and `api_key_allowed` returns early for a route without one
  (`webapp/utils/api_auth.py`), so under `RBAC_SOURCE=db` the `admin` key is authorized
  by its legacy `role_api_credentials` link alone. The row comes raw with every
  `make refresh-dev` (`containers/sam-sql-dev/config.yaml` never empties
  `api_credentials`). Do not add an `API_KEYS_ADMIN` env var: a config key wins with no
  roles and fails closed. The DB key map refreshes every `API_KEYS_DB_TTL` (60 s).
- **TLS works as shipped.** The Perl client reads `HTTPS_CA_DIR=/etc/ssl/certs`
  (`env.example`), the syncd image carries Debian's `ca-certificates`, and from inside the
  running container `openssl s_client -CApath /etc/ssl/certs -verify_hostname
  samuel-dev.k8s.ucar.edu` verifies (InCommon DVG2C under emSign Root G1). The trap: when
  no CA path exists the client silently falls back to no verification
  (`LDAP_SYNC_API.md` P1a), so `syncdTest` against dev is still the first proof.
- **The guard names the host.** `bin/guard.sh` allows writes to `host.docker.internal`,
  `localhost`, `127.0.0.1` and `samuel-dev.k8s.ucar.edu`, by exact name; prod is refused
  by its own case. No wildcard: a typo'd k8s host is refused too.
- **`make refresh-dev` discards dev writes and restarts nothing.** `clone` + `clone-pg`
  load a raw prod subset into `sam_dev_next` and swap it under the running pods, which
  reconnect; `seed_status_dev.sh` and the cache refresh follow. The shadow's writes go
  with the swap, which is the point of a re-baseline; collect first. Needs the VPN and
  `SAM_DEV_PG_*`, `SAM_CACHE_REFRESH_API_PASS_DEV`, `PROD_STATUS_DB_*` in `.env`.
  Never `make -n refresh-dev`: the sub-makes execute anyway.
- **Dev's other users** (XRAS demo dispatch, load tests, the GLADE dev-lane collectors)
  already lose their writes at every refresh; purge's real deletes are churn of the same
  kind. Lifecycle stays off, so nobody's memberships close on dev.
- **A clean day is mechanical.** `bin/compare-dumps --ignore projectGroup.tags,projectGroup.lastModified`
  compares lists order-insensitively and drops the two fields legacy and SAMuel disagree
  on by design (D14; the daemon's synthetic `all-hpc-users` stamp). What remains is data
  age (a few records, one or two fields each, consistent with the minutes between the two
  captures) or a port defect (the same field on many records of a type, a count off, keys
  missing). Exit status 1 when anything differs, so the day's verdict is one line.
- **The daily table** lives in `LDAP_SYNC_API.md` § 10.7, counts only.
- The 175+ users already stamped pending in prod data finish on the first run that ever
  asks with lifecycle on; that is a separate decision, not part of this shadow.

## Sequence

### 1. Merge and roll (done for #773)

- #773 squashed to `staging` as `08794fbb`. Argo rolls `samuel-dev` to `samuel:sha-08794fb`
  (gated off: no `LDAPSYNC_*` env on dev yet). Proof: the image on
  `deploy/samuel-dev` carries that sha, and `scripts/cirrus_watch.sh --context nwc1 --env dev`
  is quiet. The handshake on dev before any lever: `curl -sI
  https://samuel-dev.k8s.ucar.edu/api/protected/admin/ldapsync/status` → 401 with
  `WWW-Authenticate: Basic realm="Realm"`; then `-u admin:<SAM_AUTH_admin>` → 200. A 403
  means the role link did not come across with the data. The credential is read from
  `secrets/sam.parm` and never echoed.
- #774 merged with the "patched here" pointers in `SAM_LDAP_SYNCD_REFERENCE.md` (bug 15,
  N5, N22, N23). The daemon image for the shadow is `PATCHED=1 bin/build-images`
  (`SYNCD_TAG=pipeline-local-patched`); `compose.yaml` on `staging` reads `SYNCD_TAG`.

### 2. Baseline

`make refresh-dev`; record T0 (the clone's start time) in § 10.7. Dev's identity tables are
now prod's as of T0, including `users.deactivate` stamps and the `admin` key.

### 3. Levers, dev only

One PR, one commit, `helm/values-dev.yaml` under `webapp.env`:

```yaml
    # The LDAP sync shadow (docs/plans/LDAPSYNC_DEV_SHADOW.md): purge and restore
    # live, lifecycle off (the daemon cannot call it; #774's comment). Prod stays off.
    LDAPSYNC_PURGE_ENABLED: "1"
    LDAPSYNC_RESTORE_ON_REACTIVATE: "1"
    LDAPSYNC_LIFECYCLE_ENABLED: "0"
```

`values.yaml` untouched. Proof before push: `helm/tests/test-dev-render.sh`, and a render
with both values files shows the three keys on the Deployment only (the tasks CronJob
forwards `NOTIFY_*` and the DB keys, nothing else). Proof after the roll:
`userPurgePermit?unixUid=<an inactive user's uid>` no longer answers "Purge disabled",
and `userlifecycle/pendingdeactivations/24` answers `[]`.

### 4. Close the local soak, repoint the testbed

1. `bin/collect`, then write § 10.7's local-soak block (counts only) and run the teardown
   in `out/HANDOFF.md`: stop syncd and ldap-poll, stop the 5051 server, `make -C
   containers/sam-sql-dev clone` so the laptop clone is prod again.
2. `syncdTest` against dev, guard first (literal flags; zsh does not word-split):
   ```bash
   docker compose --profile test run --rm -T -e SAM_URL=https://samuel-dev.k8s.ucar.edu guard-test \
     && docker compose --profile test run --rm -T --no-deps -e SAM_URL=https://samuel-dev.k8s.ucar.edu syncdTest
   ```
   This is the TLS proof (a verification failure is an LWP 500 with `Client-Warning:
   Internal response`) and the handshake proof in one.
3. `.env.dev` = `.env.soak` with `SAM_URL=https://samuel-dev.k8s.ucar.edu`, `SAM_READ_PROD=`
   and `SAM_UPDATES_STUB=` empty, `SYNCD_TAG=pipeline-local-patched`; copy it over `.env`
   (`.env.prod-read.bak` stays as the restore point).
4. Reset the spool by renaming it aside (never `rm` in the data volume), `touch
   /var/data/.reset` owned 303:303, `docker compose up -d ldap transformer syncd ldap-poll`.
   Pass 1 should be small: dev is prod at T0 and the replica tracks prod. The 24 MB `user`
   GET now crosses the dev ingress; note its time from the daemon log. The first tick
   after pass 1 settles, then the soak cadence: every 4-6 h, a daily summary, quiet unless
   a signal moves (5xx, a pause, `IMDBStateException`, the 0002 skip count, poller errors).

### 5. The daily shadow check

Two `syncdInit` captures a day, prod then dev, with the daemon stopped for the five
minutes they take (the transformer keeps spooling directory changes meanwhile; the daemon
applies them when it restarts). `syncdInit` downloads only into an empty spool, so the
live spool is moved aside and back; `.reset` is never touched here, or the transformer
would rebuild the seed diff and resend it.

```bash
cd containers/ldap-pipeline
bin/collect                                        # the daemon's state first
docker compose stop syncd
ts=$(date +%Y%m%dT%H%M)
docker compose --profile tools run --rm -T shell -c \
  "mv /var/data/syncd /var/data/syncd.live-$ts && mkdir /var/data/syncd && chown 303:303 /var/data/syncd"
# A: prod, one deliberate download (SAM_READ_PROD=1 is the guard's permission)
docker compose --profile init run --rm -T --no-deps -e SAM_URL=https://sam.ucar.edu:443 -e SAM_READ_PROD=1 guard \
  && docker compose --profile init run --rm -T --no-deps -e SAM_URL=https://sam.ucar.edu:443 -e SAM_READ_PROD=1 syncdInit
bin/collect                                        # -> out/<A>
docker compose --profile tools run --rm -T shell -c \
  "mv /var/data/syncd /var/data/syncd.initA-$ts && mkdir /var/data/syncd && chown 303:303 /var/data/syncd"
# B: dev (the .env target; the guard runs as syncdInit's dependency)
docker compose --profile init run --rm -T syncdInit
bin/collect                                        # -> out/<B>
docker compose --profile tools run --rm -T shell -c \
  "mv /var/data/syncd /var/data/syncd.initB-$ts && mv /var/data/syncd.live-$ts /var/data/syncd"
docker compose up -d syncd
bin/compare-dumps --ignore projectGroup.tags,projectGroup.lastModified out/<A>/sam-data.json out/<B>/sam-data.json
```

Then the day's tick: the daemon log counts (`scratchpad`'s `soak_tick.sh` shape: PUTs by
type and status, 0002 skips, pauses, downloads), `cirrus_watch.sh --env dev`, and the
dev pod log grepped for `/api/protected/admin` 5xx (counts only). One row in § 10.7:
date, T0/T1, record counts per type on both sides, differing fields after `--ignore`,
PUT tallies by type and status, 5xx, verdict.

### 6. Re-baseline, once in the window

`bin/collect`; `docker compose stop syncd ldap-poll`; `make refresh-dev` (record T1);
reset the spool (aside + `.reset`); `docker compose up -d syncd ldap-poll`. Pass 1 is the
delta between dev-at-T1 and the replica, small again. Legacy's pending-deactivation
stamps come across with the data, as before.

### 7. Exit

Five clean weekdays, the re-baseline day among them. Then the prod `SAM_URL` flip on
sam-app is George's change; rollback is the same variable. Lifecycle is decided
separately (`LDAP_SYNC_API.md` § 10.6).

## Who does what

Ben: the merges, `make refresh-dev`, the levers PR's merge. This side: the testbed changes
(guard, `compare-dumps --ignore`), the levers PR, the daily check and its § 10.7 row, every
proof named above. Prod is read-only throughout (one deliberate download per capture);
`out/` is PII; no DDL.
