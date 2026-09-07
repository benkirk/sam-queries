---
name: update-vendored-assets
description: >-
  Checking for, assessing, and applying updates to the webapp's vendored
  front-end assets (Bootstrap, htmx, Font Awesome, Poppins). Load
  before bumping a vendored library to read the changelog against our real
  usage and documented workarounds — so a bump that fixes a bug we work around
  or breaks one we rely on is caught on purpose, not by luck — then apply it
  through the registry and run the right gates.
---

# Update vendored front-end assets

An ordered procedure for keeping `src/webapp/static/vendor/` current. The
*mechanical* swap is already documented — `src/webapp/static/vendor/README.md`
(the walk-through) and the `VENDOR_ASSETS` docstring in
`src/webapp/vendor_assets.py` (the terse version). This skill does not repeat
that recipe; it adds the judgment steps those docs lack — checking upstream,
reading release notes, and cross-referencing them against our usage and the
version-tied workarounds — and points back to the README for the file swap.

Work top to bottom. Steps 1–4 are investigate-and-decide (no file changes);
5–7 apply a bump; 8–9 verify and hand off. Steps 1–4 are the reason this skill
exists — do them even for a "trivial" patch bump.

## 1. Inventory what we actually ship

Read `VENDOR_ASSETS` in `src/webapp/vendor_assets.py` for the current
key → version → path set. That dict is the source of truth; a filename or a
header banner can lie, the registry cannot. The libraries: `bootstrap-css` /
`bootstrap-js`, `htmx`, `fontawesome-css`, `poppins`. We vendor
**Font Awesome Free** (not Pro) — assess only the Free tier. (jQuery was
vendored through 3.7.1 but removed entirely once its sole consumer was
rewritten in vanilla JS — there is no jQuery to bump.)

## 2. Check upstream for newer releases

Per library, find the latest published release and note a candidate target.
Separate an **in-major** bump (patch/minor, e.g. 5.3.3 → 5.3.x) from a
**major** jump (e.g. FA 6 → 7) — they carry very different risk. Authoritative
sources (use WebFetch / WebSearch):

| Library | Where to look |
|---|---|
| Bootstrap | GitHub releases `twbs/bootstrap`; blog at getbootstrap.com |
| htmx | GitHub releases `bigskysoftware/htmx`; htmx.org/posts |
| Font Awesome | `FortAwesome/Font-Awesome` releases / `CHANGELOG.md` — **Free tier** |
| Poppins | Google Fonts version (special case — see step 6) |

## 3. Read the release notes and assess impact

For each candidate, skim the changelog *between* our version and the target and
answer two questions, per library:

- **Does it fix a bug we currently work around?** That is a bump we *want*, and
  it turns a workaround into removable code (step 7).
- **Does it change or remove behavior we rely on?** That is a bump that needs a
  careful read before it lands.

Cross-reference against the **known workarounds table** at the bottom of this
file, *and* re-discover any that have been added since by grepping the tree —
the table's pointers drift, the grep does not:

```bash
rg -n -i 'bootstrap|htmx|font\s*awesome|fontawesome|jquery|poppins' \
   src/webapp/static/js src/webapp/static/css docs CLAUDE.md \
   .claude/skills/wire-dashboard-feature/SKILL.md
```

Record findings per library — this is the report the run produces.

## 4. Classify and decide

Label each candidate:

- **Safe** — patch/minor, same major, and nothing in step 3 touches a
  documented workaround or the CSP surface. Proceed to apply.
- **High-risk** — a major jump, **or** it touches a workaround / CSP / icon-name
  surface. Do **not** auto-apply: report it with a breaking-change summary and
  stop for a human decision.

A Font Awesome major is always high-risk: majors rename or retire icon glyph
classes and can change the default metrics (FA7 made icons fixed-width by
default — measure action-cell wrap before/after). If a major is approved and the
plan is to modernize class names rather than lean on shims:

- **Build the old→new map from the release's own metadata, never by hand.**
  The web zip ships an `icons.json` metadata file; each canonical icon lists its
  `aliases.names` and its `free` styles. Invert it to alias→canonical over the
  names we actually use, and confirm every target is in the **free** set at the
  style we use it. An icon that is neither canonical nor an alias is Pro (already
  rendering blank) — pick a free replacement.
