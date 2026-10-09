# HPC lanes for every user: layered, permission-scoped env

**Status:** in rollout, 2026-10-09. Steps 0 (census), 1 (`hpc_reader`) and 3 (prod lane files) done; step 2 next.
**Goal:** a regular Casper/Derecho user runs `sam-search` (last-seen and `accounting --jobs`
included) from the ncar-hpc-deploy SIF lanes with read-only database access; `sam-admin` stays
gated to named groups. Replaces the conda install's world-readable `.env`.
**See also:** `containers/ncar-hpc-deploy/README.md`, `JOBHIST_WRITER_ROLE.md` (the role-creation
precedent), ledger entry 20 (`UNPLANNED_CITY_LEDGER.md`).

## 1. Where we are (measured 2026-10-09)

- **Lanes:** `lanes/{prod,dev}/env` and `lanes/prod/env.jobhist-sync` are 0600 csgteam, no
  ACL. A regular user's `bin/sam-search` stops at "missing lane env".
- **Merge needs write access:** `nhd_exec` writes its `env` + `env.<tool>` merge into `state/`,
  and `nhd_lane_init` runs `mkdir -p` there. Neither works for anyone but csgteam.
- **Conda install:** `/glade/u/apps/opt/sam-queries/.env` is **0644, no ACL**. `make fixperms`
  (`Makefile:148`) would set a group ACL, but was never applied on GLADE.
- **Census of that file**, by read-only privilege check (Ben-authorized):

| credential | role | can do |
|---|---|---|
| `SAM_DB_*` (= `PROD_`, `TEST_`) | `hpc-reader` | `SELECT, SHOW VIEW ON sam.*` |
| `LOCAL_SAM_DB_*`, `STATUS_DB_*` | — | point at 127.0.0.1; unusable from Casper |
| `JOB_HISTORY_PG_*` = `CIRRUS_PG_*` | `pguser` | read jobs DBs, `campaign` (117 of 126 tables), `destor`; **DML on all 20 tables of `system_status` and `system_status_dev`** |

**Accepted for now (Ben, 2026-10-09):** the `pguser` exposure stays until `hpc_reader` (§ 3)
replaces it in the public layer. The password is not rotated immediately.

## 2. The rule

The database grant is the security boundary; a file mode only decides who can read which grant.
Every env file holds credentials for exactly one audience, and its mode matches that
audience. A writer never shares a file with anything wider than its own audience. The census
above is what breaking this rule looks like.

## 3. Roles

| need | public layer (any user) | gated layer |
|---|---|---|
| SAM MySQL | `hpc-reader` (exists) | `hpc-writer` (`env.sam-admin`, the cron overlays) |
| job_history PG | `hpc_reader` on `csg-postgres-ro` | `jobhist_writer` (`env.jobhist-sync`) |
| system_status PG | `hpc_reader` on `csg-postgres-ro` | `pguser` stays in k8s and never goes in a GLADE file |

`hpc_reader` (`scripts/sql/create_hpc_reader_pg.sql`) **reads everything** (Ben, 2026-10-09):
- `GRANT pg_read_all_data`: every table, view and sequence in every schema and database,
  including later ones.
- `LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE`, `CONNECTION LIMIT 40`.
- At the role level, `default_transaction_read_only=on`, `statement_timeout=60s` and
  `idle_in_transaction_session_timeout=60s`.

The password is a verifier from `scripts/pg_scram_verifier.py`, stored in OpenBao
`csg/hpc-reader-pg`. `csg-postgres` is 18.3 (`pg_read_all_data` needs 14+). Casper reaches
`csg-postgres-ro`, which reports `pg_is_in_recovery() = true`.

Proven on postgres-test (18.6), 2026-10-09:
- The verifier logs in, and a wrong password is refused.
- With `pg_read_all_data`, SELECT works in `public` and in another schema, in two databases,
  and on a table created after the grant.
