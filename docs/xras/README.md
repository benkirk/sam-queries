# XRAS integration docs

XRAS is the ACCESS allocations broker at <https://admin-ncar.xras.org/>. SAM runs
the **site-side server** it talks to: XRAS **pushes** allocation decisions in
(`POST /api/xras/v1/actions`) and **pulls** identity and request data back out
(`GET /api/xras/v1/people*`, `/requests/*`).

`incoming/` covers that surface — the Python reimplementation of the legacy
Java/Tomcat server (deployed build 2.0.3), as a drop-in replacement: same URLs,
same auth headers, same response bytes.

`outgoing/` is the **opposite direction**: SAM calling out to the XRAS
Allocations API at `https://api.xras.org/v1/…`. Read-only and GET-only — the
same credential can create requests, modify roles and merge one person into
another, so the client has no verb method but an internal `_get`.

## Live docs — `incoming/`

| Doc | What it is |
|---|---|
| [`XRAS_REIMPLEMENTATION.md`](incoming/XRAS_REIMPLEMENTATION.md) | The reference: wire contract, production data, deliberate divergences, phase status |
| [`XRAS_CUTOVER_RUNBOOK.md`](incoming/XRAS_CUTOVER_RUNBOOK.md) | The day-of sequence. Operational only — no code left. Cutover is **abrupt**: XRAS holds one base URL, so all seven endpoints and all six handlers move at once |
| [`XRAS_TRIAGE_PLAYBOOK.md`](incoming/XRAS_TRIAGE_PLAYBOOK.md) | The week after. Classify a row, then the 422 catalog with the data fix for each. ⚠️ `--recheck` validates, it cannot apply — every fix ends by asking XRAS to re-post |

## SAM → XRAS — `outgoing/`

| Doc | What it is |
|---|---|
| [`XRAS_OUTGOING_QUERIES.md`](outgoing/XRAS_OUTGOING_QUERIES.md) | The account-creation worklist: the readable API surface, every closed path, and the two-feed design. Implemented 2026-08-20; § 0 records what the build learned |

## Shipped — `incoming/implemented/`

As-built records, one per sprint. Each documents what was built, what deviated
from its plan, and the measurements behind the decisions.

| Doc | Phase |
|---|---|
| [`XRAS_SPRINT_A.md`](incoming/implemented/XRAS_SPRINT_A.md) | Action ingestion — `xras_action_log`, the seven schemas, `POST /actions` in capture mode |
| [`XRAS_SPRINT_B.md`](incoming/implemented/XRAS_SPRINT_B.md) | The operator surface — the 4th Allocations tab, replay, `sam-admin xras`, the activation worklist |
| [`XRAS_SPRINT_C.md`](incoming/implemented/XRAS_SPRINT_C.md) | The handlers — all six paths, and the replay-and-diff oracle that proves them |
| [`XRAS_HANDLER_REFACTOR.md`](incoming/implemented/XRAS_HANDLER_REFACTOR.md) | The `ActionHandler` base class the six handlers should have shared, and the six bugs the duplication produced |
| [`XRAS_STRESS_AND_SCHEMA.md`](incoming/implemented/XRAS_STRESS_AND_SCHEMA.md) | Stressing the handlers with the audit row as the assertion target; the remaining `xras_action_log` columns |
| [`XRAS_PRE_DEPLOY_SMOKE.md`](incoming/implemented/XRAS_PRE_DEPLOY_SMOKE.md) | End-to-end smoke on a local stack, incoming action → email |
| [`XRAS_PRE_CUTOVER_TIDY.md`](incoming/implemented/XRAS_PRE_CUTOVER_TIDY.md) | What the stacked sprints left behind |

## Two XRAS docs live elsewhere — on purpose

They are filed by lifecycle, not by topic, and predate this tree:

- [`docs/plans/implemented/XRAS_ACTION_INGESTION.md`](../plans/implemented/XRAS_ACTION_INGESTION.md)
  — the original Sprint A handoff, superseded and retired.
- [`docs/plans/implemented/XRAS_SPRINT_B_FOLLOWUP.md`](../plans/implemented/XRAS_SPRINT_B_FOLLOWUP.md)
  — the activation-worklist anti-spam and re-open mechanism.

Two more that the XRAS work depends on but that are not XRAS-specific:
[`NOTIFICATION_FRAMEWORK.md`](../plans/implemented/NOTIFICATION_FRAMEWORK.md)
(the mailer, Sprint D) and
[`DBA_PRIVILEGE_REQUEST.md`](../plans/implemented/DBA_PRIVILEGE_REQUEST.md)
(the production DDL tickets `zz-90` / `zz-92`).