- **Discover files by the prefix token, not just `fa-`.** A template can carry
  `class="fas {{ row.icon }}"` with the glyph in a Jinja variable and *no literal
  `fa-`* — a `grep -l 'fa-'` file list misses it. Discover on `fas|far|fab` too,
  and handle the `prefix {{ … }}` / `{% … %}` form in the rewrite.
- **Sweep `tests/` as well as `src/webapp/`.** Tests assert on rendered icon
  markup (`assert 'fa-edit' in html`, `find('<h5><i class="fas')`); the rewrite
  touches app code, so update the matching test expectations in the same change.

## 5. Apply a bump (mechanics live in the README)

Follow `src/webapp/static/vendor/README.md` § "Bumping an existing asset" for
the exact recipe. The invariants that must not slip:

- Download the publisher's SRI-carrying artifact (cdnjs/jsdelivr "copy SRI", or
  the GitHub release asset); **note that URL in the commit message.**
- Verify the download against the publisher's SRI, then record the **sha384** the
  registry uses. Mind the digest algorithm: **cdnjs now publishes sha512 SRI**,
  not sha384 — so verify the bytes against the sha512, then record the sha384 of
  those same verified bytes:
  ```bash
  # verify (match cdnjs's published sha512 for the file)
  openssl dgst -sha512 -binary <file> | openssl base64 -A
  # record (the value that goes in vendor_assets.py)
  openssl dgst -sha384 -binary <file> | openssl base64 -A   # prefix sha384-
  ```
  Pull the publisher's SRI with `curl … | python -c '…json…'` (or `jq`), **never
  by having a model read it back** — a one-character transcription slip in a hash
  reads as a corrupt-download false alarm. The GitHub release asset is
  publisher-direct, so its own bytes are the reference.
- Use a **version-pinned** filename/dir (`htmx-2.0.5.min.js`,
  `bootstrap-5.3.4/…`) and **delete the old files in the same commit** — the
  test rejects a `path` that doesn't exist, and a stray old file is dead weight.
- Update `path` **and** `sha384` **together** in `vendor_assets.py`.
- Preserve sub-resource layout the CSS resolves by relative path — Font
  Awesome's `../webfonts/`, Poppins' `*.woff2`.

Templates need no change — they reference assets by registry key, not filename.

## 6. Poppins is a special case

Poppins is not a CDN download; it is self-hosted Google Fonts output. To change
weights or glyph ranges, follow the README § "The Poppins special case":
request the CSS from the Google Fonts API with a **pinned `User-Agent`** (the
API serves different CSS per browser — pin it for reproducibility), download
every `.woff2` it references into `poppins/`, rewrite the `src: url(...)` paths
to `/static/vendor/poppins/...`, and recompute the `poppins.css` hash.

**Keep the matplotlib TTF copy in sync.** `src/webapp/static/fonts/poppins/*.ttf`
is a deliberate server-side duplicate — matplotlib's font_manager cannot read
woff2 — and `test_chart_fonts.py` asserts `findfont('Poppins')` resolves there.
Never delete it as "unreferenced"; if a Poppins bump changes the family, refresh
these TTFs too.

## 7. Re-verify the workarounds the bump touched

For every workaround step 3 flagged as *possibly fixed upstream*, confirm from
the changelog whether it is now unnecessary. If so, note it for removal in a
**separate follow-up** — do not rip workarounds out in the version-bump commit,
so a revert of the bump doesn't also revive a bug. If a workaround now needs to
be done *differently* under the new version, flag that as blocking.

## 8. Gates

Run these after any applied bump. The README's "Verifying a change" section has
the failure-meaning glossary for the first one.

```bash
source etc/config_env.sh
pytest tests/unit/test_vendor_assets.py            # hashes, version-pinned, local-only, sub-resources present
pytest tests/unit/test_csp.py tests/unit/test_security_headers.py   # CSP still 'self', no new origins
```

For a bump touching a trap surface, add the matching structural gate:
`test_modal_shell_contract.py` and `test_collapse_trigger_rows.py` (Bootstrap),
`test_chart_fonts.py` (Poppins).