- INSERT is refused (read-only transaction). After `SET default_transaction_read_only = off`
  an INSERT is still refused (permission denied). CREATE TABLE is refused.
- With the role settings, a 5 s query under a 2 s timeout is cancelled.

`INHERIT` matters: under `NOINHERIT` the membership grants nothing without `SET ROLE`.

**Your call (Ben):**
- A world-readable `hpc-reader` (MySQL) lets any HPC user run arbitrary SELECTs on `sam`,
  including tables `sam-search` never shows: `api_credentials` (bcrypt hashes),
  `account_request`, `notification_log`. This is true today through the conda `.env`. The
  fix is a DBA request: REVOKE on those tables, or a view-based grant.
- With `pg_read_all_data`, a world-readable `hpc_reader` lets any HPC user read every database
  on csg-postgres. That includes `sam_dev`, a prod snapshot that may not be obfuscated, and
  `campaign`/`destor` (filesystem scans: paths and owners). This is the same question as the
  MySQL one above, for Postgres.

### 3a. Transport (measured 2026-10-09 from casper)

- **Postgres:**
  - Both endpoints already negotiate TLS 1.3, because libpq defaults to `prefer`. But
    `sslmode=disable` also connects, so nothing *required* TLS.
  - The bundle sets `*_REQUIRE_SSL=true` (`sslmode=require`) and `PGCHANNELBINDING=require`.
    libpq reads the latter from the environment, so no code change is needed.
  - SCRAM channel binding ties the login to the TLS session, which defeats a man-in-the-middle
    without distributing a CA. Tested for `hpc_reader` (both endpoints) and `jobhist_writer`
    (both jobs DBs). libpq is 17.0 in both the conda env and the SIF.
  - Any SCRAM-less (md5) role would fail under it.
- **`verify-full` is not used:** the CNPG server certificate's SANs list
  `csg-postgres.k8s.ucar.edu` but not `csg-postgres-ro.k8s.ucar.edu`, and the CNPG CA rotates.
  Add the `-ro` name to `serverAltDNSNames` first if it is ever wanted.
- **MySQL:** sam-sql already negotiates TLS 1.3 with `SAM_DB_REQUIRE_SSL=true`. There is no
  certificate verification; that would need sam-sql's CA published on GLADE (optional).

## 4. Layered env in the wrapper (`libexec/lane.sh`)

The loader walks from the **deploy root** (`NCAR_HPC_DEPLOY_ROOT`, i.e.
`sam-queries/containers/ncar-hpc-deploy/`) down to the lane. It does not start at the install
root, so the conda `.env` and the lane files never see each other. At each level it reads `env`,
then `env.<tool>`, skipping any file it cannot read; a deeper level overrides a shallower one.

```
containers/ncar-hpc-deploy/env        0644  non-secrets shared by lanes: TZ, JOB_HISTORY_MACHINES (optional)
  lanes/<lane>/
    env                               0644  hpc-reader / hpc_reader, SAM_DB_READ_ONLY=1, STATUS_DB_READ_ONLY=1
    env.sam-admin                     0640  csgteam + setfacl g:<admins>:r   hpc-writer, XRAS, MAIL, NOTIFY; READ_ONLY=0
    env.jobhist-sync                  0600  csgteam   jobhist_writer
    env.<cron job>                    0600  csgteam   per-job writers (accounting-comp, accounting-disk, collectors)
```

- **Merge:** concatenate the readable layers, in order, into `mktemp` under `$TMPDIR` (umask 077,
  trap rm), then `--env-file`. Each layer sets final names. No cross-layer `${VAR}` aliases (the
  `JOB_HISTORY_PG_USER=${CIRRUS_PG_USER}` ambiguity the census found).
- **Gate:** `sam-admin` (and any tool listed in `NHD_GATED_TOOLS`) dies with "needs read access to
  `env.<tool>` (group X)" when its overlay is unreadable. It never falls back to the public
  read-only credentials.
