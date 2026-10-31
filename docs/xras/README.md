# XRAS integration docs

XRAS is the ACCESS allocations broker at <https://admin-ncar.xras.org/>. SAM runs
the **site-side server** it talks to: XRAS **pushes** allocation decisions in
(`POST /api/xras/v1/actions`) and **pulls** identity and request data back out
(`GET /api/xras/v1/people*`, `/requests/*`).

`incoming/` covers that surface — the Python reimplementation of the legacy
Java/Tomcat server (deployed build 2.0.3), as a drop-in replacement: same URLs,
same auth headers, same response bytes.

`outgoing/` is the **opposite direction**: SAM calling out to the XRAS
Allocations API at `https://api.xras.org/v1/…`. SAM reads XRAS through a GET-only
client (the worklist queries, the preflights, the probes) and writes to it through
one audited sibling (`sam/integration/xras_api/admin_client.py`): the remediations,
the projcode renames and the submission flow. Each write sits behind its own lever,
and each attempt leaves an audit row.

**samuel-dev** (`https://samuel-dev.k8s.ucar.edu`) runs the same loop against
XRAS's demo instance: `xras-admin-demo.xsede.org` posts to it, and it calls
`xras-submit-api-demo.xsede.org` with the production key at its own OpenBao
path (`docs/plans/XRAS_SUBMISSION.md` § 5).

Filing rule: this tree keeps the living references. A shipped plan or sprint
record lives in `docs/plans/implemented/`, whatever its topic; the last table
links the XRAS ones.

## Start here

| Doc | What it is |
|---|---|
| [`PROJECT_AND_ACCOUNT_LIFECYCLE.md`](PROJECT_AND_ACCOUNT_LIFECYCLE.md) | Where a project comes from — ARC → XRAS → SAM, and the internal path beside it. ⚠️ **SAM never originates users**: `users` is mirrored in from an organizational LDAP, which is why the account worklist is a worklist and not a button |

## Live docs — `incoming/`

| Doc | What it is |
|---|---|
| [`XRAS_REIMPLEMENTATION.md`](incoming/XRAS_REIMPLEMENTATION.md) | The reference: wire contract, production data, deliberate divergences, phase status |
| [`XRAS_CUTOVER_RUNBOOK.md`](incoming/XRAS_CUTOVER_RUNBOOK.md) | The day-of sequence. Operational only — no code left. Cutover is **abrupt**: XRAS holds one base URL, so all seven endpoints and all six handlers move at once |
| [`XRAS_TRIAGE_PLAYBOOK.md`](incoming/XRAS_TRIAGE_PLAYBOOK.md) | The week after. Classify a row, then the 422 catalog with the data fix for each. ⚠️ `--recheck` validates, it cannot apply — every fix ends by asking XRAS to re-post |

## SAM → XRAS — `outgoing/` and the living plans

| Doc | What it is |
|---|---|
| [`XRAS_SUBMISSION.md`](../plans/XRAS_SUBMISSION.md) | **Shipped, sprints 1 and 2** ([`XRAS_SUBMISSION_SPRINT1.md`](../plans/implemented/XRAS_SUBMISSION_SPRINT1.md), [`XRAS_SUBMISSION_SPRINT2.md`](../plans/implemented/XRAS_SUBMISSION_SPRINT2.md)); the living design. Authoring requests from SAMuel: Supplement and Extension from the lead's dashboard, a New from a logged-in user, and a New from someone with no SAM account, with its roster. One flow layer serves the PI form and the admin editor. Next: the signed-in New's eligibility filter |
| [`XRAS_POST_AUTOMATION.md`](../plans/XRAS_POST_AUTOMATION.md) | The roadmap for approve-and-post doing everything: phases A to D live, E open |
| [`XRAS_OUTGOING_QUERIES.md`](outgoing/XRAS_OUTGOING_QUERIES.md) | The account-creation worklist: the readable API surface, every closed path, and the two-feed design. Implemented 2026-08-20; § 0 records what the build learned |
| [`REQUEST_EDITOR.md`](outgoing/REQUEST_EDITOR.md) | The request editor on the XRAS Remediations card: the Requested-stage editors are live; the Approved-stage editors are built and dark until an admin-provisioned key exists |
| [`XRAS_OPPORTUNITY_ALLOCATION_TYPE.md`](outgoing/XRAS_OPPORTUNITY_ALLOCATION_TYPE.md) | The `opportunityId` → allocation type mapping table. Implemented 2026-08-20; its CLI audit (§ 5.1) is deferred |
| [`XRAS_WRITE_FIXUPS.md`](outgoing/XRAS_WRITE_FIXUPS.md) | The research behind the write half: what the credential can mutate (merge ✅, unreconcile ❌, withdraw ✅ role-scoped, delete ❌), proven live. Its merge and withdraw controls were built as [`XRAS_REMEDIATIONS.md`](../plans/implemented/XRAS_REMEDIATIONS.md) |

