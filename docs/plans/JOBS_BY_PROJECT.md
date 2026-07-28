# Jobs "By Project" everywhere multi-project + entity modals + histogram User|Project pill

**Status (2026-07-27): COMPLETE. Unit P = plugin PR #101 (merged; local
containers carry it); Units 1+2 IMPLEMENTED on `redis_and_ux_tweaks`
(PR #383). All three units Playwright-verified.**

Cross-repo status doc for the round following Job History UX round 3
(`JOB_HISTORY_UX_ROUND3.md`). Ben's asks: By Project wherever the context
spans >1 project; projcode/username cells pop the entity modals ("we could
backport this to user too"); histograms gain a User|Project owners pill in the
same contexts.

## Unit P — plugin `owners_by` (hpc-usage-queries PR #101)

`jobs_histogram(..., owners_by='user'|'account')` — per-bucket `owners`
generalized through `_FACET_SPECS` (GROUP BY `Job.account_id`, account-code
keys). Default `'user'` byte-identical. 213 plugin tests green (8 new).
Branch `jobs_histogram_owners_by` off `main` (`aed06d6`).

## Unit 1 — SAM (works against deployed plugin, no wait)

- `service.jobs_usage_by_project`: `username` now optional; `account_projcodes`
  added (tree scoping); the pin-collision raise applies only when pinned.
  `search_jobs_machine`/`count_jobs_machine` accept a narrowing `account=`.
- `routes.py`: `_render_by_project` mode-parameterized; new routes
  `/machine/<machine>/by-project` (VIEW_ALL_JOB_DATA) and
  `/<projcode>/by-project` (require_project_access, tree-scoped).
  `_jobs_table_response`: machine branch accepts `?account=` narrowing;
  project branch accepts it ONLY in-tree (out-of-tree ignored — tree stays
  the security boundary).
- `jobs_card.html`: machine mode renders BOTH By User and By Project; project
  mode gates By Project on `jobs_multi_project` (computed in the
  resource-details view via `_resolve_scope_projcodes` — tab visibility and
  row scoping always agree); user mode unchanged.
- Entity modals: username cells (By User, gated `can_view_users` — the
  user_card route's VIEW_USERS gate, project_members.py idiom) →
  `#userDetailsModal`; projcode cells (By Project, `can_view_projects or
  user mode`) → `#projectDetailsModal`. Canonical trigger pattern from
  allocations/project_table.html. `resource_details.html` gained the two
  missing modal shells (it extends dashboards/base.html directly).
- Tests: `/by-project` in `_MACHINE_FRAGMENTS`; machine/project by-project
  scoping + drills + badges; modal affordance present/absent by permission;
  both-tabs on status card; shells + tab gate on resource-details; cache
  scope discrimination. Route-map snapshot untouched (jobs blueprint not
  covered).

## Unit 2 — SAM histogram pill (IMPLEMENTED)

- `service.jobs_histogram`: `owners_by=None`, forwarded + cached **only when
  set and != 'user'** → soft degradation, no lockstep (old plugin only breaks
  the Project pill click; the User path never sends the kwarg).
- `_render_histogram`: `?owners_by=` param honored only in multi-project
  contexts (machine mode; project tree > 1 — same gate as the By Project
  tab); round-tripped through the metric/dimension pills via the hidden
  params form (added AFTER drill URLs, which don't take it).
- `jobs_histogram.html`: User|Project pill; drives all three drill levels —
  stacked segments, bucket owner tier (Project header, projcode modal
  triggers, "Other projects"), per-owner jobs drill via `&account=`. The
  owner rows' collapse toggle moved from the `<tr>` to the chevron + stat
  `<td>`s (capture-phase rule, fragments/collapse.html) so the nested
  entity-modal buttons work — user-mode owner cells got the user-modal
  affordance in the same restructure. charts.py unchanged
  (`_jobs_bucket_segments` is owner-shape-agnostic).
- Tests: pill gating (machine / user-ignored / tree-size), soft-degradation
  forwarding, account tier + drill + round-trip input, owner-cell modals,
  cache-key discrimination (explicit 'user' aliases the omitted default).

## Verification

Playwright-smoked on webdev (status → Derecho → Job History):
- Unit 1: both tabs; By Project 25 rows + "Other beyond top 25 by
  CPU-hours"; UTAM0017 modal pop; row drill → `project: UTAM0017` badge +
  that project's 5,171 jobs; By User fredc → User Details modal.
- Unit 2 (container at plugin #101): Wait Times Owner-dimension pill;
  Project pill → per-project bucket tier (UCBK0034 rank 1, 9.5% of band,
  "Other projects (beyond top 10)"); projcode click pops the project modal
  without toggling the collapse; chevron drill → `project: UCBK0034` badge
  + the tier row's exact 50,651 jobs.
Full pytest suite green before each commit (3325 at unit 2).
