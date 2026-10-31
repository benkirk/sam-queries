# XRAS submission sprint 3: the request types under Allocations > XRAS

**Status:** built 2026-10-31 on `xras-submission-sprint3` (from `origin/staging` at the #883
merge), one PR to `staging`, issue #884: refactor commits first, one commit per step with its
tests. The dev proof in § C waits on the merge; this file moves to `implemented/` in the window
sweep after it.
Builds on sprint 2 (`docs/plans/implemented/XRAS_SUBMISSION_SPRINT2.md`, #880) and sweep 33 (#883).

## Context

Sprint 2 put the allocation types in front of a visitor as cards (`/request/`), with SAM's own copy
in `allocation_type_guide` and the per-resource bounds in `allocation_type_resource_limit`. This
sprint does three things:

1. **The guide and limit CRUD moves to Allocations > XRAS > Configuration.** Admin > Configuration
   is the read-only system snapshot (`admin/configuration_routes.py` docstring, subtitle "Read-only
   system snapshot"), so nothing there mutates. Writes gate on `MANAGE_XRAS` (nusd and csg under
   the defaults); views follow the XRAS tab, `VIEW_XRAS`.
2. **The signed-in New picks its type from cards, filtered by eligibility.** A user with a current
   `user_organization` and no current `user_institution` is NCAR staff (legacy's internal rule,
   `sam/queries/xras_access.py:81-83`) and sees `audience` `ncar` plus every type with no audience.
   Anyone else is university and sees `university` plus every type with no audience. The other
   audiences' types stay reachable behind a disclosure. XRAS's `can_submit` stays the per-user
   open/closed answer on each card.
3. **The admin page is the request page, editable.** One card per type as the PI sees it, with Edit
   (the guide), a pencil per limit row, a "Preview as" audience switch, and the SAM types that have
   limits but no guide shown as cards too, so nothing is hidden.

## Decisions (Ben, 2026-10-31)

- Writes on the new page: `MANAGE_XRAS`. Views: `VIEW_XRAS`.
- Page shape: cards as the request page, editable, with "Preview as"; unguided types with limits shown.
- Eligibility: legacy's internal rule (current organization, no current institution: NCAR staff).
  The other audiences' types stay reachable behind a disclosure; XRAS's `can_submit` stays per user.
- The audience vocabulary is a registry keyed per audience (`sam/xras/audiences.py`), broad enough
  for a facility that does not use XRAS today (Wyoming, WNA) to join as one registry row plus its
  guide rows, with no schema change (`audience` is `String(16)`).
- Picker: a first stage inside the existing `lg` request dialog, compact radio cards; then the
  describe page with the type fixed.

## What exists

| Piece | Where | Note |
|---|---|---|
| The cards' data | `sam/xras/allocation_overview.py` `allocation_type_overview(session, opportunities, *, rules_for, resource_keys, eligible=None)` returns `TypeCard`s (frozen: `title summary funding cadence notes opportunity resources rules reason`; `amounts`, `access`, `is_open`) | 4 queries flat in cards (perf pin `test_request_overview_route`). `eligible=` has no production caller and can only narrow `public_guides`, which already drops `ncar`. |
| Guide rows | `sam/accounting/allocation_type_guide.py` (`AUDIENCE_LABELS`, `AUDIENCES`, `AUDIENCE_NCAR`, `public_guides`) | Seed: Small, Exploratory, Classroom, Data Analysis, Large `university`; External NULL; NSC `ncar`. One row per XRAS type (unique key). |
| Limits | `sam/accounting/allocation_type_limits.py` (`limits_for_allocation_types`, `check_bounds`) | Keyed by SAM `allocation_type_id`; the guide keys by `xras_allocation_type_id` plus a SAM type name. |
| The visitor overview | `templates/register/request_overview.html:34-88` (inline card markup), `static/css/register.css` `.request-type*` (loaded only by `base_register.html`) | Square cards; closed is `.is-closed`; cadence is the one pill. |
| The CRUD | `dashboards/admin/allocation_limits_routes.py` (two `CrudSpec`s, two card routes, `VIEW_SYSTEM_CONFIG` / `SYSTEM_ADMIN`), `admin/fragments/allocation_{limits,guides}_card.html`, `allocation_limit_bounds.html` (`bounds_row`), `allocation_guide_fields.html` (`guide_fields`), `create_/edit_allocation_{limit,guide}_form_htmx.html`; shells on `admin/configuration.html`; reload map `htmx-config.js` (`reloadAllocationLimitsCard`, `reloadAllocationGuidesCard`) | `register_crud(bp, spec)` takes any blueprint. |
| The XRAS section | `/allocations/xras` (`allocations/xras/card_routes.py`, `VIEW_XRAS`, not facility-scopable); tabs via `page_tabs` in `allocations/base_allocations.html`; navbar `webapp/utils/nav.py` (`active_endpoints`) | The inner `#xrasWorklistTabs` is pinned to two tabs (`TestItIsACardNotATab`): the new strip is `page_tabs`, never a third inner tab. |
| Permissions | `sam/security/permissions.py` (`VIEW_XRAS`, `MANAGE_XRAS`, `ADMIN_XRAS`), `rbac_defaults.py` (`MANAGE_XRAS` in `_ALLOCATION_ADMIN`) | No XRAS permission is facility-scoped. |
| The signed-in New | `dashboards/user/requests_routes.py` `NewRequestForm` (seams in its docstring; `gate()` builds `opportunity_options`), `my_request_new_fragment`, `my_request_new_opportunity_fragment`; templates `request_new_htmx.html`, `request_new_opportunity_htmx.html`, `request_new_inputs_htmx.html` | `#xrasRequestModal` is `modal_scaffold(size='lg')` in `my_requests.html`; fragments bring their own `.modal-body` / `.modal-footer`. The visitor's subclass `_VisitorNewRequest` (`register/allocation_request.py`) sets `request_urls['fixed_opportunity']`, which hides both the select and the SAM-user pickers. |
| The card-picker idiom | `.xras-candidate` (`xras.css`): a `label.list-group-item` as the radio, `:has(.form-check-input:checked)` draws the selection | `allocations/partials/xras_merge_form.html`. |
| Affiliation | `sam/xras/extractors.py` `best_organization` / `best_institution` (first current row) | No `is_staff` helper exists. |

## Design

**Subject:** a short menu of allocation programs, each a small contract (who, how much, how often,
what to attach). Audience: PIs choosing once a year, and allocation admins editing copy and limits.
Primary job: make the choice legible in one glance, and show the admin exactly what the PI sees.

**Tokens:** SAM's own, no new literals. Poppins; square corners; `--surface-raised` card on
`--surface-card`; `--text-heading` title; `--text-secondary` facts; `--text-link` the one accent.
The memorable element is the **limits ledger**: each resource on its own line, name left, "typical"
and "up to" as right-aligned tabular figures (`fmt_number`), the access-only resources folded into
one quiet sentence. An open type carries a 3px inset rule in `--text-link` on its left edge (the
`.xras-action.is-latest` idiom); a closed one has none, and its reason sits where the button would
be. The cadence stays the one pill. No eyebrow labels, no numbered markers (the types are not a
sequence), no icon per fact beyond the existing four.

**One macro, three variants**, `dashboards/fragments/type_cards.html`:

```
{% macro type_card(card, variant='full', pick_name=None, start_url=None, admin=None) %}
```

- `full` (the visitor overview): title and cadence, summary, facts, ledger, notes, Start.
- `pick` (the New dialog): a `label.type-card-pick.xras-candidate` wrapping a radio, two lines:
  title and cadence; then "NSF award required · Derecho up to 1,000,000 · Casper up to 50,000".
  A closed card's radio is `disabled`, carries no value, and its reason replaces line two.
- `admin` (the Configuration page): `full` plus Edit in the head, a pencil per ledger row (the limit
  edit modal), "Add a limit" under the ledger, the audience as a `state_tag`, and the XRAS state as
  the last line. A type with no active guide row is a card tagged "Not on the request page", titled
  with the SAM type name; "Publish on the request page" opens the guide create modal with
  `allocation_type` preset, or Edit when an inactive guide row exists (the unique key forbids a
  second row).

```
 full                                  pick                                   admin
┌───────────────────────────────┐  ( ) Small      Continuous · NSF award  ┌ Small  (Continuous)  [Univ.]  Edit ┐
│ Small          Continuous     │      Derecho up to 1,000,000 · Casper … │ For university researchers …        │
│ For university researchers …  │  (●) Exploratory  no award needed       │ ⚑ NSF award  ⌚ 12-month award      │
│ ⚑ NSF award   ⌚ 12-month      │      Derecho up to 100,000 · …          │ Derecho     100,000    1,000,000  ✎ │
│ Derecho     100,000  1,000,000│  (x) Large  Spring call · not open now  │ Casper       10,000       50,000  ✎ │
│ Casper       10,000     50,000│                                         │ Add a limit                         │
│ [Request Small]               │  ▸ Other allocation types               │ ● Open in XRAS: Small (University)  │
└───────────────────────────────┘                                         └─────────────────────────────────────┘
```

The admin ledger lists the type's SAM limit rows (every resource with a row, active or not, an
inactive one marked), because those are what the pencils edit; the visitor ledger lists what XRAS
offers, annotated with the limits.

