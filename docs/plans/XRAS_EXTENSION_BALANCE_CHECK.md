# XRAS Extension balance check

**Status: plan, not started. The zero-balance prefill waits on Ben's sign-off on the
minimums ("For Ben" below).**

## Context

On My Requests a project lead files an Extension (new end date, comments) or a Supplement
(amounts per resource) as two separate forms. Leads often extend a project whose balance is
already spent, and the reviewer then leaves a note asking for a Supplement too, so the lead starts
over. This change adds a balance check to the Extension form:

- **Zero or less:** SAM adds a Supplement for the type's minimum needed to keep access. The lead
  can raise it, and reviewers still approve it.
- **Low (below the guidance level):** the form says so and offers the resource. The lead chooses.

On submit, SAM files the Extension and then, if any amount was entered, a Supplement. Both go
through the existing audited flow. The lead sees one form and one outcome.

## Decisions (Kyle, 2026-11-11)

| Question | Decision |
|---|---|
| Balance source | SAM's DB through the usage kernel. The ARC recommender is a daily batch job over requests already in XRAS's queue, and it reads its usage from SAM (HEUV API), so it cannot answer while the form is open |
| Zero-balance trigger | `remaining <= 0`: the minimum is prefilled, and the wording says it is **requested for review**, not granted |
| Low-balance trigger | `0 < remaining < low-water mark`: a notice, and the resource is offered but not selected |
| Resources | CPU only: `Derecho` and `Casper`. No GPU and no disk for now |
| Low-water marks | Derecho 100,000 for **Small, Small (No NSF award) and Classroom only**; Casper gets no low-balance notice unless Ben says otherwise (it still gets the zero-balance minimum) (award sizes differ about 50x by type; `docs/plans/implemented/XRAS_SUBMISSION_SPRINT1.md` § 4) |
| Zero-balance minimums | **Ben has the final say**, for both Derecho and Casper, per allocation type (see "For Ben" below). Nothing is prefilled until he signs off |
| Upper end of the band (500,000 Derecho) | Not enforced now; see Future |

## For Ben: zero-balance minimums (needs your sign-off)

When a lead files an Extension and a resource's balance is **zero or less**, SAM prefills a
Supplement request for the minimum needed to keep access. Ben has the final say on these values.
Until he signs off, the minimum stays `None`: the form shows the notice and an unselected row,
and SAM prefills nothing.

| Allocation type | Derecho minimum (core-hours) | Casper minimum (core-hours) |
|---|---|---|
| Small | _Ben to set_ | _Ben to set_ |
| Small (No NSF award) (Exploratory) | _Ben to set_ | _Ben to set_ |
| Classroom | _Ben to set_ | _Ben to set_ |
| External Project, CHAP, NSC, Data | _Ben to set, along with their low-water marks_ | _Ben to set_ |

For reference, the seeded `default_amount` is the median award (Small: 500,000 Derecho and
5,000 Casper), so it is too large to use as a "keep access" minimum.

## Balance guidance, below the webapp (`src/sam/xras/submission_preflight.py`)

One per-type table in code, shaped like `XRAS_REQUEST_ROUTES` (`src/sam/xras/routing.py`):

```python
#: SAM allocation-type name -> resource name -> (low-water mark, zero-balance minimum).
#: An unlisted type or resource gets no notice; a None mark gives no low-balance notice;
#: a None minimum offers the row unselected.
BALANCE_GUIDANCE = {
    'Small':                {'Derecho': (100_000, None), 'Casper': (None, None)},
    'Small (No NSF award)': {'Derecho': (100_000, None), 'Casper': (None, None)},
    'Classroom':            {'Derecho': (100_000, None), 'Casper': (None, None)},
}
```

Ben's minimums replace the `None` minimums; Casper's mark stays `None` unless Ben sets one. Until they land, an exhausted resource gets the notice and an
unselected row, the same as the low tier but with different wording. CHAP, NSC, External Project
and Data get rows once Ben gives their values.

`balance_resources(session, project, resources, floor)` works over the line's resources
(`line_resources`, already annotated by `resources_with_limits` with `sam_name` and `limit`). For
each one the table names, it attaches `remaining` and a `balance` tier (`exhausted` or `low`).
An `exhausted` resource with a minimum also gets `prefill=True` and `suggest=<minimum>`. Every
other resource is dropped.

It reads usage through `usage_by_resource` (`src/sam/queries/dashboard.py`) at
`active_at = min(now, floor)`. That way a window that has already ended, the usual case for an
Extension (`extension_floor`), still answers. `project_balances`
(`src/sam/queries/xras_requests.py`) is the model to follow. This is the one usage
computation, not a new sum.

## The flow, Flask-free (`src/sam/manage/xras_submission.py`)

`request_actions(session_factory, drafts, **kw) -> List[SubmissionOutcome]` calls `request_action`
once per draft, in order, and stops at the first one that does not succeed. The order is
**Extension, then Supplement**:

