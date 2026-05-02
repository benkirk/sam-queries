# Display Formatting Migration Plan

**Module**: `src/sam/fmt.py` (introduced in commit `b3cda8a`)
**Status**: Module + Jinja2 filters wired; call sites not yet migrated.

---

## Overview

`sam.fmt` provides four functions (`number`, `pct`, `date_str`, `size`) plus
`configure()`, `register_jinja_filters()`, and `mpl_number_formatter()`.
The Jinja2 filters `fmt_number`, `fmt_pct`, `fmt_date`, `fmt_size` are already
registered in `create_app()` — templates can start using them immediately.

Migration is purely mechanical: find the old pattern, substitute the filter or
function call, delete the old string-format expression.  Work file-by-file; the
suite stays green throughout.

---

## Conversion Reference

### Jinja2 templates

| Old pattern | New filter |
|---|---|
| `'{{ "{:,.0f}".format(x) }}'` | `{{ x \| fmt_number }}` |
| `'{{ "{:,}".format(x) }}'` | `{{ x \| fmt_number }}` |
| `'{{ "%.1f"\|format(x) }}%'` | `{{ x \| fmt_pct }}` |
| `'{{ "%.2f"\|format(x) }}%'` | `{{ x \| fmt_pct(decimals=2) }}` |
| `'{{ x.strftime("%Y-%m-%d") }}'` | `{{ x \| fmt_date }}` |
| `'{{ x.strftime("%Y-%m-%d") if x else "—" }}'` | `{{ x \| fmt_date }}` |
| `'{{ x.strftime("%b %Y") }}'` | `{{ x \| fmt_date(fmt="%b %Y") }}` |
| filesystem TB values | `{{ x \| fmt_size }}` |

The `null='—'` default handles `None` automatically — remove manual
`if x else '—'` guards when converting.

**Note on `fmt_number` and compact notation**: allocation amounts
(`allocated`, `used`, `remaining`, `charges`, `total_amount`) are large
enough (typically millions) that `fmt_number` will render them as `68.6M`
rather than `68,567,808`.  Job counts (`jobs`, `cores_allocated`) are
typically below `COMPACT_THRESHOLD` (100,000) and will stay exact.  If an
exact integer is ever needed regardless of size (e.g. a sort key hidden in
a `data-` attribute), keep the raw `{:,.0f}` expression there and use
`fmt_number` only for the visible cell text.

### Python CLI (`src/cli/`)

```python
# Before
from sam import fmt

f"{amount:,.0f}"               →  fmt.number(amount)
f"{jobs:,}"                    →  fmt.number(jobs)
start_date.strftime("%Y-%m-%d") →  fmt.date_str(start_date)
f"{pct:.1f}%"                  →  fmt.pct(pct)
```

Rich markup is preserved around the call:
```python
# Before
f"[{style}]{amount:,.0f}[/]" if is_expired else f"{amount:,.0f}"

# After
value = fmt.number(amount)
f"[{style}]{value}[/]" if is_expired else value
```

### matplotlib (`src/webapp/dashboards/charts.py`)

```python
# Before
legend_labels = [f'{n} ({v:,.0f})' for n, v in zip(names, values)]

# After
from sam import fmt
legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]

# For axis tick labels
ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())
```

---

## File-by-File Work Items

### HTML Templates (~39 number sites, ~22 date sites, ~5 percent sites)

#### High value / high traffic — do first

**`src/webapp/templates/dashboards/user/partials/project_card.html`**
- Lines 160–162: `resource.allocated`, `resource.used`, `resource.remaining`
  ```jinja2
  {# Before #}
  {{ '{:,.0f}'.format(resource.allocated) }}

  {# After #}
  {{ resource.allocated | fmt_number }}
  ```
- Lines 136–144: progress bar `percent` variable — use `fmt_pct` for the
  label inside the bar (already a rounded float, just needs `%`).

**`src/webapp/templates/dashboards/user/resource_details.html`**
- ~16 sites: `summary.allocated/used/remaining`, `user.charges`, `q.charges`,
  `d.charges`, `day.charges`, `sub.total_charges` → `fmt_number`
- ~8 sites: `user.jobs`, `q.jobs`, `d.jobs`, `sub.total_jobs` → `fmt_number`
  (will stay exact; job counts < 100K threshold)
- Date cells: `strftime` in sort-attribute `data-sort-value` should stay raw;
  visible text cells only should use `fmt_date`.

**`src/webapp/templates/dashboards/allocations/partials/project_table.html`**
- Line 41: `project['total_amount']` → `fmt_number`
- Line 63: `project['total_used']` → `fmt_number`
- Lines 109, 116: `sd.strftime(…)`, `ed.strftime(…)` → `fmt_date`
- Lines 74–75: `sort_start`/`sort_end` data attributes — keep raw `strftime`
  (used for JS sort, not display).
