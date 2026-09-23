# Gallery visual snapshots — deferred

**Status: deferred 2026-09-23, not started. Nothing is scheduled.**

**Why deferred.** Pixel-diff snapshots of `/dev/gallery` would catch too little
to justify the complexity. The front-end libraries are vendored, so their
visuals change only when we deliberately bump a version, about five times a year.
A major bump turns nearly every baseline (about 78) red at once. Reviewing those
diffs is no better than paging through the gallery's six theme × layout states
by hand, which is the job the gallery already does. Bumping the Playwright/Chromium
image also forces a regen for antialiasing noise unrelated to our code.

The visual bugs we have actually hit mostly fall outside what snapshots can see:
- The FA7 spin-wobble was an animation, and snapshots disable animations.
- Dark-mode contrast is already gated by the raw-color check and WCAG measurement.
- The PR #464 modal trap was behavior, which the console sweep covers.
- The renew-modal phone scroll was page-level, not a gallery macro.

What's left is an edit to our own CSS or macros that breaks a *different* macro.
That's real but rare, and we have no prod incident of it on record.

## Revisit if

- A CSS or macro regression reaches prod that the gallery would have shown.
- A shared design system or an embedding target (SAMuel served inside another
  site) raises the cost of an unnoticed visual change.
- `/dev/gallery` grows past what a person can page through by hand.

## Middle ground, if ever wanted

A single script that screenshots `/dev/gallery` in the six theme × layout states
into a folder: no baselines, no CI. Run it before and after a vendored bump and
compare the two folders by eye. Add one checklist line to the
`update-vendored-assets` skill. It costs about 40 lines and pays off at the one
moment that matters.

---

## Design (as planned, not built)

Decisions taken during planning: run in a **pinned container**, **drop the icon
inventory** from the comparison, keep baselines in **plain git**.

### Runtime: one rendering environment

Chromium renders fonts differently on macOS and Linux (including `<code>`'s
system monospace), so baselines are valid in exactly one environment.

- `e2e/visual.Dockerfile`: `FROM mcr.microsoft.com/playwright/python:v<X>-noble`,
  then `pip install playwright==<X> pytest-playwright pytest pytest-timeout pillow`,
  with the Python package matching the image tag so the bundled Chromium agrees.
  No `pip install -e .`, because nothing under `e2e/` imports `sam`. **The tag is
  the rendering pin**: bumping it means regenerating baselines.
- `compose.yaml`: add a `gallery-visual` service with `profiles: [visual]` on
  `sam-network`, the repo mounted at `/work`, `SAM_E2E_VISUAL=1`, and
  `SAM_E2E_BASE_URL=http://webapp:5050` (the internal port). For local iteration,
  point it at `webdev:5050`, which is code-synced; `webapp` bakes its code in.
- `make e2e-visual` → `docker compose --profile visual run --rm --build
  gallery-visual`. It forwards `GALLERY_SNAPSHOT_REGEN` and `SAM_E2E_BASE_URL`,
  and sets `user:` from the host UID/GID so baselines written on Linux are not
  owned by root. Add the target to `check-all` next to `e2e`.

### Test: `e2e/test_gallery_visual.py`

- Skip the module unless `SAM_E2E_VISUAL=1`, so `make e2e` on a Mac host stays
  exactly as it is.
- Parametrize theme × layout. Viewports are mobile 390×844, tablet 1024×768 and
  desktop 1440×1000 (the sweep's), each inside the bands in
  `src/webapp/static/js/layout-axis.js` (767.98 / 1199.98). That keeps the server
  layout, the cookie the JS writes, and the CSS breakpoints in agreement.
- Navigate to `/dev/gallery/?layout=<l>` after `set_theme()`, then
  `assert_theme_applied()`, `document.fonts.ready`, `networkidle`.
- **Sections come from the DOM** (`section[id]` minus `skipped`), not from
  `specimens.SECTIONS`. A new section fails with "no baseline, regenerate" until
  one is committed.
- Before capture, remove every `[data-visual-skip]` element via `page.evaluate`.
  Script run through CDP isn't blocked by CSP. Removing an element keeps image
  dimensions stable, which a `mask=` box can't do while the icon inventory keeps
  growing. The inventory card in `src/webapp/templates/dashboards/gallery/index.html`
  gets the attribute; "special usages" (fa-spin, fa-regular) stays compared.
- Capture with `locator.screenshot(animations='disabled', caret='hide')` under
  `reduced_motion='reduce'`. That freezes `fa-spin`.
- Compare with Pillow alone: `ImageChops.difference`, threshold, count pixels.
  Pure-Python pixelmatch is too slow for 78 images. A size mismatch fails and
  reports both sizes. Start strict (channel threshold 8, at most 0.05% of pixels)
  and tune on the first CI runs. No retries, per the flake budget in `e2e/pytest.ini`.
- On failure, write `test-results/gallery-visual/<name>-{expected,actual,diff}.png`
  (the diff paints changed pixels red over a dimmed copy of actual).
  `browser-smoke.yaml` already uploads `test-results/`.
- `GALLERY_SNAPSHOT_REGEN=1` overwrites baselines and passes, the same convention
  as `CHART_FINGERPRINT_REGEN`. Regenerate in the same commit as an intended
  visual change.
- Ratchet: `test_no_orphan_baselines` fails on a PNG with no matching
  (theme, layout, section), in the same spirit as
  `test_console_allowlist_has_no_dead_entries`. Regen deletes orphans.
- Baselines live at `e2e/snapshots/gallery/{theme}-{layout}-{section}.png`,
  about 13 × 6 files and 3–8 MB, in plain git with a `binary` `.gitattributes`
  line.

### CI and docs

- `.github/workflows/browser-smoke.yaml`: after `make e2e`, a "Gallery visual
  snapshots" step runs `make e2e-visual` against the stack that is already up.
- One checklist line each in `.claude/skills/wire-dashboard-feature/SKILL.md` and
  `.claude/skills/update-vendored-assets/SKILL.md`. A short section in
  `docs/plans/implemented/FRONTEND_TEST_NET.md` and a row in `docs/TESTING.md`.

### Reuse

From `e2e/conftest.py`: `set_theme`, `assert_theme_applied`, `THEMES`, and the
login-once `storage_state` / `browser_context_args` fixtures. The `?layout=`
handling is `read_layout()` in `src/webapp/utils/htmx.py`.

### Verification

1. `make docker-up`, then `GALLERY_SNAPSHOT_REGEN=1 make e2e-visual`. Eyeball
   dark/mobile, light/desktop and icons: the inventory should be gone and the
   spinner static.
2. Run `make e2e-visual` twice with no regen and get zero diffs both times.
3. Temporarily change one CSS token. Only the expected sections should fail,
   with diff PNGs written. Revert.
4. Run `make e2e` on the Mac host: the visual module should report skipped.
5. In the PR, confirm baselines generated locally in the container pass on the
   CI runner. That is the pin's whole claim.

Context: the gallery itself is `docs/plans/implemented/DESIGN_SYSTEM_TOOLING.md`.