**The probe records** stay under `outgoing/` because the next probe appends to them. Read
them before calling XRAS by hand: re-probing is expensive, and the write probes are destructive.

- [`XRAS_WRITE_PROBES.md`](outgoing/XRAS_WRITE_PROBES.md): what each write verb does against
  production (2026-08-21), the one authorization rule over the whole surface, the role-type
  encoding trap.
- [`XRAS_SUBMISSION_PROBES.md`](outgoing/XRAS_SUBMISSION_PROBES.md): the authoring surface,
  measured on production (2026-09-14 and 09-15).
- [`XRAS_ARC_BASELINE.md`](outgoing/XRAS_ARC_BASELINE.md): one real ARC → XRAS → SAM round trip,
  observed from every surface (2026-09-15).
- [`XRAS_DEV_PROBES.md`](outgoing/XRAS_DEV_PROBES.md): the XRAS-dev demo instance mapped, and
  every samuel-dev test session against it since 2026-10-13.

## Shipped — `../plans/implemented/`

As-built records. Each documents what was built, what deviated from its plan, and the
measurements behind the decisions.

| Doc | What it records |
|---|---|
| [`XRAS_ACTION_INGESTION.md`](../plans/implemented/XRAS_ACTION_INGESTION.md) | The original sprint A handoff, superseded by the as-built record below |
| [`XRAS_SPRINT_A.md`](../plans/implemented/XRAS_SPRINT_A.md) | Action ingestion — `xras_action_log`, the seven schemas, `POST /actions` in capture mode |
| [`XRAS_SPRINT_B.md`](../plans/implemented/XRAS_SPRINT_B.md) | The operator surface — the 4th Allocations tab, replay, `sam-admin xras`, the activation worklist |
| [`XRAS_SPRINT_B_FOLLOWUP.md`](../plans/implemented/XRAS_SPRINT_B_FOLLOWUP.md) | The activation worklist's anti-spam and re-open mechanism |
| [`XRAS_SPRINT_C.md`](../plans/implemented/XRAS_SPRINT_C.md) | The handlers — all six paths, and the replay-and-diff oracle that proves them |
| [`XRAS_HANDLER_REFACTOR.md`](../plans/implemented/XRAS_HANDLER_REFACTOR.md) | The `ActionHandler` base class the six handlers should have shared, and the six bugs the duplication produced |
| [`XRAS_STRESS_AND_SCHEMA.md`](../plans/implemented/XRAS_STRESS_AND_SCHEMA.md) | Stressing the handlers with the audit row as the assertion target; the remaining `xras_action_log` columns |
| [`XRAS_PRE_DEPLOY_SMOKE.md`](../plans/implemented/XRAS_PRE_DEPLOY_SMOKE.md) | End-to-end smoke on a local stack, incoming action → email |
| [`XRAS_PRE_CUTOVER_TIDY.md`](../plans/implemented/XRAS_PRE_CUTOVER_TIDY.md) | What the stacked sprints left behind |
| [`XRAS_REMEDIATIONS.md`](../plans/implemented/XRAS_REMEDIATIONS.md) | The operator write surface on Allocations → XRAS |
| [`XRAS_CLOSED_LOOP.md`](../plans/implemented/XRAS_CLOSED_LOOP.md) | Post automation phase A: SAMuel renames requests; the samuel-dev ↔ XRAS-dev test bed |
| [`XRAS_PHASE_C_AND_TICKETS.md`](../plans/implemented/XRAS_PHASE_C_AND_TICKETS.md) | Phase C: SAMuel sends the award mail, with the cutover checklist [`XRAS_NOTICES_CUTOVER.md`](../plans/implemented/XRAS_NOTICES_CUTOVER.md) |
| [`XRAS_REQUEST_TICKETS.md`](../plans/implemented/XRAS_REQUEST_TICKETS.md) | Phase D: one NUSD ticket per refused post |
| [`XRAS_EXTENSION_INACTIVE_PROJECT.md`](../plans/implemented/XRAS_EXTENSION_INACTIVE_PROJECT.md) | The UCLA0042 incident: an Extension on a project the expiry task switched off |

The rest of the XRAS records (the attention queue, pending users, pending work, identity
merge, push-readiness, contract blockers, the data-model uplift, auto notices, the triage
week) sit beside these under an `XRAS_` name. Two that the XRAS work depends on but that are
not XRAS-specific: [`NOTIFICATION_FRAMEWORK.md`](../plans/implemented/NOTIFICATION_FRAMEWORK.md)
(the mailer, Sprint D) and
[`DBA_PRIVILEGE_REQUEST.md`](../plans/implemented/DBA_PRIVILEGE_REQUEST.md)
(the production DDL tickets `zz-90` / `zz-92`).
