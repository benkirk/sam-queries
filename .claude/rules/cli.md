---
paths:
  - "src/cli/**/*"
  - "tests/unit/cli/**/*"
---

# CLI: sam-search and sam-admin

Loaded with `src/cli/`. Architecture and how to add a command: `src/cli/README.md`.

## Related projects

**hpc-usage-queries** (`/Users/benkirk/codes/hpc-usage-queries/devel`) ships
the `jobhist` CLI for PBS job history, charging, and daily summaries. SAM
loads it as an optional plugin via `require_plugin(HPC_USAGE_QUERIES)` —
see `src/cli/core/base.py` and `sam/plugins.py`. The two CLIs share
architectural conventions deliberately:

- Same `Context` / `BaseCommand` hierarchy shape
- Same exit codes (`EXIT_SUCCESS=0` / `EXIT_NOT_FOUND=1` / `EXIT_ERROR=2` / `EXIT_KEYBOARD_INTERRUPT=130`)
- Same JSON envelope conventions (top-level `kind`, ISO-8601 dates, `float(Decimal)`, sorted sets, `indent=2`, `sort_keys=False`)
- Same `rich` / `json` stdout formats; jobhist alone adds an `ExporterRegistry` for file exports (`dat` / `csv` / `md` / `json-file`)

If you change the shape of the JSON envelope or
the `EXIT_*` codes in this repo, check the parallel structures in
`hpc-usage-queries/devel/job_history/cli/` before merging — and update
both repos in lockstep. See `hpc-usage-queries/devel/job_history/README.md`
§ *CLI Architecture* for the canonical recipe (mirrored in SAM's
`src/cli/README.md`).

## CLI Tools: sam-search & sam-admin

Modular class-based CLI (`src/cli/` — see its README for the architecture and
how to add commands). Command classes encapsulate logic; display functions are
stateless; admin commands extend search commands via inheritance. Exit codes:
0 success / 1 not-found / 2 error / 130 interrupt.

```bash
# Search
sam-search user benkirk --list-projects --verbose
sam-search user benkirk --directory ; sam-search project SCSG0001 --directory   # the UCAR directory's record + SAM's drift (key absent when unconfigured)
sam-search user --search "ben%" ; sam-search user --abandoned [--not-seen-since 1y --source pbs]
sam-search project SCSG0001 --list-users --verbose
sam-search project --upcoming-expirations --list-users
sam-search allocations --resource Derecho --total-facilities --total-types
sam-search contracts AGS-1852977 --list-projects        # SAM's contract table
sam-search contracts --search climate [--all] --pi poulsen --source NSF
sam-search awards AGS-1852977                           # ask NSF/USAspending, cross-ref SAM
sam-search awards --search turbulence                   # composite free-text
sam-search accounting --last 7d --user benkirk          # daily rollups (no plugin)
sam-search accounting --jobs --last 7d --user benkirk   # per-job (job_history plugin)
sam-search accounting --jobs --last 365d --job-id 6049117[28].desched1
sam-search --format json project SCSG0001 | jq          # JSON envelopes everywhere

# Admin (superset of search)
sam-admin user benkirk --validate ; sam-admin user <u> --deactivation | --restore-deactivation [--dry-run]   # the identity sync's closure undo
sam-admin user <u> --directory-apply [--dry-run]        # patch from the directory per DIRECTORY_APPLY_<KIND>; JSON refused for the live write
sam-admin directory report [--kind K] [--limit N]       # the directory sweep: counts by kind and category (a verb group, see below)
sam-admin project SCSG0001 --validate ; sam-admin project SCSG0001 --reconcile   # reconcile = everyone on every resource
sam-admin project --reconcile-lead-admin --all --dry-run   # bulk, active resources only; drop --dry-run to write
sam-admin accounting --disk --dry-run                   # summary rebuild/reconcile ops
sam-admin xras --readiness | --mnemonic-report | --contract-report | --identity-report   # XRAS boards (docs/xras/)
sam-admin rbac [--seed | --keys | --effective USER | --diff | --grant SUBJECT --role R | --revoke ID]   # the samuel_role_* catalog
sam-admin tasks [--list | --history | --run NAME | --run-due] ; sam-admin last-seen USER | --backfill   # task dispatcher; sightings ledger
# Cache refresh: POSTs /api/v1/admin/cache/refresh (webapp + Redis, NOT the DB); needs SAM_API_USER/PASS.
sam-admin cache --refresh [--category flask|chart|usage|scans|jobs|awards|xras_api]
```

`--job-id` matching: digits match the scalar job and every array element; a partial array form prefix-matches across hosts; a full id with host is exact. `sam-search user --has-active-project` is the inverse of `--abandoned`: active users with at least one active project.

✅ **DO** use proper exit codes (0, 1, 2, 130)
✅ **DO** give a new multi-verb command a click group with verbs (`sam-admin directory report`),
   not mode flags; jobhist already nests. The flat commands convert later: `docs/plans/CLI_VERBS.md`.