- The Extension is what the lead asked for. If it fails, nothing else is filed.
- A submitted row leaves `OPEN_STATES` (`src/sam/integration/xras_submission.py`), so the
  routing's pending check does not refuse the Supplement, and neither does `open_submission_for`,
  which is keyed per type.
- At ingest the order does not matter. The Supplement lands on `latest_allocation`, and the
  Extension moves that allocation's end date (`src/sam/xras/handlers/supplement.py`).
- If the Supplement stops after the Extension was submitted, its row stays stopped. The existing
  Resume and Abort controls already handle that (`src/webapp/dashboards/user/requests_routes.py`).
- The Supplement is filed only when `submit_allows('Supplement')`. With that lever off, the form
  shows the notices without an amounts table, and the lead is told to file a Supplement
  separately.

## Schema (`src/sam/schemas/forms/xras_submission.py`)

`XrasExtensionRequestForm` gains `amounts`, as on the Supplement form (`load_default=dict`),
parsed by `_parse_amounts` with "none" allowed. An Extension with no amounts is today's request.

## Handler (`src/webapp/dashboards/user/requests_routes.py`, `_RequestForm`)

- `form_input`: fold the `amount_<id>` fields for an Extension, as for a Supplement.
- `_type_context` (Extension): set `resources` from `balance_resources(...)` over
  `_with_limits(config, line_resources(decision.line))`. It is recomputed at every stage (no
  decision rides in the browser), so `_check_limits` refuses a posted id outside the set. The
  existing `max_amount` check still applies.
- `draft` returns the Extension. A new `supplement_draft` builds the Supplement `ActionRequest`
  with the same `request_number`, `request_id` and `SamRefs`. Its comments are a fixed line
  naming the resources and their balances, followed by the lead's Extension comments.
- `_verdict`: preflight both drafts. `_file`: `flow.request_actions(..., drafts)`.

## Templates (`src/webapp/templates/dashboards/user/`)

`fragments/request_form_htmx.html`, Extension branch: one notice per resource, then
`amounts_table` (`src/webapp/templates/dashboards/user/partials/request_bits.html`). The table already honors `prefill`. The
`suggest` value overrides the `default_amount` shown, which is a small change to the macro. The
copy, with units from `fmt_unit`:

- **Exhausted, minimum set:** "No Derecho core-hours remain on this project. SAM will submit a
  supplement request for <Ben's minimum> core-hours with this extension: the minimum needed to
  keep access. XRAS reviewers must approve it before the hours are added. To request a larger amount,
  enter it below."
- **Exhausted, no minimum yet:** "No Derecho core-hours remain on this project. To request more,
  enter an amount below and SAM will submit a supplement request with this extension. XRAS
  reviewers must approve it before the hours are added."
- **Low:** "This project has 80,000 Derecho core-hours remaining, below the recommended 100,000.
  To request more, enter an amount below and SAM will submit a supplement request with this
  extension."

`fragments/request_review_htmx.html` shows the Extension summary, then the supplement amounts.
`fragments/request_outcome_htmx.html` shows one line per action. A stopped Supplement says the
Extension was filed and offers Resume.

## Tests

- `tests/unit/xras/test_xras_submission_preflight.py`: `balance_resources` covering zero,
  negative, low, at the mark, above it, a low Casper balance with no notice, unlisted type (CHAP), GPU skipped, minimum `None` vs set,
  an ended window, and an unmapped resource.
- `request_actions` with a fake admin client: the order, a stop after a failed Extension, and a
  Supplement stopped after a submitted Extension.
- `tests/unit/webapp/test_my_requests.py`: the notices render per tier; the Supplement lever off
  hides the table; a posted id outside the set is refused; submit files one or two drafts in
  order.
- Schema: an Extension with and without amounts.
- A browser smoke test of the Extension dialog (`wire-dashboard-feature` skill).

## Verification

1. XRAS-dev probe first: confirm a Supplement action can be added while an Extension action on the
   same line is `Submitted`. The design doc measured actions on Submitted lines, not two pending
   actions at once.
2. `pytest tests/unit/xras tests/unit/webapp tests/unit/gates`.
3. `docker compose up samuel-dev --watch` with a Small project, once at zero Derecho balance and
   once low: open Extension, check the notice and the rows, review, and submit against XRAS-dev.
   Expect two `xras_submission` rows and two action-log rows.

## Open

- **Ben (blocking the prefill):** sign off on the Derecho and Casper zero-balance minimums per
  allocation type (the "For Ben" table), set low-water marks for the types beyond the three
  small ones, and say whether Casper should get a low-balance notice. The code ships with `None` minimums, so this part stays off until he decides.

## Future

- Cap what a lead may request in a Supplement at the upper end (500,000 Derecho
  for the small types), using the ARC recommender's per-request recommendation to enforce it.

## Docs

- Add an Extension row for the balance check to `docs/plans/XRAS_SUBMISSION.md` § 4.1.
- Add a glossary line if the copy names the check.

## Rollout

Each step waits for Kyle's go-ahead: implementation, commit, push, PR to `staging`.
