# Disk ingest triage

`sam-admin accounting --disk` sorts every row that it cannot charge into one
category before it writes anything. It then prints one report grouped by
category; with `--format json`, that report is the `disk_import` envelope.

## Categories

| Category | Meaning | Charged | Exit |
|---|---|---|---|
| system account | the reader dropped an OS service account (`SYSTEM_USERNAMES`, `systemd-*`) | no | 0 |
| `known_unowned` | the path is not linked and the label is in `KNOWN_UNOWNED_PROJCODES` (ROOT, RISC, NGIC) | no | 0 |
| `unlinked_directory` | the path is not an active `ProjectDirectory`; resolved through the label as a projcode, or through the one project this file's linked rows give the same label; the account has an active allocation | **yes** | 0 |
| `expired_directory` | as `unlinked_directory`, but the account has no active allocation (post-expiry access window, or not cleaned up yet) | **yes** | 0 |
| `no_project` | the path is not linked, the label matches no projcode, and no single linked sibling shares the label | no | 2 |
| `no_account` | the project has no account on the resource | no | 2 |
| `unknown_user` | a real username that is not in SAM | no | 2 |

Rows in the exit-2 categories are *unexpected gaps*. The rich report always
lists their rows. The informational categories show as counts, and their rows
are listed under `--verbose` (a Campaign_Store file has about 200 unlinked
directories).

- With `--skip-errors` (what the `accounting-disk` lane passes), every other
  row still loads and the run exits 2.
- Without `--skip-errors`, the run refuses the file before its first write.
- `--dry-run` resolves the file too and returns the same exit code as a live run.

The unresolved rows (every category except system accounts) still get a
tier-1 `disk_activity` row, with
`error_comment = unresolved(<category>): projcode=… path=…`.

The sibling rule is what makes Quasar's `/quasar/rda_dr` (label `decs`) charge to
the project `/quasar/rda` is linked to. Tier 3 already charged it that way by
grouping on the label; the report now agrees. A label that is linked to two
projects is not borrowed, so `no_project` is reported.

`KNOWN_UNOWNED_PROJCODES` is a code constant in `src/cli/accounting/commands.py`.
It moves to config only if HSG starts adding labels often.

## `--reconcile-directories`

This flag makes SAM's directory links agree with what the feed shows. It acts
only on `unlinked_directory` entries, where the project has an active allocation
on the resource. `expired_directory` entries are left alone: an ended allocation
whose data is still on disk is expected.

Each `unlinked_directory` entry carries an `action`, chosen from every
`project_directory` row (ended ones included):

| action | when | write |
|---|---|---|
| `reopen` | this project has an ended row for the exact path | `end_date = NULL` (`ProjectDirectory.reopen()`) |
| `rename` | this project has a row whose path differs only in letter case | `directory_name = <path>`, `end_date = NULL` |
| `create` | no row for this path in any case | `ProjectDirectory.create(project_id=, directory_name=)` |
| `review` | the path's only rows belong to another project | none; left for a person |

Measured by a `--dry-run` of the 2026-09-26 files against a prod-like snapshot
(221 distinct directories):

| Category or action | Count |
|---|---|
| `reopen` | 102 (101 Campaign_Store, 1 Quasar `/quasar/rda_dr`) |
| `rename` | 10 (8 Campaign_Store, 2 Quasar: `hpcd`, `ncarlib`) |
| `create` | 12 (6 Campaign_Store, 5 Destor `/lustre/desc1/p/*`, 1 Quasar `/quasar/eol_dr`) |
| `review` | 1 (`/gpfs/csfs1/ncar/ATEC`, an ended row of another project) |
| `expired_directory` | 96 (all Campaign_Store) |

Why the reopens exist: legacy set each row's `end_date` to the allocation's end
at the time (`23:59:59`, last modified in 2025), and later renewals and
extensions did not carry it forward. `project_directory` has no unique key on
the path, so `create` on those rows would duplicate them.

How the flag runs:
- **Dry run.** Without the flag, or with `--dry-run`, the report shows the action
  on each entry and writes nothing.
- **Live run.** With the flag, the writes run in one `management_transaction`
  before charging, deduplicated by `(project_id, path)`, and the envelope carries
  `directories: {reopen, rename, create}` counts.
- Charging is unchanged either way: those rows already charge the same project
  through the fallback.

## Follow-ups

- **Renew and extend do not touch directories.** Clearing `end_date` stops the
  lapse from coming back. If a dated row is ever wanted, renew/extend would have
  to carry it forward.

- **Grouping keys on the input label.** `_group_disk_entries` sums rows by
  `(date, label, user)` *before* resolution, and tier 3 resolves the group through
  its first row's path. Suppose one user has files under an umbrella label (`cgd`)
  in two sub-project directories that map to different SAM projects. Those rows
  collapse into one, and all the bytes go to the first path's project. Tier 1/2 is
  per-directory and correct. The fix is to group on the resolved project.
- **`disk_activity.processing_status` is `bit(1)`** in MySQL but mapped as
  `Boolean`, so a stored 0 reads back truthy through the ORM. Filters on it in
  Python are unreliable until the mapping is fixed.
