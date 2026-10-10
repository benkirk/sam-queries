# HPC lanes for every user: layered, permission-scoped env

**Status:** in rollout, 2026-10-09. Steps 0–3 done (step 2 = #780, in staging); step 4 (the `lane.sh` loader) next.
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

As built (step 4). Layers are `lanes/<lane>/env`, then `env.<tool>` when the user can read it;
a deeper layer overrides. There is no deploy-root layer: nothing needed one (`TZ` arrives by
`--env`), and the conda `.env` at the install root is never read.

```
lanes/<lane>/
  env                  0644      hpc-reader / hpc_reader, SAM_DB_READ_ONLY=1, STATUS_DB_READ_ONLY=1
  env.sam-admin        0640      csgteam (+ setfacl g:<admins>:r)   hpc-writer, SAM_DB_READ_ONLY=0
  env.accounting-*     symlink   -> env.sam-admin
  env.collectors       0600      csgteam   STATUS_API_*, JUPYTERHUB_*
  env.jobhist-sync     0600      csgteam   jobhist_writer
```

- **Merge:** the readable layers, `awk 1` (a missing final newline cannot fuse two lines), into a
  0600 `mktemp` under `$TMPDIR`, then `--env-file`. It is removed after the run, on `nhd_die`, and
  on HUP/INT/TERM (each trapped into an exit); only SIGKILL leaves it, owner-only. Each layer sets
  final names; no cross-layer `${VAR}` aliases.
- **Gate:** `sam-admin` and `jobhist-sync` (`NHD_GATED_TOOLS`) die with "needs read access to
  `env.<tool>` (group X)" when the overlay *exists* but is unreadable. They never fall back to the
  public layer. An absent overlay is a single-audience lane (dev: one 0600 `env`).
- **Regular users:** nothing is written to `state/`; `MPLCONFIGDIR` goes to `$TMPDIR` when
  `state/` is not writable (`mkdir -p` on the existing lane dirs already succeeds). `NHD_DEBUG=1`
  prints the layer *names* loaded, never values.
- **Also:** an empty image is refused (§ 6b's 0-byte SIF). `--cleanenv` stays, and the image
  ships no `.env`. Cron jobs read the same chain, and their overlays win.

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
| 2 | Claude | § 5 app PR: `read_only` on `connect_args` (`SAM_DB_READ_ONLY` / `STATUS_DB_READ_ONLY`), `STATUS_DB_PORT` | **done (#780, in staging 2026-10-09).** On the dev-lane image with the prod public layer, last-seen returns 6 sources via the primary and via `csg-postgres-ro`. **Prod, 2026-10-09 20:15:** #781 put #779/#780/#782 on `main`; the GLADE checkout pulled to `c020b188`, the prod lane blessed `samuel-main-a99cdb56a06b` (smoke PASS casper + derecho), and `STATUS_DB_SERVER` in `lanes/prod/env` now names `csg-postgres-ro` (backup `env.bak-20261009-201619`). Last-seen returns 6 sources on both hosts; the 20:18 hourly and 20:20 rapid ticks exited 0. The `SAMConfig.validate` tweak was dropped: nobody has hit it |
| 3 | Ben + Claude | write the layered files on GLADE: the § 6a tarball (works with today's loader) | **done 2026-10-09** (r2). `smoke --lane prod` PASS on casper, every step. The installed files match the MANIFEST checksums and modes. r1 broke `hpc-reader`: apptainer's `--env-file` expands `$NAME` even inside single quotes, and that password has a `$`. r2 writes `$` as `\$`, which apptainer turns back into a literal `$`. Last-seen reads `null` until step 2 (the role's read-only default fails libpq's `target_session_attrs=read-write`) |
| 4 | Claude | § 4 wrapper PR, `tests/unit/gates/test_nhd_env_layers.py`, and the SIF `.pyc` (`compileall` in the Dockerfile) | **in review.** Branch wrapper against the live prod lane as benkirk: `sam-search` layers `env`, `sam-admin` `env` + `env.sam-admin`; old vs new wrapper on derecho within noise (3.8 s on prod's pre-#779 image). Plain-user simulation on casper (`env.sam-admin` chmod 000, `state/` 0555): `sam-admin` refused naming the group, `sam-search` works, nothing written, no temp file left. `.pyc`, local read-only container: `import cli.cmds.search` 1.60 s -> 0.51 s. **Real non-csgteam user** (`cmipap`, casper, 2026-10-09, the branch wrapper against the live prod lane): all three secret layers unreadable; `sam-search --help` 1.70 s and `project SCSG0001` 3.03 s rc=0 on `env` alone; `sam-admin` and `jobhist-sync` refused rc=2 naming group csgteam; nothing written under `lanes/prod`, no temp file left; `last_seen` null until #780 is on prod and `STATUS_DB_SERVER` flips. Owed: dev-lane re-time after merge |
| 5 | Ben | point the module's `sam-search` at the lane `bin/` (rollback: repoint at the conda wrapper) | **Timed 2026-10-09** (ledger 20): the conda env with `.pyc` is ~1 s faster than the lane (Casper 1.33 vs 2.32 s, Derecho 1.72 vs 2.73 s for `project SCSG0001`); SIF imports run ~2.2x slower. Alternative: keep users on conda and give its install-root `.env` only the read-only roles (the lane's public layer). That closes the `pguser` exposure (step 6) without the 1 s. Either way, csgteam runs `conda-env/bin/python -m compileall -q src` after each GLADE pull |
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

### 6b. Incident, 2026-10-09 17:06–18:06 (prod lane down)

- **What broke:** at 17:06 two csgteam-owned files were truncated to 0 bytes:
  - `containers/ncar-hpc-deploy/libexec/ncar-hpc-deploy`;
  - the prod lane's current image `lanes/prod/images/samuel-main-c7a66c68ca35.sif`.
- **Effect:** every tick was a silent no-op (exit 0, no log) until the script was restored at
  17:59. The 18:00 and 18:05 ticks then failed with `image format not recognized`.
- **Missed work:** collectors (a status gap of about an hour), `jobhist-sync` rapid (it catches up
  incrementally) and the 17:18 hourly `accounting-comp`.
- **Cause:** not chased (Ben). It coincided with a `git pull` and `source etc/config_env.sh` in
  the install root.
- **Recovery:**
  - `git checkout -- containers/ncar-hpc-deploy/libexec/ncar-hpc-deploy` as csgteam;
  - `ncar-hpc-deploy update --lane prod`, which blessed `samuel-main-2a370bd0d5e3` (`main` @ `581c42f0`).
- **Trap:** run `ncar-hpc-deploy` as `sudo -u csgteam bash -lc '...'`. A bare `sudo -u csgteam`
  has no `/opt/pbs/bin` on `PATH`, so the collectors smoke fails with exit 127.
- **Gap:** `status` does not flag a 0-byte script or image. A tick that does nothing looks healthy
  until `tick.rapid` goes stale.

## 7. Decisions open

- `sam-admin` ACL groups (csgteam plus ...?).
- `hpc-reader` MySQL grant narrowing (§ 3).
- ~~Whether `hpc_reader` reads more than the last-seen tables~~: it reads everything (Ben).