- **Regular users:** no `mkdir` when `state/` is not writable. `MPLCONFIGDIR` goes to
  `$TMPDIR`. `NHD_DEBUG=1` prints the layer *names* loaded, never values.
- **Unchanged:** `--cleanenv` stays, and the image ships no `.env`. Cron jobs read the same
  chain, and their overlays win.

## 5. App changes (one PR, sam-queries)

- `sam.session.connect_args(driver, require_ssl, application_name, read_only=False)`:
  - Postgres: `target_session_attrs` is `any` when `read_only`, else `read-write`.
    `read-write` assumed the primary; it refuses a standby and a read-only session.
  - Postgres also gets `options=-c default_transaction_read_only=on`; MySQL gets
    `init_command='SET SESSION TRANSACTION READ ONLY'`.
- `SAM_DB_READ_ONLY` / `STATUS_DB_READ_ONLY` choose it per bind. It is general: any
  context can read through a replica or a reader role.
- `system_status.session`: honor `STATUS_DB_PORT` (the URL ignores a port today).
- `SAMConfig.validate`: don't require `SAM_DB_*` for subcommands that never open SAM
  (`tasks`, `cache`).
- **Peer repo (hpc-usage-queries):** `JOB_HISTORY_PG_READ_ONLY`, the same shape, for the plugin's
  engine. Optional; the role and the replica already enforce it.

## 6. Rollout

