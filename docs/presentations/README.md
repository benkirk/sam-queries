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
