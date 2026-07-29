# Resource-details usage card — History | By User tabs

Status: **in progress** — single PR vs `staging`, branch `job_history_top_cards_redux`.

## Framing

`/user/resource-details/<projcode>?resource=Derecho` is SAM's *own* compute-usage
view: it reads `comp_charge_summary` directly and needs no `hpc-usage-queries`
plugin. The SAM-side content had grown into three sibling collapse cards, with the
date-range picker stranded above an unrelated project tree:

```
date_range_picker (own card)   <- controls everything below, but…
Project Hierarchy              <- …this sits between it and its targets
Usage Trend      (#collapseUsage,   bar chart, lazy htmx fragment)
Historical Usage (#collapseCharges, daily table, rendered inline)
Usage by User    (#collapseUsers,   per-user table, rendered inline)
```

Three cards is a lot of vertical scroll for one question asked two ways — *when*
was the resource used, and *by whom*. The plugin-backed Job History card
immediately below already answers that as one card with tabs plus a period control
in the tab strip, which left the SAM-side section reading as the odd one out.

After:

```
Project Hierarchy                                   <- moved ABOVE the card
+-------------------------------------------------------------+
| [History] [By User]    Epoch 7d 30d [90d] 6mo 1yr  [Custom]  |
+-------------------------------------------------------------+
|  History : stacked/flat bar chart + Historical Usage table   |
|  By User : NEW usage pie + Usage by User table               |
+-------------------------------------------------------------+
Job History (plugin card, unchanged)
```

## Decisions

1. **The pills keep full-page navigation.** `pickers.js` still computes
   `start_date`/`end_date` and reloads. That window governs the tree badges, this
   card *and* the Job History card — one control, one reload, nothing goes stale.
   An htmx card-shell swap (the jobs-card model) would have left the tree badges
   and the jobs card showing the *previous* window on the same screen.
2. **The pills render inside the tab strip**, right-aligned, `Custom` toggling a
   thin date-input row beneath. This is what forced the `date_range_picker` split
   below; the macro had baked its own `card` wrapper.
3. **Tree badges stay window-scoped.** `get_charges_by_projcode` still runs over
   the selected range; the tree card header says so, since tree-above-pills would
   otherwise imply independence.
4. **The History chart stays stacked-by-user.** Its legend username links now
   activate the By User pane before expanding the row, instead of being dropped in
   favour of the pie.

## What this reuses

Nearly all of it already existed; the feature is mostly assembly.

| Need | Existing thing |
|---|---|
| Card tabs w/ server-side active tab | `_trig`/`_tabcls`/`_panecls`, `partials/jobs_card.html` |
| Pills in a tab strip | `jobs_card.html` (`<li class="nav-item ms-auto">`) |
| Pie w/ clickable wedges + legend | `generate_disk_entity_pie_chart`, `dashboards/charts.py` |
| Top-N + inert "Other" slice | `_pie_cumulative_keep`, `charts.py` |
| Wedge → row click wiring | `svg-chart-links.js` — `#usage-user-<name>` **already existed** for the stacked legend, so the pie added no new sentinel |
| Per-user data | `get_user_summary_for_project`, `sam/queries/charges.py` — already fetched by the page |
| Metric persistence | the `metric:usage` family, whose vocabulary is already `charges\|jobs\|core_hours` |

The fragment registrar (`webapp/utils/fragments.py`) is deliberately **not** used:
its own docstring says a family this small stays a bespoke route.

## Persistence contract

The part most likely to regress, and the one real design decision.

| State | Channel |
|---|---|
| Period (pills / Custom / Epoch) | `?start_date=`/`?end_date=`; `markActive` repaints |
| Metric (Charges/Jobs/Core-Hours) | `chart:__shared__` bucket, family `metric:usage` |
| Which usage tab | `?usage_tab=`, kept live by the tab ⇄ URL sync |
| Month / row expansions | `collapse:<id>` (`data-no-persist` rows opt out) |

