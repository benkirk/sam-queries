# XRAS Remediations — the pending-work queue

**Status: BUILT 2026-08-24 on PR #482.** The Remediations card defaults to
the same population as XRAS admin's "Recent submissions" list; a
**Show everything** switch restores the full swept set with the date filter.

## What XRAS admin's list keys on (measured 2026-08-24)

Captured from a logged-in admin-ncar.xras.org session. The dashboard is
server-rendered — no XHR to read a filter from — so the rule was inferred by
comparing its 48 rows against the sweep snapshot:

- **It is state, not a date window.** "Last Updated" ran from 33 minutes to
  6 weeks. 39 rows had a blank status (still in review), 9 read `Approved`.
- The blank-status rows are requests with an action in `Submitted` /
  `Under Review`; the `Approved` rows are approved-and-not-yet-posted (all
  `New`, matching the contract-blockers table). Extensions posted that
  afternoon had dropped off.
- The public and admin GET APIs expose **no** posted/notified flag —
  `actions[].states` is the review workflow (`Conflicts Verified`,
  `Reviewers Assigned`) on every action. That state lives in XRAS admin.

## The predicate — `is_pending_work()` in `sam/queries/xras_requests.py`

A request is pending work when any action the sweep checked (has a
`preflight`) is:

| condition | why |
|---|---|
| `action_status` in `Submitted` / `Under Review` | in flight |
| `Approved` and `push_state == 'pending'` | a New with no SAM project |
| `Approved`, `push_state == 'unknown'`, entered on/after `XRAS_REPOINTED_ON` (2026-08-24) | after the repoint, no log row means never posted; before it, legacy SAM took the post and this log cannot see it |

An action with no `preflight` is outside the sweep's window and is not
recent work — that is what keeps months-old in-review requests, which XRAS
admin does not list, out of the queue.

**Result against their 48: 46 matched, 1 extra, 2 missed.** The extra
(NCAR4277) is a local-DB artifact — the local `xras_action_log` has none of
the day's prod rows, so a posted New still reads `pending`; on prod it is
`seen_in_log`. The two misses (UMIT0073, UPSU0053) carried an in-flight
action the sweep had not pulled — a sweep-coverage gap, not a rule gap.

## The card

- Default (`show_all` absent): the queue, **no date window** — an old
  approval nobody pushed is the point. Header badge reads `N pending` plus
  `M more with Show everything`.
- `show_all=1`: every swept request with the shared date filter, the
  `outside the date filter` badge, and "Check all" for unchecked rows (which
  are never pending work, by definition).
- The switch is bound to the hidden filter form like the search box, so a
  chip click keeps the mode, and the batch re-check reads it from the POST
  body. Absent means off (CLAUDE.md § 10).
- `sam-admin --format json xras --readiness` rows carry `pending_work`.

## Follow-ups

- Posted-but-not-notified is not in the predicate: XRAS admin drops a row
  once it is posted *and* notified, and SAM knows "notified" only through
  `xras_activation_event`. Join it if the queue proves too eager.
- **The notified half is a cross-system question.** XRAS's "notified" is its
  own record, and with SAM sending the handoff mail nothing sets it, so
  posted rows will linger in their queue. Batched for Steve
  (`XRAS_TRIAGE_WEEK.md` § Questions for Steve, item 4); the answer decides
  whether SAM's Notify path should call XRAS back or the predicate should
  join `xras_activation_event` instead.
- The two misses: check why the sweep's request pull lacks the newest action
  for a request with an in-flight renewal.
