# Jobs "By Project" everywhere multi-project + entity modals + histogram User|Project pill

**Status (2026-07-27): Unit P (plugin PR #101) OPEN; Unit 1 (SAM tab/drills/
modals) IMPLEMENTED on `redis_and_ux_tweaks`; Unit 2 (histogram pill) BLOCKED
on #101 merge + container rebuild.**

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

## Unit 2 — SAM histogram pill (DO AFTER #101 merge + rebuild)

- `service.jobs_histogram`: `owners_by=None`, forwarded + cached **only when
  set and != 'user'** → soft degradation, no lockstep (old plugin only breaks
  the Project pill click).
- `_render_histogram`: `?owners_by=` param, pill offered only in
  multi-project contexts (machine mode; project mode w/ `jobs_multi_project`).
- `jobs_histogram.html`: User|Project pill; drives all three drill levels —
  stacked segments, bucket owner tables (projcode + modal trigger), per-owner
  jobs drill via `&account=` instead of `&user=`. charts.py unchanged.
- Tests: pill gating, threading + cache key, account-mode drill URLs.

## Verification

Unit 1 Playwright-smoked on webdev (status → Derecho → Job History): both
tabs; By Project 25 rows + "Other beyond top 25 by CPU-hours"; UTAM0017 modal
pop; row drill → `project: UTAM0017` badge + that project's 5,171 jobs;
By User fredc → User Details modal. Full pytest suite green before commit.