**CSS:** the `.request-type*` rules leave `register.css` for `static/css/request-types.css`, renamed
`.type-card*`, loaded by `base_register.html`, `my_requests.html` and the new page. The pick
variant shares `.xras-candidate`'s rules by class, no second `:has()` rule. The open rule is an
inset `box-shadow`, so open and closed cards keep one width. Phone (390 px): the ledger stays a
table with right-aligned tabular figures; a pick card holds two lines, its second (funding, each
amount's ceiling, the access-only resources) cut with an ellipsis and whole in `title`; nothing
breaks mid-word.

**Copy (sentence case, active):** "Request Small"; "Not open for requests now."; "Other allocation
types"; "Not on the request page"; "Publish on the request page"; "Preview as". The admin page's
lede: "What a PI sees before starting a request. Edit the words here; the amounts come from the
limits."

**The New dialog's flags:** both doors now reach the describe page with the type fixed, so the
describe page always carries the opportunity as a hidden input. The SAM-user pickers (project admin,
the roster search) are a separate handler attribute, off for the visitor, since a visitor may not
search SAM users. The "change" link back to the type stage shows only where a type stage exists.

## Build plan

### R. Refactors (no visual change)

| # | Commit | What |
|---|---|---|
| R1 | the macro | `type_cards.type_card(card, variant='full')` extracted from `request_overview.html`; the overview calls it. `request-types.css` holds the renamed `.type-card*` rules; `register.css` loses them; `base_register.html` loads the new sheet. CSS dead and tokens gates; `ui_snapshots --element .type-card-list` on `/request/` before and after, `--compare-pixels`. |
| R2 | the audience registry and read | `sam/xras/audiences.py` (Flask-free): `Audience(key, label, applies)` rows in `AUDIENCES`: `university` ("University", the floor) and `ncar` ("NCAR staff", `best_organization(user)` and no `best_institution(user)`). A Wyoming entry is one more row when the day comes (`applies`: the current institution's `state_prov` is Wyoming), with its own guide rows. `user_audiences(user) -> frozenset[str]`: every key whose rule applies, `university` when none does. A guide row keeps one `audience` (NULL is everyone); breadth lives on the user's side. `AUDIENCE_LABELS` on the guide model derives from the registry, so the guide form's select and the "Preview as" switch read it. `guides_for_audiences(session, audiences)` in `allocation_type_guide.py`: active rows whose `audience` is NULL or in the set, display order (`None` means every audience); `public_guides` is `guides_for_audiences(session, VISITOR_AUDIENCES)`. `allocation_type_overview(..., audiences=None, include_unguided=False)` replaces `eligible=`; `TypeCard` gains `audience`, `guide` and `allocation_type`; `include_unguided` adds a card per active SAM `AllocationType` with a limit and no active guide. Visitor route and its perf pin unchanged. |
| R3 | the classifier's tests | `user_audiences` over org only (`{'ncar'}`), institution only, both (the institution wins, as legacy's `COALESCE`), neither (all `{'university'}`), with the core factories; every seeded `audience` value is a registry key; a row added to `AUDIENCES` reaches the form select and the preview switch. |

### A. The Configuration page

| # | Commit | What |
|---|---|---|
| A1 | the page and strip | `GET /allocations/xras/configuration` (`allocations_dashboard.xras_configuration`, `login_required` + `require_permission(VIEW_XRAS)`), `dashboards/allocations/xras_configuration.html` extending `base_allocations.html`; a `page_tabs` sub-strip `Work queue | Configuration` shared by `xras.html` and the new page (`_xras_strip.html`); the XRAS tab gains `active_endpoints` in `base_allocations.html` and `nav.py`. Body: lede, the "Preview as" switch (`btn-check` radios: Everyone, then the registry), the cards section. `view_only_client` lifts to the XRAS conftest. Tests: `tests/unit/xras/test_xras_configuration.py`, route map, the modal-shell page map. |
| A2 | the CRUD moves | The two `CrudSpec`s re-register on `allocations_dashboard` in `dashboards/allocations/xras/configuration_routes.py` (slugs unchanged; templates under `dashboards/allocations/partials/`), edit and create only, both `MANAGE_XRAS`: the edit form's Active box retires a row, so no delete route. The four form fragments and `bounds_row` / `guide_fields` move (`git mv`); the four shells move to the new page under `MANAGE_XRAS`; `allocation_limits_routes.py`, the two table cards, and `configuration.html`'s sections, shells and reload-map entries go; the two admin test modules move to `tests/unit/xras/` (`test_xras_type_guides.py`, `test_xras_type_limits.py`) with the new URLs. |
| A3 | the cards fragment | `GET /allocations/xras/configuration/cards?audience=` renders `type_card(variant='admin')` over `allocation_type_overview(..., audiences=..., include_unguided=True)` with the live opportunities; an outage says so once and every card's state line reads "XRAS is not answering." `webapp/utils/xras.py` holds the reads (`report_opportunities`, `type_cards`), shared with the visitor overview and the New dialog. Edit opens the guide edit modal, each ledger row the limit edit modal, "Add a limit" the limit create modal preset with the type's first id, an unguided card the guide create modal preset with `allocation_type` and `title`. The section reloads itself on both reload events, carrying the switch. Tests: each audience, the retired and unguided cards, the presets, an open type, buttons hidden without `MANAGE_XRAS`. The fragment joins `test_admin_table_conventions.CARDS`, the page `PAGE_SETS['wraps']`. Perf pin `xras_configuration_cards_route`: 15 (6 for the cards), flat. |

### B. Eligibility in the New dialog

| # | Commit | What |
|---|---|---|
| B1 | the type stage | `NewRequestForm` gains a type stage before `describe`: `my_request_new_fragment` renders `request_new_type_htmx.html` (`request_title('New request')`, lede "Choose the type of allocation.", `type_card(variant='pick', pick_name='opportunity_id')` per eligible card, closed cards disabled with the reason, the `<details>` "Other allocation types" holding the rest, `htmx_form` footer Next). One `type_cards(new_request_opportunities(username), audiences=None)` read, split by `card.audience` against `user_audiences(pi)`. Posting `stage=type` (`choose_type`, answered before the schema: the choice is its only input) renders the describe page with the type fixed and "Requesting Small (University) Change type", or the stage again with "Choose one of the open types." The dropdown, `my_request_new_opportunity_fragment` and its out-of-band inputs go; `names_sam_users` and `_type_url` are the visitor's seams. A separate commit turns the XRAS cache off during each `armed` My Requests test: a cached fake catalog failed a Supplement test once the new tests moved the xdist distribution. |
| B2 | tests and proof | `TestTheNewForm` on committed guide rows of its own (`committed_guide`, root conftest): a staff PI sees the staff and everyone types first and the university type disclosed; a university PI the reverse; a closed card is disabled with XRAS's reason; the real classifier decides for benkirk; an outage lists the types disabled. Perf pin `user_my_requests_new_route`: 26, flat. `ui_snapshots_modals.json`: `my-requests-new-type`, `xras-configuration-cards`. |

### C. Close

- `XRAS_SUBMISSION.md`: Status names sprint 3; § 5 "Where it is edited" names Allocations > XRAS > Configuration; the New's picker is the type stage.
- Ledger entry 33's W9 closes with the page; D1 (`lazy_card`) stays open unless this page makes it the third copy worth folding.
- This plan's Status flips to shipped and it moves to `implemented/` in the window sweep after the merge.
- Dev proof on samuel-dev (`XRAS_SUBMIT_ANONYMOUS_ENABLED=1`): the page edits a guide and a limit and `/request/` reflects both; `benkirk` (organization, no institution) opens New and sees NSC among the eligible; a university account sees Small.

## Gates that will trip

`test_route_map_parity` (10 admin endpoints out, 8 allocations endpoints and the page and fragment in), `test_modal_shell_contract` (shells move pages;
`HTMX_FRAGMENT_SHELL_DEPS`), `test_template_csp_lint`, `test_css_tokens` / `test_css_dead` (the
sheet move), `test_static_assets`, `test_nav`, the perf pins, `test_admin_table_conventions`,
`TestItIsACardNotATab` (untouched), `test_layer_imports` (`sam/xras/` stays Flask-free).

## Verification

- Every commit: its tests; the whole suite once on 3307 (`test_access_control_decorators.py` and
  `test_nhd_env_layers.py` fail locally only; `TestDispatchArms` flakes under `-n 4`).
- R1: `scripts/ui_snapshots.py --element '.type-card-list' --page /request/` on the base and the
  branch, `--compare-pixels`: identical.
- A and B: `ui_snapshots.py --recipes` light and dark at 1440 and 390; `--midword --width 360` on the
  page and the dialog; `--headers` on the ledger; pick-card heights with `getBoundingClientRect`
  (two lines each at 390 px).
- `pytest -m perf -n 0` for the pins; the dev proof in § C.