**Why the tab does not use the `tab:<tablistId>` localStorage channel.**
`restoreTabs` runs on `DOMContentLoaded` — the same event htmx initializes on. If
localStorage said `byuser` while the server rendered `history` active, the History
pane's `hx-trigger="load"` chart would fire *and* the restore would then show By
User, firing its chart too: two chart fetches per load, one of them invisible, in
a load-order race. The URL is also strictly stronger — it survives bookmarking,
link-sharing and back/forward, not just F5.

So a tablist marked `data-tab-url-param` is server-driven and **opts out** of the
localStorage channel (two one-line guards in `saveActiveTab`/`restoreTabs`), and
`syncTabParam` keeps three writers in step on `shown.bs.tab`: the address bar
(`history.replaceState`), every `.drp-hidden` JSON block (`pickers.js` reads it at
*click* time, so the pills follow for free), and `[data-tab-param-link]` hrefs plus
same-named hidden inputs. Without that last part you would click By User, click a
pill, and land back on History.

`replaceState` is safe beside the scroll-preserve machinery: `nav:scroll:` keys on
the pathname only.

**The payoff:** exactly one chart request per page load. Only the server-active
pane gets `hx-trigger="load"`.

**One correction found in live smoke.** The inactive pane was originally
`shown.bs.tab once`, which left the two panes able to contradict each other:
pick Core-Hours on By User, switch to History, and History was still the Charges
chart it had rendered on load — with its own metric pill saying "Charges" while
the other pane said "Core-Hours". The metric is a *shared* family precisely
because it means the same thing on both, so disagreement is a bug in the model,
not a caching trade-off. Both panes now refetch on every `shown.bs.tab` (no
`once`); the persisted metric is injected on each request, so whichever pane you
land on is current. A warm refetch measured ~90-110 ms against the dev DB — the
chart SVGs are content-hash cached — which is the right price for two views that
can never disagree.

`usage_tab` is deliberately *not* named `active_tab`: the jobs blueprint already
owns `?active_tab=`, and this page hosts a jobs card.

## Non-goals

- `resource_details_disk.html` keeps its own standalone picker card. DISK has
  capacity semantics, not burn-rate, and its own template.
- The Job History card is untouched, including its `tab:jobsCardTabs` localStorage
  behaviour — the tab-sync opt-out is marker-driven, so it does not leak.
- No fragment registrar, no card-shell fragment route, no changes to the
  legacy-compat API blueprints.
- The three per-card collapses (`#collapseUsage`/`#collapseCharges`/
  `#collapseUsers`) are gone rather than reproduced per pane; the tabs serve the
  same "hide what I'm not reading" purpose. Their stale `collapse:` localStorage
  keys are inert.

## Order

| # | Commit | Gate |
|---|---|---|
| 0 | this doc | — |
| 1 | split `date_range_picker` into `drp_pills` + `drp_custom` | disk page renders byte-identical |
| 2 | `generate_user_usage_pie_chart` | unit render, sentinels present |
| 3 | shared usage-fragment prologue + `resource_details_user_pie` route | usage-chart route unchanged in behaviour |
| 4 | `activateOwningTab` in `svg-chart-links.js` | existing bar/legend clicks still work |
| 5 | tab ⇄ URL sync in `nav-view-persistence.js` | jobs card tabs still restore |
| 6 | the tabbed usage card + tree move | route-map parity snapshot regen |
| 7 | tests | full suite |

## Verification

Live smoke on `docker compose up webdev --watch` (`:5050`), four shapes:
multi-user/multi-child tree, single-user project (By User tab absent), an archive
resource (charges-only metrics), and Derecho GPU (jobs-machine mapping).

Persistence matrix — click, then reload, and check the tab *and* the window
survive: pill, Epoch, Custom→Apply, tree node. Plus: metric carries across tabs;
stale `?usage_tab=byuser` clamps to History on a single-user project; DevTools
shows exactly one chart request per load; month expansions still restore.