**Browser smoke any visible bump** (Bootstrap, Font Awesome, Poppins) at 3
layouts × 2 themes per `wire-dashboard-feature` step 12, and eyeball
`/dev/gallery`. Two caches lie on webdev first — flush Redis
(`docker exec samuel-cache redis-cli -n 0 FLUSHDB` or `sam-admin cache --refresh`)
and restart webdev so the static `?v=` hash re-memoizes — or you will measure
"no change" against a stale copy.

## 9. Deploy note

A version-pinned filename change moves the served `url_for('static', …)` path.
Redis-cached pages (`CACHE_DEFAULT_TIMEOUT=300s`) may reference the old path
until they expire; run `sam-admin cache --refresh` at rollout (the same flush
the CSP rollout calls for). Browsers cache by URL, so the new filename is a
clean cache-bust for free.

## Known version-tied workarounds to re-check on a bump

These are the traps whose behavior is pinned to a specific library version.
A bump may fix one (removable, step 7) or invalidate one (blocking, step 4).
The grep in step 3 catches any added since; this table is the starting set.

- **Bootstrap — modals/toggles.** PR #464 fragment-in-open-modal trap
  (`CLAUDE.md` §9, gated by `tests/unit/test_modal_shell_contract.py`);
  cross-modal z-index stacking (`static/js/htmx-config.js`); a `data-bs-toggle`
  inside an open modal closes its host (`static/js/actions.js`); pinch-zoom
  scrollbar-pad miscalculation (`static/js/modals.js`); manual tooltip/popover
  init + dispose-before-htmx-swap (`static/js/tooltip-init.js`).
- **Bootstrap — collapse.** Collapse data-api fires in the capture phase, so a
  nested link/button toggles the row (`tests/unit/test_collapse_trigger_rows.py`;
  `wire-dashboard-feature` step 6).
- **Bootstrap — CSS/theme.** `<td>` paints opaque `--bs-table-bg` over any
  row-level background; dark mode does not retheme `.table-*` or
  `--bs-secondary-rgb`; color utilities carry `!important`
  (`docs/plans/implemented/DARK_MODE_STRAGGLERS.md`, `static/css/*`). A Bootstrap
  minor can change these tokens — diff the compiled variables.
- **htmx (currently 2.0.4).** A dangling `hx-target` emits
  `console.error("htmx:targetError")` but a dangling `data-bs-target` emits
  nothing — this asymmetry is why the modal-shell contract is a Python gate, not
  a browser sweep; GET requests append params to the path (needs
  `URLSearchParams` dedupe); `htmx:configRequest` does not fire on full-page GET
  chart renders (`docs/plans/implemented/FRONTEND_TEST_NET.md`,
  `docs/plans/implemented/MOBILE_CHARTS.md`, `static/js/layout-axis.js`).
  **htmx is the highest-value changelog to read** — its event and request
  behavior is load-bearing across the dashboards.
- **Font Awesome.** The entry-point CSS resolves webfonts by relative
  `../webfonts/` `url()`, which no `url_for` can reach — the short-TTL branch of
  the cache-header rule exists for exactly these (`CLAUDE.md` §11), and their
  presence is gated (`tests/unit/test_vendor_assets.py`). A **major** can rename
  or retire icon classes — grep template `fa-*` usage before bumping (step 4).
  Also: the webfont **family name is versioned** (`'Font Awesome 6 Free'` →
  `'Font Awesome 7 Free'`), and two `static/css/dashboard.css` pseudo-element
  rules hardcode it (navbar caret `\f107`, collapse chevron `\f078`). Grep the
  versioned family string on any FA major or those glyphs silently vanish; FA7
  also dropped ttf, shipping woff2-only.
- **CSP.** The policy is nonce-free by design, so any inline `<script>` /
  `<style>` / `on*` / `hx-on:` is blocked; the htmx-config hardening meta tag is
  mandatory (`docs/plans/implemented/CSP.md`). A vendored bump must keep
  `script-src 'self'` with no new origin (step 8).
- **jQuery — removed.** No longer vendored. Its sole consumer
  (`static/js/lazy-loading.js`) was rewritten in vanilla JS and the asset
  dropped, since Bootstrap 5's bundle does not depend on jQuery. If a new
  library ever wants it back, that is a fresh vendoring decision, not a bump.
