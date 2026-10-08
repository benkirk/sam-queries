# Presentations

The SAMuel decks: a full deck plus one deck per part and appendix, each built
as pptx, revealjs HTML and an NCAR-branded beamer PDF.

```text
docs/presentations/
├── framework/   quarto-docs-framework, a git submodule (pinned)
└── samuel/      the SAMuel decks: sources, images, frozen data
```

## How it works

The build machinery is
[quarto-docs-framework](https://github.com/benkirk/quarto-docs-framework):
the NCAR pptx template and its post-render steps, the beamer and revealjs
themes, and the `make qa` layout checks. This repo takes it as a submodule,
pinned to one commit, and keeps only the decks.

A deck directory needs one line of Makefile:

```make
DECKS := samuel 1-overview 2-concepts ...
include ../framework/docs/Make.common
```

On the first build, make links `_quarto.yml`, `_extensions` and `_ncar` from
the framework into the deck directory. Those links and the outputs are
gitignored (`.gitignore` here).

## Building

```bash
git submodule update --init docs/presentations/framework   # once per clone
make -C docs/presentations/framework conda-env             # once: quarto, pandoc, python-pptx
conda activate docs/presentations/framework/conda-env
cd docs/presentations/samuel
make pptx            # or html, pdf (needs TeX), all, qa
make qa DECKS=samuel # layout checks on one deck
```

The framework has its own environment. This repo's `conda-env` doesn't build
decks.

## Publishing

The HTML decks are published to GitHub Pages at
<https://benkirk.github.io/sam-queries/presentations/samuel/>: every deck, an
index, and the companion pages.

```bash
cd docs/presentations/samuel
make site      # _site/: the decks, one shared libs/, images/, companion/, index.html
python3 -m http.server -d _site   # look at it first
make publish   # pushes _site/ as the one commit on gh-pages
```

The `gh-pages` branch is a build artifact, not a record: each publish replaces
it with a single parentless commit, so it never grows, and each publish uploads
the whole site (about 17 MB). Pages serves the branch tip and keeps no versions.
Enable it once, from the branch (`gh api -X POST repos/benkirk/sam-queries/pages
-f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/'`).

The URL is public but every page carries `noindex` (`SITE_NOINDEX` in the
Makefile), so search engines leave it alone. The repo is public too, so the
rule from `samuel/companion/README.md` holds for everything that renders: no
IPs, no OpenBao paths, no real names in slides or screenshots. The knobs
(`SITE_EXTRA`, `SITE_NOINDEX`, `PUBLISH_PREFIX`, `PUBLISH_BRANCH`) are the
framework's: README "Publishing the HTML decks". Decision record:
`docs/plans/DECK_PUBLISHING.md`.

## Changing the framework

Fix the framework upstream, then move the pin here. Don't edit the submodule
in place: its checkout is a detached HEAD, and the change would be lost.

1. Change and merge it in quarto-docs-framework (its CI builds a deck laid out
   like this one).
2. Here, run `git -C docs/presentations/framework pull origin main`, rebuild,
   run `make qa`, and commit the new submodule pointer.

The NCAR beamer theme follows the same path one level down: it is fixed in
NCAR_beamer_template and re-vendored into the framework.

## Notes

- **Skills:** `.claude/skills/deck-polish` links to the framework's
  deck-polish skill (the `make qa` loop and the fixes for what it finds). It
  works once the submodule is checked out.
- **Frozen data:** `samuel/refresh_data.sh` regenerates the frozen data and
  charts from this checkout and the obfuscated test database. It is
  hand-run; a build never runs it.
- **Doc gates:** the doc gates (`tests/unit/gates/test_docs.py`) treat this
  tree as a record. MegaLinter and the Docker build context leave it out.
- **Plan and phase log:** `docs/plans/SAMUEL_PRESENTATION.md`.
