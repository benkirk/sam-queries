# Vendored asset updates

**Status: planned 2026-11-03, not started. The pass starts only on an explicit
go-ahead; approving this plan starts nothing.**

The webapp serves its own copies of its front-end libraries from
`src/webapp/static/vendor/`. The CSP allows `'self'` only, so there is no CDN
fallback (`docs/plans/implemented/CSP.md`). Those copies change only when we
bump them on purpose. This plan is one pass of the `update-vendored-assets`
skill (`.claude/skills/update-vendored-assets/SKILL.md`) across every vendored
asset. The skill is the procedure, and this plan follows it. Where the plan adds
to it, the change is listed under "Deviations from the skill" with its reason.

The mechanical file swap is not repeated here. It lives in
`src/webapp/static/vendor/README.md` § "Bumping an existing asset".

## Scope

`VENDOR_ASSETS` in `src/webapp/vendor_assets.py` is the source of truth. As of
this plan:

| Asset | Registry key(s) | Shipped | Upstream to check | Read most closely for |
|---|---|---|---|---|
| Bootstrap | `bootstrap-css`, `bootstrap-js` | 5.3.8 | GitHub `twbs/bootstrap` releases | modal, collapse, tooltip and dark-mode token changes |
| htmx | `htmx` | 2.0.10 | GitHub `bigskysoftware/htmx` releases | event order, request params, error events |
| Font Awesome Free | `fontawesome-css` | 7.3.1 | GitHub `FortAwesome/Font-Awesome` releases (Free only) | renamed or retired icons, webfont family name |
| Poppins | `poppins` | Google Fonts v24, 300-700 | Google Fonts | family or weight changes |
| NWSC user agreement | none (`src/webapp/register/eula.md`) | as vendored | `NCAR/HPC-Docs` via `scripts/update_eula.py` | any wording change |

jQuery is not vendored, so there is nothing to bump.

**htmx in this pass.** Only an in-major (2.x) bump is in scope. The skill calls
htmx's changelog the highest-value one to read, because the dashboards rely on
its event and request behavior.
- A Safe 2.x release goes to Phase B like any other asset.
- A 2.x release that touches a documented workaround is High-risk and stops for
  a decision. The workarounds cover dangling-target errors, GET param dedupe and
  `htmx:configRequest` on chart renders, so even a patch release can land here.
- A new major is reported in Phase A and stops there. Migrating to it gets its
  own plan, because the skill has no major-upgrade procedure for htmx (unlike
  Font Awesome). The Phase A report is that plan's starting point.

## How the pass runs

| Phase | Skill steps | Changes repo files | Ends with |
|---|---|---|---|
| A. Investigate | 1-4, plus the EULA check | no (the EULA script rewrites the file in place; revert unless it is wanted) | a report per asset |
| B. Apply | 5-7 | yes, on a branch off `staging` | one commit per applied asset |
| C. Verify and hand off | 8-9 | no | gates green, before/after snapshots, PR on request |

As the skill says, a **Safe** bump goes straight on to Phase B. A **High-risk**
bump, or any EULA wording change, stops after Phase A for a decision. Phase A
finds out on paper whether a bump fixes a bug we work around or breaks behavior
we rely on, before any file moves.

### Phase A: investigate

1. Confirm the inventory above against `VENDOR_ASSETS`.
2. Find the latest release of each asset. Mark it **in-major** (patch or minor)
   or **major**.
3. Read each changelog from the shipped version to the candidate. Check it
   against the skill's workaround table and the skill's step-3 `rg` sweep (the
   sweep catches workarounds the table has not caught up with).
4. Classify each candidate:
   - **Safe:** in-major, and it touches no workaround, CSP or icon-name surface.
     Proceeds to Phase B.
   - **High-risk:** a major, or it touches one of those. Report a
     breaking-change summary and stop for a decision.
5. EULA: run `python scripts/update_eula.py`, then
   `git diff src/webapp/register/eula.md`. No diff means current. Any diff is
   reported and stops for a decision.
6. Record the baseline: testing checkpoint 1, below.

The report has one row per asset:

| Asset | Shipped → candidate | Class | Fixes a workaround? | Breaks something we rely on? | Recommendation |
|---|---|---|---|---|---|

### Phase B: apply

Follow the README recipe. These invariants must not slip:
- Download the publisher's SRI-carrying artifact and put its URL in the commit
  message.