| # | who | step | verify |
|---|---|---|---|
| 0 | Claude | privilege census | done (§ 1) |
| 1 | Ben (as postgres) + Claude | `pg_scram_verifier.py --generate` → OpenBao `csg/hpc-reader-pg`; run `create_hpc_reader_pg.sql` | **done 2026-10-09.** 64/64 checks from casper and derecho, on `csg-postgres-ro` (standby) and the primary, in `system_status` and `casper_jobs`: SELECT works; writes refused (read-only), and still refused after `SET default_transaction_read_only = off` (standby on the replica, privilege on the primary); CREATE refused; runaway query cancelled |
| 2 | Claude | § 5 app PR | unit tests per backend; parity capture unchanged |
| 3 | Ben + Claude | write the layered files on GLADE: the § 6a tarball (works with today's loader) | **done 2026-10-09** (r2). `smoke --lane prod` PASS on casper, every step. The installed files match the MANIFEST checksums and modes. r1 broke `hpc-reader`: apptainer's `--env-file` expands `$NAME` even inside single quotes, and that password has a `$`. r2 writes `$` as `\$`, which apptainer turns back into a literal `$`. Last-seen reads `null` until step 2 (the role's read-only default fails libpq's `target_session_attrs=read-write`) |
| 4 | Claude | § 4 wrapper PR + `smoke.sh` cases (plain user, admin ACL, csgteam) | `--help` and a query as each identity; `sam-admin` refused for a plain user; `NHD_DEBUG=1` layer list |
| 5 | Ben | point the module's `sam-search` at the lane `bin/` (rollback: repoint at the conda wrapper) | conda and lane outputs and timings compared side by side first; re-time (ledger 20) with the SIF `.pyc` follow-on |
| 6 | Ben | remove the install-root `.env` once nothing reads it; rotate `pguser` | no reader of the root `.env` left |

**Coexistence:** steps 1–4 touch only `containers/ncar-hpc-deploy/`. The conda path keeps its
install-root `.env` until step 6. The one earlier edit there, optional, right after step 1: swap
its `JOB_HISTORY_PG_*` / `CIRRUS_PG_*` lines to `hpc_reader` on `csg-postgres-ro`. Conda users
keep working, and the world-readable `pguser` copy is gone before the cutover.

### 6a. The GLADE files: a tarball and a finalize script

**Current files (key names read 2026-10-09 via `sudo -u csgteam cat`, values masked):**
- `prod/env` holds one file's worth of everything:
  - `hpc-writer` (SAM) and `pguser` (jobs, via `CIRRUS_PG_*` on the **primary**).
  - `JUPYTERHUB_*` and `STATUS_API_*` (collectors).
  - A commented-out test-instance `hpc-writer` block. **Its password was exposed in the session
    transcript (a masking miss on comment lines): rotate it on test-sam-sql and drop the block.**
- `prod/env.jobhist-sync` re-declares `JOB_HISTORY_PG_*` after its `CIRRUS_PG_*`, so the overlay
  works.
- `dev/env` holds the `sam_dev` owner role, a copy of `pguser`, and the collector and JupyterHub
  tokens.

**Prod lane after the split** (every file sets its final names; no `${}` aliases):

| file | mode | contents |
|---|---|---|
| `env` | 0644 | `SAM_DB_*` = `hpc-reader` on sam-sql, `SAM_DB_READ_ONLY=1`; `JOB_HISTORY_*` and `STATUS_DB_*` = `hpc_reader` on `csg-postgres-ro`, `STATUS_DB_READ_ONLY=1` |
| `env.sam-admin` | 0640 + ACL | `SAM_DB_USERNAME/PASSWORD` = `hpc-writer`, `SAM_DB_READ_ONLY=0` |
| `env.accounting-comp`, `env.accounting-disk` | symlink → `env.sam-admin` | one writer secret, three names |
| `env.collectors` | 0600 | `STATUS_API_*`, `JUPYTERHUB_*` |
| `env.jobhist-sync` | 0600 | `JOB_HISTORY_*` = `jobhist_writer` on the primary |

**Order:**
1. `hpc_reader` exists (step 1). The public `env` may not exist before it does, or it would
   have to carry `pguser`.
2. The tarball (step 4) works with today's loader. The cron jobs get `env` + `env.<job>`, which
   is unchanged behavior with fewer secrets per job.
3. The wrapper (step 3) is what makes the lane usable by a plain user. Before it, a plain user
   still stops at `mkdir`, which is harmless.

The dev lane stays csgteam-only (all 0600) until a dev reader exists on `sam_dev`.

**Build (Claude, on the laptop):**
- The input is the current `lanes/<lane>/env*` files. They are 0600 csgteam with group `---`,
  so benkirk cannot read them even as a csgteam group member. Ben, as csgteam, stages a copy:
  `tar czf ~csgteam/env-snapshot.tgz -C <deploy root> lanes/prod/env lanes/prod/env.jobhist-sync
  lanes/dev/env && chmod 600 … && setfacl -m u:benkirk:r …`. Claude reads it, and Ben deletes
  it after the install.
- Split them into the § 4 layers, add `hpc_reader` from OpenBao, and write the tree under the
  session scratchpad with umask 077. The tree also holds `finalize.sh` and a `MANIFEST` that
  lists, per file, the path, mode, ACL and SHA-256 (no values).
- The tarball never enters the repo; delete the local copy once Ben confirms the install.

**Install (Ben, as csgteam):** `tar xf` into a staging directory beside the install root, then
`./finalize.sh` (dry run by default; `--apply` to write). It:
- refuses unless `id -un` is csgteam, and checks every file against `MANIFEST`;
- backs up each file it replaces to `<file>.bak-YYYYMMDD-HHMM` (0600), and removes nothing;
- installs each file with its mode and ACL (`setfacl --set` for `env.sam-admin`), atomically
  (copy to a temp file beside the target, then `mv`);
- prints the final `ls -l` / `getfacl` table and the `NHD_DEBUG=1` layer list as csgteam;
- `--rollback` restores the newest `.bak-*` of each file.

## 7. Decisions open

- `sam-admin` ACL groups (csgteam plus ...?).
- `hpc-reader` MySQL grant narrowing (§ 3).
- ~~Whether `hpc_reader` reads more than the last-seen tables~~: it reads everything (Ben).