- Lines 111–113: `bar_state` badge text — leave as-is (text labels, not numbers).

**`src/webapp/templates/dashboards/allocations/dashboard.html`**
- Line 187: `"%.1f"|format(facility_data.percent)` → `fmt_pct`

**`src/webapp/templates/dashboards/allocations/partials/usage_modal.html`**
- Lines 74, 86: `"%.1f"|format(percent)` → `fmt_pct`

#### Lower priority

**`src/webapp/templates/dashboards/admin/fragments/facility_card.html`**
- Line 107: `at.default_allocation_amount` → `fmt_number`
- Lines 41, 108: `fair_share_percentage` → `fmt_pct(decimals=2)`

**`src/webapp/templates/dashboards/status/partials/filesystem_table.html`**
- Lines 38, 45: `fs.used_tb`, `fs.capacity_tb` — currently TB floats;
  consider whether `fmt_size` (bytes input) or `fmt_number` (already-scaled TB)
  is more appropriate.  If the backend passes raw bytes, switch to `fmt_size`.
- Lines 91, 98: `fs.used_inodes`, `fs.capacity_inodes` → `fmt_number`

**`src/webapp/templates/dashboards/status/partials/utilization_metrics.html`**
- Lines 16, 66: CPU cores and memory → `fmt_number`

**`src/webapp/templates/dashboards/status/partials/queue_table.html`**
- Lines 38–40: `cores_allocated`, `cores_pending`, `cores_held` → `fmt_number`

**`src/webapp/templates/dashboards/status/jupyterhub.html`**
- Lines 157, 184: CPU/memory counts → `fmt_number`

**`src/webapp/templates/dashboards/status/queue_history.html`**
- Lines 32, 39, 46: core counts → `fmt_number`

**Date-only templates** (form `value=` attributes keep raw `strftime`; only
visible display text needs `fmt_date`):
- `dashboards/admin/fragments/organization_card.html`
- `dashboards/status/partials/system_header.html`
- `dashboards/status/derecho.html`, `casper.html`
- `dashboards/status/fragments/reservations.html`

---

### CLI Python files (~15 number sites, ~10 date sites)

**`src/cli/allocations/display.py`** — highest density
```python
# Lines 106–108
start_str = fmt.date_str(row.get('start_date'))
end_str   = fmt.date_str(row.get('end_date'))
duration  = fmt.number(row['duration_days']) if row['duration_days'] else 'N/A'

# Lines 130–132
fmt.number(allocated),
fmt.number(used),
fmt.number(remaining),

# Lines 137, 139, 144
table_row.append(fmt.number(amount))
table_row.append(fmt.number(row['avg_amount']))
rate_str = fmt.number(row['annualized_rate'])
```

**`src/cli/project/display.py`**
```python
# Lines 73–79: creation/modification timestamps
grid.add_row("Created", fmt.date_str(project.creation_time, fmt="%Y-%m-%d %H:%M:%S"))

# Lines 132–133
start_str = fmt.date_str(start_date)
end_str   = fmt.date_str(end_date)

# Lines 153–155: preserve Rich markup, extract value first
alloc_str = fmt.number(allocated)
f"[{resource_style}]{alloc_str}[/]" if is_expired else alloc_str
```

**`src/cli/project/commands.py`**
```python
# Line 272
'expiration_date': fmt.date_str(item['allocation'].end_date),
```

---

### matplotlib (`src/webapp/dashboards/charts.py`)

```python
# Lines 277, 335: pie-chart legend labels
legend_labels = [f'{n} ({fmt.number(v)})' for n, v in zip(names, values)]
```

Consider adding `ax.yaxis.set_major_formatter(fmt.mpl_number_formatter())`
to any bar/line charts that have large y-axis tick values.

---

## Suggested Migration Order

1. `user/partials/project_card.html` — most visible, already touched this session
2. `user/resource_details.html` — highest site count in one file
3. `allocations/partials/project_table.html` + `usage_modal.html`
4. `allocations/dashboard.html`
5. `cli/allocations/display.py` — highest CLI density
6. `cli/project/display.py`
7. `status/` templates (lower urgency; numbers are infrastructure metrics)
8. `admin/` fragments
9. `charts.py` matplotlib labels

Each file is an independent commit.  Run `pytest tests/ --no-cov -q` after
each file to keep the suite green.

---

## Env-var cheat sheet

```bash
# Compact notation (default)
sam-search allocations --resource Derecho

# Raw numbers for scripting / grepping
SAM_RAW_OUTPUT=1 sam-search allocations --resource Derecho

# Fewer significant figures
SAM_SIG_FIGS=2 sam-search allocations --resource Derecho

# SI sizes instead of IEC (call configure() at CLI startup for this)
# fmt.configure(size_units='si')
```
