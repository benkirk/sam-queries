# Presentations

The SAMuel decks live in the
[quarto-docs-framework](https://github.com/benkirk/quarto-docs-framework)
repo, on its long-lived `samuel` branch, under `docs/samuel/`: a full deck
plus one deck per part, built as pptx, revealjs HTML and NCAR-branded beamer
PDF. The plan and phase log are in `docs/plans/SAMUEL_PRESENTATION.md`.

To work on them from this tree, link a framework checkout here. The link is
gitignored, because an absolute symlink would dangle everywhere else:

```bash
git clone -b samuel https://github.com/benkirk/quarto-docs-framework ~/Documents/quarto-docs-framework
ln -s ~/Documents/quarto-docs-framework/docs/samuel docs/presentations/samuel
cd docs/presentations/samuel && make pptx    # after `source etc/config_env.sh` in the framework
```

The deck that lived here (`overview/`, April 2026) was retired in #679; it is
in git history.
