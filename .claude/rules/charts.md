---
paths:
  - "src/webapp/dashboards/charts/**/*"
  - "src/webapp/utils/charts.py"
  - "src/webapp/static/css/charts.css"
  - "src/webapp/templates/dashboards/fragments/chart_bits.html"
  - "tests/unit/charts/**/*"
---

# Charts

Loaded with the chart package and its tests.

## Charts — `webapp/dashboards/charts/`

18 matplotlib charts, server-rendered to **inline SVG** (no PNG/base64/dpi
anywhere). Each is a `BaseChart` subclass bound to its cache by `chart_view`.

```
charts/
  __init__.py     facade: the chart_view bindings, re-exports, __all__
  base.py         BaseChart lifecycle + chart_view (the cache binder)
  theme.py        fonts, rcParams, Unity palettes, Theme, scale_bytes
  layout.py       Layout — the geometry axis
  links.py        drill targets                      [no matplotlib]
  series.py       stacked-band normalization         [no matplotlib]
  jobs_metrics.py plugin-envelope accessors          [no matplotlib]
  pie.py sunburst.py stacked.py histogram.py dualpanel.py pace.py   the families
```

**Two render axes**, both composed into the cache key by `chart_view` *and* into
cached HTML by `user_aware_cache_key` — forget the latter and one layout is
served to everyone:

- **`layout`** — mobile ≤767.98px / tablet 768–1199.98px / desktop ≥1200px.
  Desktop is the identity. Tablet is desktop with a smaller figure; mobile is a
  redesign. Declared per family via `LAYOUTS = profile(...)`. Transported by
  **both** a `sam_layout` cookie and an htmx `?layout=` param, because 5 of 21
  call sites render in a full-page GET.
- **`theme`** — light / dark. Cookie only: a viewport is discovered, a theme is
  declared. Colors are baked into SVG bytes, which is why this needs a
  server-side carrier at all.

Routes read them with `read_layout()` / `read_theme()` from `utils/htmx.py`.
Date axes go through `self.apply_date_axis(ax, layout)`, never
`fig.autofmt_xdate()`. Drill-downs use one scheme, `#sam/<action>/<segments>`,
built by `links.py` — a row drill is one attribute declared at the chart, with
no JavaScript change.

Lifecycle, the family hierarchy, how to add a chart, the layout/theme design
and all the measurements: `docs/plans/implemented/CHART_ARCHITECTURE.md`,
`DARK_MODE.md`, `MOBILE_CHARTS.md`, `TABLET_CHARTS.md`, and the
`webapp/dashboards/charts/__init__.py` and `base.py` docstrings.

### Gotchas

❌ **DON'T** call a chart from a route directly, or let `BaseChart` swallow
   exceptions — `draw_chart` (`webapp/utils/charts.py`) is the one caller: it
   logs a failure and returns the shared error state.
❌ **DON'T** rely on a base `is_empty` default: the pace chart holds ndarrays,
   where `not self.data` raises *"truth value of an array is ambiguous"*.
   Define it per family.
❌ **DON'T** import matplotlib into `links.py` / `series.py` /
   `jobs_metrics.py` — enforced by `test_chart_module_boundaries.py`.
❌ **DON'T** reorder the `chart_view` calls: that order is the admin Caching
   card's row order, and cache names are Redis key prefixes.
❌ **DON'T** put a mobile or tablet override in `profile()`'s bare keywords —
   those are desktop. Use `mobile={...}` / `tablet={...}`, which validate
   against `Layout`'s fields.
❌ **DON'T** accept a `layout` argument in a fragment renderer and then call a
   delegate without it — the fragment still renders, at desktop, forever.
   `test_renderers_forward_the_axis_they_are_given` is the gate.
❌ **DON'T** cap a legend without passing `ordered=True` to `link_legend` — it
   zips bands against patches by position, and a capped legend is no longer
   `reversed(bands)`. The fingerprint proves href *strings*, not the artists
   carrying them, so a misaligned drill looks green.
❌ **DON'T** put a theme-dependent color in `ax.text()` without setting it
   explicitly — `apply_chrome` skips `ax.texts` by design, so it will render in
   whatever the light rcParams baked in.
✅ **DO** regenerate the fingerprint snapshot in the *same commit* as an
   intentional visual change:
   `CHART_FINGERPRINT_REGEN=1 pytest tests/unit/charts/test_chart_fingerprints.py`.
   A fingerprint delta in a commit that didn't declare one is a bug. **A
   desktop-light delta in a mobile- or dark-tuning commit always is.**
✅ **DO** run `sam-admin cache --refresh --category chart` after deploying a
   visible change — keys hash *input data*, not rendering code, so warm Redis
   entries serve old-code SVGs for up to 600 s.

**Around a chart.** A host is built from `fragments/chart_bits.html` (`chart_figure`,
`chart_pills`, `chart_loading`; gate `test_chart_frame.py`) and sized by `charts.css`.
Every mark has a hover title; one naming a project carries its title only for
viewers `hover_titles` admits. A folded remainder reads `N other` (`series.py`).
