# CLI verbs: nested subcommands instead of mode flags

**Status:** follow-on, not started. Decided 2026-10-16 (Ben) while adding the directory
commands on PR #841. The first verb group is `sam-admin directory report`; everything else
is still the flat "one command, many mode flags" shape.

## Why

- jobhist, the lockstep partner (`.claude/rules/cli.md`), already nests: `jobhist history <verb>`,
  `jobhist resource <verb>`. SAM's flat shape is the one that drifted from the shared recipe.
- The flat shape costs eight hand-rolled mutual-exclusion ladders across `cmds/search.py` and
  `cmds/admin.py` (`sum(modes) > 1`), and `--dry-run` means a different thing under each mode
  flag of the same command (`user --restore-deactivation --dry-run` vs `--directory-apply --dry-run`).
- A verb gets its own options and its own `--help`; click enforces required arguments per verb
  (`rbac grant SUBJECT --role R` instead of `--grant SUBJECT --role R`).

## What converts, in order

| Today | Verbs | Why this order |
|---|---|---|
| `sam-admin accounting --machine M --comp [--create-queues --skip-errors]`, `--disk`, `--resource R --verify-paths --reconcile-quotas` | `accounting comp M`, `accounting disk`, `accounting quotas R` | First (Ben: "Accounting too is complicated"): three modes on one command, and its callers are the production cron scripts under `scripts/cron/accounting/`, so it needs the alias window most. |
| `sam-admin tasks --list \| --run-due \| --run NAME \| --history` | `tasks list`, `tasks run-due`, `tasks run NAME`, `tasks history` | Nine flags, the largest ladder; the CronJob's args and `watch-prod` scripts call it. |
| `sam-admin rbac --seed \| --keys \| --effective U \| --diff \| --grant S --role R \| --revoke ID` | `rbac seed`, `rbac keys`, `rbac effective U`, `rbac diff`, `rbac grant S --role R`, `rbac revoke ID` | Required-argument pairs. |
| `sam-admin xras --readiness \| --mnemonic-report \| --contract-report \| --identity-report \| --recheck` | `xras readiness`, `xras mnemonics`, `xras contracts`, `xras identities`, `xras recheck` | |
| `sam-admin user U --validate \| --deactivation \| --restore-deactivation \| --directory-apply [--dry-run]`, `sam-search user U --directory` | `user U` (show, with `--list-projects --directory`), `user U validate`, `user U deactivation`, `user U restore-deactivation [--dry-run]`, `user U directory-apply [--dry-run]` | Entity group with an argument, the jobhist `history --start … <verb>` shape. `project` the same. |
| `sam-admin last-seen USER \| --backfill [--dry-run]` | `last-seen USER`, `last-seen backfill [--dry-run]` | Smallest. |

## How (shims first)

1. Add the verb group beside the flat command; the flat command's body calls the same
   command class, so both forms share one implementation.
2. The flat flags stay for one release, each printing one deprecation line to stderr naming
   the verb form. JSON envelopes do not change (`kind` is per envelope, not per command shape).
3. Move the callers: `scripts/cron/accounting/*.sh`, `Makefile` (`cache --refresh`),
   `.github/workflows/*.yaml` (the CLI smoke), `helm/templates/cronjob-tasks.yaml` (`tasks`
   args), `scripts/repair/RUNBOOK-missing-projects.md`, `scripts/sql/create_samuel_roles.sql`,
   the `watch-*` skills and `.claude/rules/cli.md`. Census 2026-10-16: 23 scripted callers.
4. Drop the aliases; update `src/cli/README.md` "Adding New Commands" to show only the group
   form; check jobhist's `README.md` CLI Architecture section still describes both.

## Out of scope

The `Context`/`BaseCommand` shape, the exit codes and the JSON envelope: the lockstep
contract with jobhist does not change.
