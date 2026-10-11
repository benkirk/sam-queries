---
paths:
  - "src/sam/fmt.py"
  - "src/cli/**/*"
  - "src/webapp/templates/**/*"
  - "src/webapp/dashboards/**/*.py"
---

# Display formatting

Loaded with templates, CLI display code and `sam/fmt.py`.

## Display Formatting — `sam.fmt`

All number, date, percentage, and size formatting goes through `src/sam/fmt.py`.
Jinja2 filters are registered in `create_app()` — use them in every template.

| Need | Jinja2 filter | Python (CLI) |
|---|---|---|
| Integer / compact number | `{{ x \| fmt_number }}` | `fmt.number(x)` |
| Percentage (0–100) | `{{ x \| fmt_pct }}` | `fmt.pct(x)` |
| Date / datetime | `{{ x \| fmt_date }}` | `fmt.date_str(x)` |
| Byte size | `{{ x \| fmt_size }}` | `fmt.size(x)` |
| Hours / charge factors / relative time | `fmt_hours`, `fmt_factor`, `fmt_ago` | `fmt.hours(x)`, … |
| Count + agreeing noun (`3 pushes`) | `{{ n \| fmt_plural('push', 'pushes') }}` | `fmt.plural(n, 'push', 'pushes')` |
| UTC→local, allocation units | `to_local_dt`, `alloc_unit` | — |

**Key behaviors**
- Numbers ≤ 100,000 → exact with commas (`34,283`); above → compact (`68.6M`)
- `None` → `'—'` by default for all filters
- `fmt_pct(decimals=N)`, `fmt_date(fmt='%b %Y')`, `fmt_number(raw=True)` overrides
- `SAM_RAW_OUTPUT=1` env-var forces exact integers everywhere (scripting/grepping)
- `fmt.mpl_number_formatter()` for matplotlib tick labels

**Do NOT** use raw `'{:,.0f}'.format(x)` or `.strftime(…)` in templates or CLI
display code. (Migration history: `docs/plans/implemented/FORMAT_DISPLAY.md`.)
