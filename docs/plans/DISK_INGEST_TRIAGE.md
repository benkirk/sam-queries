# Disk ingest triage

`sam-admin accounting --disk` sorts every row that it cannot charge into one
category before it writes anything. It then prints one report grouped by
category; with `--format json`, that report is the `disk_import` envelope.

## Categories

| Category | Meaning | Charged | Exit |
|---|---|---|---|
| system account | the reader dropped an OS service account (`SYSTEM_USERNAMES`, `systemd-*`) | no | 0 |
| `known_unowned` | the path is not linked and the label is in `KNOWN_UNOWNED_PROJCODES` (ROOT, RISC, NGIC) | no | 0 |
| `unlinked_directory` | the path is not a `ProjectDirectory`; resolved through the label as a projcode, or through the one project this file's linked rows give the same label | **yes** | 0 |
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

## Follow-ups

- **`--link-missing-directories`.** Each `unlinked_directory` entry carries
  `project_id` and `path`, which is exactly what
  `ProjectDirectory.create(session, project_id=, directory_name=)` needs. The flag
  would make that call inside `management_transaction`. Until it exists, the fix is
  manual: Admin → project → Directories → add.
- **Grouping keys on the input label.** `_group_disk_entries` sums rows by
  `(date, label, user)` *before* resolution, and tier 3 resolves the group through
  its first row's path. Suppose one user has files under an umbrella label (`cgd`)
  in two sub-project directories that map to different SAM projects. Those rows
  collapse into one, and all the bytes go to the first path's project. Tier 1/2 is
  per-directory and correct. The fix is to group on the resolved project.
- **`disk_activity.processing_status` is `bit(1)`** in MySQL but mapped as
  `Boolean`, so a stored 0 reads back truthy through the ORM. Filters on it in
  Python are unreliable until the mapping is fixed.
