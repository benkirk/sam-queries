# HPC lanes and the conda install: layered, permission-scoped env

**Status:** done, 2026-10-09 (as built). #779, #780 and #782 reached `main` in #781 (`c020b188`).
**End state:**
- Interactive users run the conda install (`/glade/u/apps/opt/sam-queries/bin/sam-search`).
  Its install-root `.env` (0644) holds only read-only roles.
- Cron runs from the ncar-hpc-deploy lanes. The lane `bin/` also works for any user, read-only;
  `sam-admin` there is gated to csgteam.
- No world-readable file on GLADE holds a writer.

**See also:**
- `containers/ncar-hpc-deploy/README.md` (§ Lanes, § Install);
- `JOBHIST_WRITER_ROLE.md`, the role-creation precedent;
- ledger entry 20 (`UNPLANNED_CITY_LEDGER.md`): the CLI sweep and the timings.

## 1. Where we started (2026-10-09)

- **Lanes:** `lanes/{prod,dev}/env` and `lanes/prod/env.jobhist-sync` were 0600 csgteam, each
  file holding every role its lane used.
  - A regular user's `bin/sam-search` stopped at "missing lane env".
  - `nhd_exec` merged `env` + `env.<tool>` into `state/`, which only csgteam can write.
- **Conda install:** `/glade/u/apps/opt/sam-queries/.env` was 0644 with no ACL. A read-only
  privilege check of its credentials:

| credential | role | can do |
|---|---|---|
| `SAM_DB_*` (= `PROD_`, `TEST_`) | `hpc-reader` | `SELECT, SHOW VIEW ON sam.*` |
| `LOCAL_SAM_DB_*`, `STATUS_DB_*` | — | point at 127.0.0.1; unusable from Casper |
| `JOB_HISTORY_PG_*` = `CIRRUS_PG_*` | `pguser` | read the jobs DBs, `campaign`, `destor`; **DML on all 20 tables of `system_status` and `system_status_dev`** |

Both files were replaced (§ 6).

## 2. The rule

The database grant is the security boundary; a file mode only decides who can read which grant.
Every env file holds credentials for exactly one audience, and its mode matches that audience.
A writer never shares a file with anything wider than its own audience.

## 3. Roles

| need | public layer (any user) | gated layer |
|---|---|---|
| SAM MySQL | `hpc-reader` | `hpc-writer` (`env.sam-admin`, the cron overlays) |
| job_history PG | `hpc_reader` on `csg-postgres-ro` | `jobhist_writer` (`env.jobhist-sync`) |
| system_status PG | `hpc_reader` on `csg-postgres-ro` | `pguser` stays in k8s and never goes in a GLADE file |