- Verify the bytes against the publisher's digest with a script, never by
  reading a hash back by eye. cdnjs publishes sha512; the registry records
  sha384 of the same bytes.
- Use a version-pinned path, update `path` and `sha384` together, and delete the
  old files in the same commit.
- Keep the relative layouts the CSS resolves: Font Awesome's `../webfonts/` and
  the Poppins `*.woff2` files.

**Font Awesome major (if one is approved).** Always High-risk. A major renames or
retires glyph classes and can change default metrics (FA7 made icons
fixed-width, so measure action-cell wrap before and after). The webfont family
name is versioned, so grep it, or the two pseudo-element glyphs in
`src/webapp/static/css/dashboard.css` vanish. To modernize class names instead
of relying on shims:
- Build the old→new map from the release's `icons.json`, never by hand. Invert
  `aliases.names` to alias→canonical over the names we use, and confirm every
  target is in the **free** set at the style we use it. A name that is neither
  canonical nor an alias is Pro, so pick a free replacement.
- Find files by the prefix token (`fas|far|fab`) as well as `fa-`, so a template
  that carries the glyph in a Jinja variable (`class="fas {{ row.icon }}"`)
  is not missed.
- Sweep `tests/` as well as `src/webapp/`, and update icon-markup assertions in
  the same change.

**Poppins (only if weights, ranges or family change).** Request the CSS from the
Google Fonts API with a pinned `User-Agent` (the API serves different CSS per
browser). Download every `.woff2` it references into `poppins/`, rewrite each
`src: url(...)` to `/static/vendor/poppins/...`, and recompute the `poppins.css`
hash. Refresh the matplotlib TTF copy in `src/webapp/static/fonts/poppins/` to
match. Never delete it as unreferenced: matplotlib cannot read woff2.

**EULA (only if a wording change is approved).** Rules from
`docs/plans/implemented/ACCOUNT_REGISTRATION.md` § 6.2:
- Never hand-edit `eula.md`, not even a typo or a link. Fix it upstream and
  pull it again.
- Commit the refresh alone, with the blob SHA and upstream ref the script prints.
- Review the diff for raw HTML (the text is injected unescaped), for mkdocs-only
  syntax (it renders as literal text), and for relative links that do not match
  the sibling `*.md` pattern `eula.py` rewrites (they 404).

Shape of the change:
- One commit per asset, so any single bump can be reverted alone.
- A workaround that a bump makes unnecessary is removed in a **separate
  follow-up**, not the bump commit, so reverting the bump cannot bring the bug
  back.
- A workaround that needs to work *differently* under the new version blocks
  that bump.

### Phase C: verify and hand off

Run testing checkpoints 4 and 5 below. Open the PR (`--base staging`) only when
asked. At rollout, run `sam-admin cache --refresh` so cached pages stop pointing
at the old filenames.

## Testing

Two tiers, which complement each other (`docs/plans/implemented/FRONTEND_TEST_NET.md`):
- **Unit gates** (`tests/unit/gates/`): Python checks of hashes, CSP and markup
  contracts. Fast, and they run on every commit.
- **Browser smoke**: the Playwright suite in `e2e/` (`make e2e`), plus a short
  manual pass. It catches what Python cannot see: console errors such as
  `htmx:targetError`, rendering, and interaction.

Before any test, flush Redis and restart samuel-dev. Otherwise both tiers can
pass against stale copies (`wire-dashboard-feature` step 12).

### Checkpoints

| # | When | Runs | Why |
|---|---|---|---|
| 1 | End of Phase A, on the base branch, before any file moves | full `pytest`; `make e2e`; `scripts/ui_snapshots.py --out <dir>/before` | a failure after the bump is then the bump's, not one that was already there |
| 2 | After each asset commit | the common gates below, plus that asset's unit row | every commit stays green on its own, so it can be reverted on its own |
| 3 | After each visible asset commit (Bootstrap, Font Awesome, Poppins) | that asset's e2e row and manual row; `ui_snapshots.py --out <dir>/after`, compared with `before` | the browser tier sees what the gates cannot |
| 4 | Branch done, before the PR | full `pytest`; `make docker-build`, then `make e2e` against :7050 | the :7050 image bakes its code, so an e2e run without a rebuild tests the old assets |
| 5 | On the PR, then after the staging merge | `browser-smoke.yaml` (runs on the PR); after the merge, samuel-dev via the `watch-dev` skill, both themes by eye | CI's clean stack first, then the real deploy path with real OIDC and the Redis page cache |

