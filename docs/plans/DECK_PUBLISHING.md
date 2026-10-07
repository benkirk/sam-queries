# Publish the SAMuel decks: `make publish` to GitHub Pages, CI later

**Status:** Proposal, 2026-10-07. Awaiting Ben's review; nothing built. Branch `deck-publishing`
(this doc only). Implementation is two PRs: one in `quarto-docs-framework`, then a submodule bump
here.

Related: `docs/plans/SAMUEL_PRESENTATION.md` § 7 (the open "publish the HTML deck" item),
`docs/presentations/README.md`, `docs/presentations/samuel/companion/README.md`.

## Progress

- [ ] Framework PR: `site` and `publish` targets in `docs/Make.common`, `site_index.py`,
      `ci-consumer.yaml` exercises `make site`, README "Publishing" section
- [ ] Here: bump the submodule pin
- [ ] Here: `_metadata.yml` (noindex), `Makefile` (`SITE_EXTRA`, `PUBLISH_PREFIX`), `.gitignore` `/*/_site/`
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

1. **`site`** (depends on `html`): build `$(SITE)` (default `_site/`), gitignored:
   - copy each `$(DECKS).html`;
   - merge every `*_files/libs/` into one `$(SITE)/libs/` (`rsync -a` per deck gives the union;
     identical files overwrite harmlessly) and rewrite `<deck>_files/libs/` → `libs/` in each HTML
     (32 references per deck; a `sed` in the recipe, or a few lines in the index script);
   - copy any other `*_files/` content per deck (none today; keeps future python figures working);
   - copy `images/` if present;
   - copy `$(SITE_EXTRA)` directories verbatim (consumer hook; SAMuel sets `companion`);
   - write `.nojekyll`;
   - write `index.html` with a new `docs/common/utils/site_index.py`: reads each deck's `.qmd`
     front matter (`title`, `subtitle`), lists decks in `DECKS` order plus any `SITE_EXTRA`
     pages, inline CSS in the brand style (same approach as `companion/pipeline.html`).
2. **`publish`** (depends on `site`): `PUBLISH_BRANCH ?= gh-pages`, `PUBLISH_REMOTE ?= origin`,
   `PUBLISH_PREFIX ?=` (path under the Pages root; SAMuel uses `presentations/samuel` so the root
   stays free for other decks later). Recipe: stage `$(SITE)` into a scratch git repo,
   `git fetch $(PUBLISH_REMOTE) $(PUBLISH_BRANCH)` first (so the push negotiation knows what the
   remote already holds and sends only changed objects: the deduped `libs/` never changes, so a
   typical publish moves ~1 MB, not 16), then one **orphan** commit with a clean message (no
   skip-ci tokens: the branch triggers nothing anyway, but the message is grepped repo-wide),
   `git push --force $(PUBLISH_REMOTE) HEAD:refs/heads/$(PUBLISH_BRANCH)`, then print the Pages
   URL. `.nojekyll` lives at the branch root.
3. `DEPS` gains `$(wildcard _metadata.yml)` (see PR 2) and `clean` removes `$(SITE)`.
4. `ci-consumer.yaml`: add `make -C "$DECK" site` and assert `grep -q 'libs/revealjs' hello.html`
   in `_site/` and that no `_files/libs` remains. README gains a "Publishing" section.

**Why frequent publishing never grows the repo.** Pages keeps no versions; it serves the branch
tip. The branch holds exactly one commit at any time; the previous one goes unreachable and
GitHub garbage-collects it on its own schedule (the reported size can lag, never grow unbounded).
No rollback beyond rerunning `make publish` from an older checkout (accepted). Branch-based Pages
is soft-limited to 10 builds per hour, and each push shows up in the Actions tab as GitHub's own
`pages-build-deployment` run (free on a public repo). Stage 2's workflow-driven deploy lifts the
hourly cap if it ever matters.

### PR 2: this repo

1. Bump the submodule pin to PR 1's merge.
2. `docs/presentations/samuel/Makefile`: `SITE_EXTRA := companion`,
   `PUBLISH_PREFIX := presentations/samuel`.
3. New `docs/presentations/samuel/_metadata.yml` (directory metadata, applies to every deck in
   the directory; pptx and beamer are untouched because it is scoped to the one format):
   ```yaml
   format:
     ncar-revealjs:
       include-in-header:
         text: '<meta name="robots" content="noindex">'
   ```
   `noindex` on every page is the whole "not indexed" mechanism. No `robots.txt` `Disallow`,
   which would stop crawlers from ever reading the meta tag.
4. `docs/presentations/.gitignore`: add `/*/_site/`.
5. `docs/presentations/samuel/companion/README.md`: the live page becomes the Pages URL
   (`.../presentations/samuel/companion/pipeline.html`); the Artifact URL stays listed as the
   previous home until the slide link in `_5-deployment.qmd` is repointed.
6. `docs/presentations/README.md`, a "Publishing" section: the one-time Pages enable (Ben runs
   it), then `make -C docs/presentations/samuel publish`, and the public-repo hygiene rule already
   in the companion README (no IPs, OpenBao paths, real names in slides or screenshots).
   ```bash
   gh api -X POST repos/benkirk/sam-queries/pages \
     -f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/'
   ```
7. `docs/plans/SAMUEL_PRESENTATION.md` § 7: tick the publish item with the decision and URL.

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
