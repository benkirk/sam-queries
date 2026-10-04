# Panel sunburst: facility, panel and project in one expanded view

As-built record (2026-10-03). The two-ring sunbursts on Allocations and on Status →
Job History → By Project open a fullscreen three-ring view: facilities inside,
allocation panels in the middle, every project on the rim with radial labels.
It depends on the hover layer (`CHART_HOVER_LAYER.md` stage 1), because most rim
wedges are too thin to label.

## Decisions (Ben, 2026-10-03)

- **Draw every project.** The rim names a wedge where one radial line fits, and
  hover names the rest. Folding is a fallback, with two triggers in `prepare()`:
  - A wedge under `min_wedge_deg` (0.05°, under a pixel) folds into its panel's
    muted `+N` wedge.
  - Past `max_wedges` (1,500), each panel keeps only its `top_n` (12).
- **Fullscreen modal**, never the default view. One shell,
  `fragments/chart_expand_modal.html`, per host page.
  - The opener sits at the top right of the chart (`fragments/chart_expand.html`).
    A click on the chart away from any link opens it too.
  - It is hidden below md, because a phone cannot read the rim.
- **The panel comes from the project** (`project.allocation_type → panel`), via
  `project_panels()`. Never use `(facility, type name)`: `Small` and `Education`
  each exist under two panels.
- **Project wedges drill to the project quick-view** (`links.PROJECT_MODAL`). A row
  drill cannot resolve from inside a modal, and the jobs table holds only its top 25.
  `svg-chart-links.js` closes the expand modal before it opens the quick-view,
  because Bootstrap does not stack modals.

## Measures

| Host | Measure | Source |
|---|---|---|
| Allocations, Annual rate ring | `annualized_rate` (storage: `total_amount`), root allocations only | `cached_allocation_usage` |
| Allocations, Used ring (storage) | `total_used` | same |
| Allocations, Used ring (HPC/DAV) | charges over the ring's window × 365/days | `cached_charges_by_project` |
| Job History, By Project | the selected metric, every project in the window | `jobs_usage_by_project(limit=None)` |

## Measured (local snapshot, Derecho Annual rate)

There are 1,143 projects. 425 are drawn as wedges, and 718 fold into `+N` wedges
under the visibility floor. The SVG is 248 KB with 444 `<title>`s, and a cold render
takes 0.3 s. That is well inside the budget (under 1 MB and under 3 s), so
`max_wedges` stays at 1,500.

## Follow-ons

- Smoke on samuel-dev after staging, Job History in particular. The local
  job-history database was out of disk during development.
