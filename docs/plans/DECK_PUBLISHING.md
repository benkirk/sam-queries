# Publish the SAMuel decks: `make publish` to GitHub Pages, CI later

**Status:** PR 1 open (quarto-docs-framework#28, 2026-10-07); PR 2 not started. Branch
`deck-publishing` (this doc only). Implementation is two PRs: one in `quarto-docs-framework`,
then a submodule bump here on a branch cut from the then-current `origin/staging`.

Related: `docs/plans/SAMUEL_PRESENTATION.md` § 7 (the open "publish the HTML deck" item),
`docs/presentations/README.md`, `docs/presentations/samuel/companion/README.md`.

## Progress

- [x] Framework PR: `site` and `publish` targets in `docs/Make.common`, `site_index.py`,
      `ci-consumer.yaml` exercises `make site`, README "Publishing" section
      (quarto-docs-framework#28, 2026-10-07; awaiting merge)
- [ ] Here: bump the submodule pin
- [ ] Here: `Makefile` (`SITE_EXTRA`, `SITE_NOINDEX`, `PUBLISH_PREFIX`), `.gitignore` `/*/_site/`
- [ ] Here: `docs/presentations/README.md` "Publishing"; companion README live page; tick
      `SAMUEL_PRESENTATION.md` § 7
- [ ] Ben: enable Pages on `gh-pages` (one `gh api` call, below), run `make publish`, load the URL
- [ ] Later, separate PR: the workflow driver (Stage 2)

## Context

`docs/presentations/samuel/` builds 13 revealjs decks (12 parts + the combined `samuel.html`)
through the `quarto-docs-framework` submodule, but nothing publishes them. Surveyed 2026-10-07:

- No target in the framework's `docs/Make.common` (`pptx html pdf all qa clean distclean`), no
  `quarto publish`, no rsync. The framework's own CI only uploads Actions artifacts.
- No workflow here installs Quarto; every test workflow lists `docs/**` under `paths-ignore`;
  `.mega-linter.yml` excludes `docs/presentations/`.
- `.dockerignore` excludes `docs/presentations/` (the submodule's `conda-env/` is ~1.5 GB), the
  webapp has no docs/help route and no `send_from_directory`, the helm chart serves no static
  content (one ingress path, `/`, to Flask).
- The only precedent is `companion/pipeline.html`, republished by hand as a public claude.ai
  Artifact (`companion/README.md`).
- Rendered output is untracked (`docs/presentations/.gitignore`: `/*/*.html`, `/*/*_files/`).

Facts that shape the design:

- The repo is **public** (`benkirk/sam-queries`, personal account, no Pages site: the Pages API
  returns 404). Pages lands at `https://benkirk.github.io/sam-queries/`.
- The HTML is **not self-contained**: `embed-resources` is commented out in the shared
  `_quarto.yml` (the theme turns `chalkboard` on, which blocks it). Each deck ships
  `<deck>_files/libs/` (129 files; 9–12 MB: revealjs 8.0 MB, quarto-diagram 2.7 MB, fonts 1.1 MB)
  and references `images/*.png` by relative path (48 references in `samuel.html`). The 13
  `libs/` copies are subsets of one union: `samuel_files/libs` has everything, ten decks lack
  only `quarto-diagram`. One shared `libs/` cuts the site from ~140 MB to ~16 MB.
- Decks do not cross-link and there is no index page.
- The framework pins Quarto via conda, but HTML needs only Quarto: mermaid renders live in
  revealjs and graphviz is WASM, so a CI build needs neither Chrome nor TeX.

Ben's decisions (2026-10-07): destinations in reach are GitHub Pages and nwc1 k8s (Stratus is
possible but not turnkey); the deck may be **public but not search-indexed**; publish **all decks
plus the companion pages**. No versioning wanted; frequent publishing must not do anything
unexpected.

### Options weighed

| Option | Verdict |
|---|---|
| **GitHub Pages on this repo** | Recommended. Zero infra, matches the public repo, `noindex` meta covers "not indexed". |
| k8s nginx + ingress on nwc1 | The upgrade path if the audience ever tightens to NCAR login: serve the same `gh-pages` branch via git-sync behind oauth2-proxy. Not now. |
| Webapp serves its docs | No: couples deck edits to app deploys, drags Quarto into the image build, and the only merit (an OIDC gate) is had more cheaply by the k8s option. |
| `quarto publish gh-pages` | No: it wants to own the project render and `output-dir`, fighting the symlinked shared `_quarto.yml` and the multi-deck Makefile. Borrow its shape instead: a fresh single-commit `gh-pages` branch plus `.nojekyll`. |
| claude.ai Artifact | Fine for one hand-written page (as the companion is); 13 decks × 129 files is the wrong tool. |
| Stratus bucket | Parked; not turnkey, and Pages already satisfies the requirement. |

## Recommended approach

**One recipe, two drivers.** `make publish` in the deck directory assembles a static site and
force-pushes it as a single commit to the `gh-pages` branch. Stage 1 runs it from a laptop;
Stage 2 (separate PR, later) runs the same target from a workflow. The branch is a build artifact
like `cirrus`/`cirrus-dev`, but disposable: a fresh orphan commit each publish.

The generic parts live in the **framework** (its other decks want the same thing, and its
`ci-consumer.yaml` can exercise the target); the SAMuel-specific parts live here. Two PRs,
framework first, then a submodule bump. Alternative, if one PR is preferred: put the recipe in
`docs/presentations/samuel/Makefile` and lift it later.

### PR 1: `quarto-docs-framework`, `site` and `publish` targets in `docs/Make.common`

As built in quarto-docs-framework#28 (`docs/Make.common`, `docs/common/utils/site_index.py`):

1. **`site`** (depends on `html`): builds `$(SITE)` (default `_site/`, gitignored):
   - every `<deck>_files/libs/` merged into one `$(SITE)/libs/` (`rsync -a` per deck gives the
     union) and `<deck>_files/libs/` rewritten to `libs/` in each HTML (32 references per deck);
   - any other `*_files/` content per deck (none today), `images/`, and `$(SITE_EXTRA)` directories
     verbatim (SAMuel sets `companion`);
   - `index.html` from `site_index.py`: each deck's `.qmd` front matter (`title`, `subtitle`) in
     `DECKS` order, then every `*.html` under the extra directories by its `<title>`; brand
     colors and Poppins, light and dark;
   - `SITE_NOINDEX=1` puts `<meta name="robots" content="noindex">` on every deck and the index.
     That is the whole "not indexed" mechanism; no `robots.txt` `Disallow`, which would stop
     crawlers from ever reading the tag.
2. **`publish`** (depends on `site`): `PUBLISH_BRANCH ?= gh-pages`, `PUBLISH_REMOTE ?= origin`,
   `PUBLISH_PREFIX ?=` (path under the Pages root; SAMuel uses `presentations/samuel` so the root
   stays free for other decks later). Stages `$(SITE)` under the prefix in a scratch repo with
   `.nojekyll` at the root, one **parentless** commit with a clean message (no skip-ci tokens:
   the branch triggers nothing anyway, but the message is grepped repo-wide), `git push --force`
   to the branch, then prints the Pages URL for a GitHub remote.
3. `DEPS` gains `$(wildcard _metadata.yml)`, `clean` removes `$(SITE)`, `ci-consumer.yaml` builds
   the site for the hello deck and checks the rewrite, the tag and the index. README gains
   "Publishing the HTML decks".

**Why frequent publishing never grows the repo.** Pages keeps no versions; it serves the branch
tip. The branch holds exactly one commit at any time; the previous one goes unreachable and
GitHub garbage-collects it on its own schedule (the reported size can lag, never grow unbounded).
No rollback beyond rerunning `make publish` from an older checkout (accepted). Branch-based Pages
is soft-limited to 10 builds per hour, and each push shows up in the Actions tab as GitHub's own
`pages-build-deployment` run (free on a public repo). Stage 2's workflow-driven deploy lifts the
hourly cap if it ever matters.

**Every publish uploads the whole site** (~17 MB for SAMuel). Measured while building #28: a
parentless commit gives git no edge to delta against, so fetching the old tip first changes
nothing (166 objects pushed either way, trees identical). Keeping history would make the
transfer small and the branch grow; the one-commit branch was the decision, so the upload is
the price. Seconds on a laptop, nothing in CI.

### PR 2: this repo

1. Bump the submodule pin to PR 1's merge.
2. `docs/presentations/samuel/Makefile`, before the include: `SITE_EXTRA := companion`,
   `SITE_NOINDEX := 1`, `PUBLISH_PREFIX := presentations/samuel`.
3. `docs/presentations/.gitignore`: add `/*/_site/`.
4. `docs/presentations/samuel/companion/README.md`: the live page becomes the Pages URL
   (`.../presentations/samuel/companion/pipeline.html`); the Artifact URL stays listed as the
   previous home until the slide link in `_5-deployment.qmd` is repointed.
5. `docs/presentations/README.md`, a "Publishing" section: the one-time Pages enable (Ben runs
   it), then `make -C docs/presentations/samuel publish`, and the public-repo hygiene rule already
   in the companion README (no IPs, OpenBao paths, real names in slides or screenshots).
   ```bash
   gh api -X POST repos/benkirk/sam-queries/pages \
     -f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/'
   ```
6. `docs/plans/SAMUEL_PRESENTATION.md` § 7: tick the publish item with the decision and URL.

### Stage 2 (not in this plan; sketch for the record)

`.github/workflows/publish-decks.yaml`: `push` to `main` with `paths: docs/presentations/**`
plus `workflow_dispatch`; `actions/checkout` with `submodules: true`;
`quarto-dev/quarto-actions/setup@v2` with a pinned version (no conda, no TeX, no Chrome);
`make -C docs/presentations/samuel publish` with `permissions: contents: write`; the repo's
skip-ci `if:` guard and a concurrency group. Same recipe, second driver. Switching to
`actions/deploy-pages` (no branch) is possible then, but the branch keeps the laptop path alive.

## Verification

- Framework: `make -C docs/sample site`, then `make -C docs/sample publish PUBLISH_REMOTE=<fork>`;
  `ci-consumer.yaml` green.
- Here, after the bump: `make -C docs/presentations/samuel site`, then
  `python3 -m http.server -d _site` and open `presentations/samuel/` in a browser: the index
  lists 14 entries, each deck loads with **no 404s** in the network panel (the `libs/` rewrite is
  the thing to check), `grep -c '_files/libs' _site/presentations/samuel/*.html` is 0 everywhere,
  `grep -l 'robots.*noindex'` matches every deck, the companion page opens.
- `du -sh _site` ≈ 16 MB (13 decks + one `libs/` + `images/` + companion).
- `make publish`, Ben enables Pages once, then load
  `https://benkirk.github.io/sam-queries/presentations/samuel/`.
- `pytest tests/unit/gates/test_docs.py` for the README, companion README and plan edits
  (`docs/presentations/` and `docs/plans/` are record prefixes).
- Nothing else in the repo changes, so the normal suite is not needed.
