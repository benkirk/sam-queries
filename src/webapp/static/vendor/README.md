# Vendored frontend assets

Self-hosted, version-pinned copies of every third-party CSS/JS/font asset the
SAM webapp loads. All files here are committed to the repo and served locally
from `/static/vendor/...`; **the app loads no third-party origins at runtime.**

## Why these are vendored

Vendoring is a prerequisite for the webapp's Content-Security-Policy, which is
essentially all-`'self'` (see `docs/plans/CSP.md`). The alternative — allowlisting
a CDN such as `script-src https://cdn.jsdelivr.net https://unpkg.com` — would let
an injected `<script src>` tag pull *any* npm package those CDNs serve, sidestepping
CSP entirely (Google's CSP Evaluator flags exactly these origins). Browser cache
partitioning (~2020) also removed the shared-CDN performance benefit that used to
justify pulling from public CDNs. Self-hosting Poppins additionally closes the one
asset that could never carry an SRI hash (Google Fonts serves per-browser CSS).

Result: zero third-party origins in the policy, and tamper-evidence enforced in CI
instead of by the browser (see "How integrity is enforced" below).

## What's here

| Asset | Version | Entry point (registered) | Kind |
|---|---|---|---|
| Bootstrap | 5.3.3 | `bootstrap-5.3.3/bootstrap.min.css`, `…/bootstrap.bundle.min.js` | css + js |
| jQuery | 3.6.0 | `jquery/jquery-3.6.0.min.js` | js |
| htmx | 2.0.4 | `htmx/htmx-2.0.4.min.js` | js |
| Font Awesome | 6.5.2 | `fontawesome-6.5.2/css/all.min.css` (+ `webfonts/`) | css |
| Poppins | Google Fonts v24 | `poppins/poppins.css` (+ 15 `*.woff2`) | css |

These are **registered** — and described in machine-readable form — in
[`src/webapp/vendor_assets.py`](../../vendor_assets.py) (the `VENDOR_ASSETS` dict).
That module is the source of truth; this README explains it. Templates render the
assets via the `vendor_css()` / `vendor_js()` macros in
`templates/fragments/vendor_assets.html`.

Each registry entry lists only the **entry-point** file (the CSS/JS the page links).
Sub-resources that an entry pulls in by relative path — Font Awesome's `webfonts/`,
Poppins' `*.woff2` — ride along in the same directory; they are not individually
hashed (their integrity rests on git history), but CI does assert they are present.

## How integrity is enforced

Each registry entry pins the `sha384` of its entry-point file. For the five
formerly-CDN assets these are the *original publisher SRI values*; the downloads were
verified against them at vendoring time. Because the files are now served locally, the
browser no longer performs an SRI check — instead
`tests/unit/test_vendor_assets.py` re-computes the sha384 of every committed file and
fails CI on any mismatch. So the `path` and `sha384` of an entry must always be updated
together, and the file on disk must always match its recorded hash.

## Updating an asset

> No script automates this — it is deliberately a manual, reviewed change.
> The registry docstring in `vendor_assets.py` has the terse version; this is the
> walk-through.

### Bumping an existing asset to a new version

1. **Download** the new minified file(s) from the publisher. Prefer the exact URL
   that carries a published SRI hash (e.g. the cdnjs/jsdelivr "copy SRI" button, or
   the GitHub release artifact). Note that URL in the commit message.
2. **Verify** the download against the publisher's SRI string, then compute the hash
   you'll record (same value):
   ```bash
   openssl dgst -sha384 -binary <downloaded-file> | openssl base64 -A
   ```
   Prefix the output with `sha384-` to match the registry format.
3. **Place** the file under `static/vendor/`. Use a **version-pinned name** —
   `htmx-2.0.5.min.js`, `bootstrap-5.3.4/…` — and delete the old version's files in
   the same commit (the CI test rejects a registry `path` that doesn't exist, and a
   stale file left behind is dead weight). Preserve any sub-resource layout the CSS
   expects (e.g. Font Awesome's `css/all.min.css` resolving `../webfonts/...`).
4. **Update the registry** entry in `vendor_assets.py`: set both `path` (new filename)
   and `sha384` (step 2) together.
5. **Verify** (see "Verifying a change").
6. **Sub-resources / new files referenced by the CSS** (new webfont weights, new
   Poppins glyph ranges): add the files alongside, and if the entry-point CSS now
   references files the presence-checks don't yet cover, extend the corresponding
   assertion in `test_vendor_assets.py`.

### The Poppins special case

Poppins is not a vendor download — it's self-hosted Google Fonts output: a
hand-written `poppins/poppins.css` (`@font-face` rules) plus woff2 files fetched from
`fonts.gstatic.com`. Its `sha384` covers only the generated `poppins.css`. To change
weights or glyph ranges: request the desired CSS from the Google Fonts API with a
pinned `User-Agent` (the API serves different CSS per browser — pin it so the output
is reproducible), download every `.woff2` it references into `poppins/`, rewrite the
`src: url(...)` paths to local `/static/vendor/poppins/...`, save as `poppins.css`,
then recompute its `sha384` (step 2 above). The header comment in `poppins.css`
records the fetch date and source.

### Adding a brand-new vendored asset

1. Add the file(s) under `static/vendor/<name>/` and a new `VENDOR_ASSETS` entry with
   `kind` (`'css'` or `'js'`), `path`, and `sha384`.
2. Reference it from the page(s) that need it via the `vendor_css(['…'])` /
   `vendor_js(['…'])` macro, by the new key.
3. A `path`-only entry contributes **nothing** to the CSP beyond `'self'` (it's served
   locally) — no header edit needed. Verify as below.

### If an asset *must* stay genuinely external

Rare, and discouraged (it reintroduces a third-party origin into the CSP). Instead of
`path`, give the entry a `url` + `integrity` + `crossorigin`; if it fetches further
resources at runtime, add a `csp_extra` dict mapping CSP directives to extra sources,
e.g. `'csp_extra': {'font-src': 'https://fonts.gstatic.com'}`. The CSP builder
(`webapp/utils/csp.py`) derives the policy from the registry, so the external origin
flows into the right directive automatically (js→`script-src`, css→`style-src`+
`font-src`) — nothing else needs touching. The template macros emit a real browser
`integrity=`/`crossorigin` for `url` entries.

## Verifying a change

```bash
# one-time test-DB setup
docker compose --profile test up -d mysql-test
export SAM_TEST_DB_URL='mysql+pymysql://root:root@127.0.0.1:3307/sam'

source etc/config_env.sh && pytest tests/unit/test_vendor_assets.py
```

What the failures mean:

- **`test_committed_files_match_pinned_hashes`** — a file's bytes don't match its
  recorded `sha384`. You changed `path`/the file without updating the hash, or vice
  versa. Recompute and align them.
- **`test_paths_exist_under_static`** — the registry points at a file that isn't there
  (typo, or you bumped the version in the registry but didn't commit the new file /
  didn't delete-then-readd correctly).
- **`test_paths_are_version_pinned`** — the new filename/dir lacks a version (use
  `htmx-2.0.5.min.js`, not `htmx.min.js`).
- **`test_fontawesome_webfonts_present` / `test_poppins_woff2_present`** — a sub-resource
  the CSS references is missing from the directory.
- **`test_all_assets_are_local`** — an entry has a `url` (see the escape-hatch section;
  expected only for a deliberate external asset).

Also worth running after a change:

```bash
source etc/config_env.sh && pytest tests/unit/test_csp.py tests/unit/test_security_headers.py
```

to confirm the rendered CSP still reads as intended (vendored bumps should keep
`script-src 'self'` with no new origins).

## Deploy note

Bumping a version changes the served filename, hence the `url_for('static', …)` path.
Pages cached in Redis (`CACHE_DEFAULT_TIMEOUT=300s`) may reference the old path until
they expire; a hard cutover can flush the Flask-Cache Redis DB at rollout (same flush
the CSP rollout calls for). Browsers cache by URL, so a version-pinned filename also
gives you a clean cache-bust for free.

## See also

- `src/webapp/vendor_assets.py` — the registry (source of truth) + recipe docstring
- `src/webapp/utils/csp.py` — how the registry becomes the CSP header
- `tests/unit/test_vendor_assets.py` — the hash / presence checks described above
- `docs/plans/CSP.md`, `docs/plans/implemented/CSP-discussion.md` — full CSP rationale
```