`hpc_reader` (`scripts/sql/create_hpc_reader_pg.sql`) **reads everything** (Ben's decision):
- `GRANT pg_read_all_data`: every table, view and sequence in every schema and database,
  including later ones.
- `LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE`, `CONNECTION LIMIT 40`.
- At the role level: `default_transaction_read_only=on`, `statement_timeout=60s`,
  `idle_in_transaction_session_timeout=60s`.
- `INHERIT` matters: under `NOINHERIT` the membership grants nothing without `SET ROLE`.

The password is a verifier from `scripts/pg_scram_verifier.py`, stored in OpenBao
`csg/hpc-reader-pg`; CREATE ROLE never sees the plaintext. Verified 64/64 from casper and derecho,
on the standby and the primary:
- SELECT works.
- Writes are refused, and still refused after `SET default_transaction_read_only = off`.
- CREATE is refused.
- A runaway query is cancelled.

### 3a. Transport

- **Postgres:** every layer sets `*_REQUIRE_SSL=true` (`sslmode=require`) and
  `PGCHANNELBINDING=require`, which libpq reads from the environment (libpq 17 in the conda env
  and the SIF).
  - SCRAM channel binding ties the login to the TLS session. That defeats a man-in-the-middle
    without distributing a CA.
  - A SCRAM-less (md5) role would fail under it.
- **`verify-full` is not used:** the CNPG certificate's SANs omit `csg-postgres-ro.k8s.ucar.edu`,
  and the CA rotates. Add the `-ro` name to `serverAltDNSNames` first if it is ever wanted.
- **MySQL:** sam-sql negotiates TLS 1.3 with `SAM_DB_REQUIRE_SSL=true`, without certificate
  verification.

## 4. Layered env in the wrapper (`libexec/lane.sh`)

Layers are `lanes/<lane>/env`, then `env.<tool>` when the user can read it; a deeper layer
overrides. There is no deploy-root layer, and the conda `.env` is never read.

```
lanes/<lane>/
  env                  0644      hpc-reader / hpc_reader, SAM_DB_READ_ONLY=1, STATUS_DB_READ_ONLY=1
  env.sam-admin        0640      csgteam (+ setfacl g:<admins>:r)   hpc-writer, SAM_DB_READ_ONLY=0
  env.accounting-*     symlink   -> env.sam-admin
  env.collectors       0600      csgteam   STATUS_API_*, JUPYTERHUB_*
  env.jobhist-sync     0600      csgteam   jobhist_writer on the primary
```

- **Merge:** the readable layers go through `awk 1` (a missing final newline cannot fuse two
  lines) into a 0600 `mktemp` under `$TMPDIR`, then `--env-file`.
  - The copy is removed after the run, on `nhd_die`, and on HUP/INT/TERM (each trapped into an
    exit). Only SIGKILL leaves it, owner-only.
  - Each layer sets final names: no cross-layer `${VAR}` aliases.
- **Gate:** `sam-admin` and `jobhist-sync` (`NHD_GATED_TOOLS`) refuse to start, naming the
  group, when their overlay *exists* but is unreadable. They never fall back to the public layer.
  An absent overlay means a single-audience lane: dev has one 0600 `env`.
- **Regular users:** nothing is written to `state/`. `MPLCONFIGDIR` goes to `$TMPDIR` when
  `state/` is not writable. `NHD_DEBUG=1` prints the layer *names*, never values.
- **Also:** an empty image is refused (§ 6c). `--cleanenv` stays, and the image ships no `.env`.

## 5. App changes

- `sam.session.connect_args(driver, require_ssl, application_name, read_only=False)` (#780):
  - Postgres: `target_session_attrs` is `any` when `read_only`, else `read-write`.
    `read-write` refuses a standby and a read-only role.
  - Postgres also gets `options=-c default_transaction_read_only=on`; MySQL gets
    `init_command='SET SESSION TRANSACTION READ ONLY'`.
- `SAM_DB_READ_ONLY` / `STATUS_DB_READ_ONLY` choose it per bind. `system_status.session` honors
  `STATUS_DB_PORT`.
- #779: `Project.users` in one query (`sam-search project` on HPC went from 143 to 43 statements).
- #782: `compileall` in the image's `base` stage, and the § 4 loader.
- Not done, not needed: `JOB_HISTORY_PG_READ_ONLY` in hpc-usage-queries; the role and the
  standby already enforce it.

## 6. The end state on GLADE

| file | mode | holds | read by |
|---|---|---|---|
| install-root `.env` | 0644 | the prod public layer in python-dotenv syntax (19 keys) | conda `sam-search` / `sam-admin` |
| `lanes/prod/env` | 0644 | the same readers in apptainer syntax; `STATUS_DB_SERVER` = `csg-postgres-ro` | every lane tool and cron job |
| `lanes/prod/env.sam-admin` (+ `env.accounting-*` links) | 0640 csgteam | `hpc-writer` | lane `sam-admin`, the accounting jobs |
| `lanes/prod/env.collectors`, `env.jobhist-sync` | 0600 csgteam | API tokens; `jobhist_writer` | those jobs |
| `lanes/dev/*` | 0600 csgteam | one file per lane | csgteam only, until a `sam_dev` reader exists |

- **Writes:** the conda `sam-admin` holds readers only, so its writes fail at the database
  ("Cannot execute statement in a READ ONLY transaction"). Admin writes go through the lane's
  `bin/sam-admin`.
- **Keeping the two reader files in step:** they carry the same credentials in two syntaxes.
  - apptainer's `--env-file` expands `$NAME` even inside single quotes, so a literal `$` is `\$`.
  - python-dotenv keeps single-quoted values literal, so the same `$` stays `$`.
  - Change both together. Check them by hashing each key's value as each consumer sees it: the
    lane's environment inside the SIF against `dotenv_values()` of the conda `.env`.
- **After every GLADE pull, csgteam runs `make bytecode`.** Users cannot write `src/__pycache__`.
  Until csgteam recompiles, every module a pull changed is compiled again on every user run; after
  a Python upgrade that is all 157 modules (+0.15–0.3 s per run).

**Why users stay on conda.** Timed 2026-10-09, `main` @ `c020b188`, medians of 5, as a regular
user:

| `--help` / import / `project SCSG0001` | Casper | Derecho |
|---|---|---|
| conda, with bytecode | 0.57 / 0.55 / 1.33 s | 0.80 / 0.80 / 1.72 s |
| lane (SIF) | 1.43 / 1.38 / 2.32 s | 1.77 / 1.69 / 2.73 s |

- The SIF costs ~0.2 s to start, and its imports run ~2.2x slower for the same modules
  (`sqlalchemy.orm` 273 vs 118 ms). That points at reading files from the image under
  unprivileged apptainer 1.4; it was not chased.
- Through the lane, `project SCSG0001` went from 3.0 to 2.33 s on Casper and from 3.8 to 2.70 s
  on Derecho. Nearly all of that is #779; the bytecode is worth 0.1–0.2 s.

### 6a. Installing lane files: a tarball and `env-finalize.sh`

Lane files were installed as a bundle, never by hand-editing secrets in place:
- Claude built the tree on the laptop under umask 077, from a csgteam-staged snapshot of the old
  files plus OpenBao. The tree held the `MANIFEST`: per file, the path, mode, group and SHA-256,
  no values.
- Ben extracted it as csgteam beside the deploy root and ran
  `containers/ncar-hpc-deploy/libexec/env-finalize.sh`:
  - a dry run by default, `--apply` to write;
  - it checks every file against the `MANIFEST`;
  - it backs each replaced file up to `.bak-STAMP` (0600) and installs atomically;
  - `--rollback` restores the newest backups.

### 6b. Rollout record (2026-10-09)

| # | step | verified |
|---|---|---|
| 0 | privilege census of the conda `.env` | § 1 |
| 1 | `hpc_reader` created (`create_hpc_reader_pg.sql`, verifier in OpenBao) | 64/64 from casper and derecho (§ 3) |
| 2 | read-only binds, #780 | last-seen returns 6 sources via the primary and via `csg-postgres-ro` |
| 3 | lane files split by audience (§ 6a, second bundle) | `smoke --lane prod` PASS. The first bundle broke `hpc-reader`: its password has a `$`, which `--env-file` expanded |
| 4 | the § 4 loader, #782 | as `cmipap` (not in csgteam): `sam-search` works on `env` alone; `sam-admin` and `jobhist-sync` refused with rc=2, naming the group; nothing written; no temp file left |
| — | #781 to `main`, GLADE pull, prod lane update | `samuel-main-a99cdb56a06b`, smoke PASS on both hosts; the next hourly and rapid ticks exited 0 |
| — | `STATUS_DB_SERVER` set to `csg-postgres-ro` in `lanes/prod/env` | last-seen returns 6 sources on both hosts |
| 5 | users stay on conda (Ben), not the lane `bin/` | the timings in § 6 |
| 6 | install-root `.env` replaced with the read-only roles | 19 keys identical by hash to the lane's; project, last-seen, jobs, allocations and `sam-admin --validate` pass on both hosts; a SAM write is refused |

### 6c. Incident, 2026-10-09 17:06–18:06 (prod lane down)

- **What broke:** two csgteam-owned files were truncated to 0 bytes,
  `containers/ncar-hpc-deploy/libexec/ncar-hpc-deploy` and the prod lane's current SIF.
- **Effect:** every tick was a silent no-op (exit 0, no log) until 17:59. After that, ticks
  failed with `image format not recognized`. Collectors, rapid `jobhist-sync` and one hourly
  `accounting-comp` were missed.
- **Cause:** not chased. It coincided with a `git pull` and `source etc/config_env.sh` in the
  install root.
- **Recovery:** `git checkout --` the script as csgteam, then `ncar-hpc-deploy update --lane prod`.
- **Trap:** run `ncar-hpc-deploy` as `sudo -u csgteam bash -lc '...'`. A bare `sudo -u csgteam`
  has no `/opt/pbs/bin`, so the collectors smoke fails with exit 127.
- **Fixed in #782:** `nhd_exec` refuses an empty image, so the tick fails and mails.
- **Still a gap:** an empty script cannot report on itself; the tell is a stale `tick.rapid`.

## 7. Decisions open

- **`sam-admin` ACL groups:** csgteam only, for now. Add a group with
  `setfacl -m g:GROUP:r lanes/prod/env.sam-admin`.
- **`hpc-reader` (MySQL) grant narrowing:** any HPC user can SELECT any `sam` table, including
  ones `sam-search` never shows: `api_credentials` (bcrypt hashes), `account_request`,
  `notification_log`. The fix is a DBA request: REVOKE on those tables, or a view-based grant.
- **`pg_read_all_data` scope:** any HPC user can read every database on csg-postgres, including
  `sam_dev` (a prod snapshot) and `campaign`/`destor` (filesystem paths and owners). It is the
  same question as the MySQL one, for Postgres.
- **CLI start time:** lazy subcommands are tabled (ledger 20), and so is the SIF import slowdown
  (§ 6).