Common gates (checkpoint 2, every asset):

```bash
source etc/config_env.sh
pytest tests/unit/gates/test_vendor_assets.py tests/unit/gates/test_static_assets.py \
       tests/unit/gates/test_template_csp_lint.py \
       tests/unit/webapp/test_csp.py tests/unit/webapp/test_security_headers.py
```

### Per asset

| Asset | Unit gates (add to the common set) | e2e (`e2e/`) | Manual smoke (3 layouts × 2 themes) |
|---|---|---|---|
| Bootstrap | `test_modal_shell_contract.py`, `test_collapse_trigger_rows.py`, `test_action_cells_nowrap.py`, `test_css_tokens.py` | `test_console_sweep.py`, `test_issue356_collapse_rows.py`, `test_dialog_back.py`, `test_pr378_regressions.py`, `test_dark_mode.py` | stacked modals; a toggle inside an open modal; tooltips after an htmx swap; pinch-zoom on a phone; table rows in dark mode |
| htmx | `test_modal_shell_contract.py` | `test_console_sweep.py`, `test_pr378_regressions.py`, `test_facet_chips_multiselect.py`, `test_chart_expand_return.py` | filter forms (no duplicated GET params); full-page chart render at each layout |
| Font Awesome | `test_action_cells_nowrap.py` | `test_phone_wraps.py`, `test_console_sweep.py` | the two `src/webapp/static/css/dashboard.css` glyphs (navbar caret, collapse chevron); spinners; the icon inventory on `/dev/gallery` |
| Poppins | `test_chart_fonts.py`, `tests/unit/charts/test_chart_fingerprints.py` | `test_dark_mode.py` | headings and body text; charts. A font change that moves the chart fingerprints regenerates them in the same commit (`CHART_FINGERPRINT_REGEN=1`). |
| EULA | `tests/unit/webapp/test_account_registration.py -k "Eula or Gate"`, `tests/unit/gates/test_docs.py` | none | the `/register` gate in both themes, after restarting webdev (`eula_html()` is cached per process) |

### Retiring a workaround

The follow-up that removes a workaround a bump made unnecessary first adds a
browser test named for the original bug. It follows the pattern of
`e2e/test_issue356_collapse_rows.py` and `e2e/test_pr378_regressions.py`. Prove
it bites, the way `FRONTEND_TEST_NET.md` § Verification does: run it against
the previous library version without the workaround and confirm it fails, then
run it on the bump and confirm it passes. If the bug ever returns, it then fails
loudly instead of silently.

### Rollback

Each asset is one commit with its old files deleted, so `git revert` of that
commit restores the previous version, hash included. Run checkpoint 2 again
after any revert.

## Deviations from the skill

Each one adds to the skill; none relaxes it.

| Plan does | Skill says | Why |
|---|---|---|
| Starts the pass only on an explicit go-ahead | nothing about starting | a pass touches the network, the tree and samuel-dev, so someone decides when |
| Baseline run before any change (checkpoint 1) | gates after a bump only | a failure after the bump can then be blamed on it |
| Extra unit gates (`test_static_assets.py`, `test_template_csp_lint.py`, `test_action_cells_nowrap.py`, `test_css_tokens.py`, chart fingerprints, EULA tests) | vendor, CSP and header gates, plus modal/collapse for Bootstrap and chart fonts for Poppins | each covers a surface the matching library can change |
| Named `e2e/` tests per asset, and `make e2e` after `make docker-build` | a browser smoke by hand, 3 layouts × 2 themes | the suite already exists and catches console errors a manual pass misses; the :7050 image bakes its code |
| One commit per asset | delete old files in the bump's commit | each bump can be reverted alone |
| A browser test before a workaround is removed | remove it in a separate follow-up | the bug cannot come back unnoticed (`FRONTEND_TEST_NET.md`) |
| PR only on request; samuel-dev checked after merge | deploy note only | matches the repo's PR workflow, and dev is the first real deploy |

## Known housekeeping

- The skill's workaround table reads "htmx (currently 2.0.4)", but the registry
  ships 2.0.10. Fix it in Phase B, with or without an htmx bump.
- When a pass finishes, record its outcome here (versions taken, workarounds
  retired), then move this file to `docs/plans/implemented/`.
