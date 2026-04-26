# Shared presentation infrastructure

This directory holds the infra that's shared across every Quarto→pptx deck
under `docs/presentations/`: the NCAR-branded reference template, the Poppins
font blobs, the post-render font-embedding script, and (via `../Makefile.common`)
the render recipes themselves.

Each presentation is its own sibling directory (`overview/`, `roadmap/`, ...)
with a ~3-line `Makefile` that includes the shared recipes. Adding a new deck
should never involve copying template files or render logic around.

## Layout

```
docs/presentations/
├── Makefile.common               # shared recipes (pptx / html / pdf / clean)
├── common/                       # ← you are here
│   ├── assets/
│   │   └── fonts/                # Poppins .fntdata blobs (EOT-subsetted)
│   │       ├── poppins-regular.fntdata
│   │       ├── poppins-bold.fntdata
│   │       ├── poppins-italic.fntdata
│   │       └── poppins-bolditalic.fntdata
│   ├── branding/
│   │   └── ncar/
│   │       └── template.pptx     # NCAR reference pptx (pandoc-compliant)
│   └── utils/
│       └── embed_poppins.py      # post-render font embedder
└── overview/                     # one presentation
    ├── Makefile
    ├── _quarto.yml
    └── overview.qmd
```

Room to grow: additional brand packages (`common/branding/ucar/`,
`common/branding/cisl/`) or additional shared assets (`common/assets/images/`)
drop in as sibling subdirs without disturbing existing decks.

## Build pipeline

Per deck, `make pptx` runs:

1. `quarto render <deck>.qmd --to pptx -o <deck>.pptx`
   - Uses `reference-doc: ../common/branding/ncar/template.pptx` from the
     deck's `_quarto.yml`, so the output inherits NCAR theme colors, title
     slide layout, masters, etc.
2. `python3 ../common/utils/embed_poppins.py <deck>.pptx`
   - Re-injects the four Poppins variants as `<p:embeddedFont>` entries.
     Pandoc strips these on reference-doc copy; the script puts them back so
     the font travels with the file.

`make html` (revealjs) and `make pdf` (beamer) are also wired up but don't
consume the pptx template or fonts.

## Adding a new presentation

```bash
cd docs/presentations
mkdir roadmap && cd roadmap

cat > Makefile <<'EOF'
OUT := roadmap

include ../Makefile.common
EOF

cat > _quarto.yml <<'EOF'
project:
  type: default

format:
  pptx:
    reference-doc: ../common/branding/ncar/template.pptx
    slide-level: 2
    toc: false
EOF

# then author roadmap.qmd (no format: block needed — inherits from _quarto.yml)
make pptx
```

## Template constraints

The reference template under `common/branding/ncar/template.pptx` must stay
pandoc-compliant. Two requirements pandoc is strict about:

- **`slideLayout1` placeholders**: must include all five of `ctrTitle`,
  `subTitle`, `dt`, `ftr`, `sldNum`. Pandoc emits empty `<p:sp/>` stubs for
  any missing placeholder, which PowerPoint then flags as corrupt ("Repair?"
  prompt).
- **Layout names**: pandoc looks up layouts by their display name. The title
  slide layout must be named exactly `Title Slide`; other layouts pandoc
  knows about: `Title and Content`, `Section Header`, `Two Content`,
  `Comparison`, `Content with Caption`, `Blank`. Missing names don't break
  the build but do trigger a `Couldn't find layout named …` warning and fall
  back to pandoc's bundled layout (losing NCAR styling for that slide).

Verify after any template edit:

```bash
unzip -p common/branding/ncar/template.pptx ppt/slideLayouts/slideLayout1.xml \
  | grep -oE '<p:ph[^/]*/>'     # expect 5 matches
```

## About the font embedding

The theme is wired to Poppins via `<a:fontScheme name="Poppins">`, so
slide masters/layouts all dereference to Poppins through `+mj-lt` / `+mn-lt`.
Embedding the font blobs on top of that is only needed when the `.pptx`
will be opened on a machine without Poppins installed — without the embed,
PowerPoint substitutes (typically Calibri) and the deck loses its look.

If portability stops mattering, `embed_poppins.py` can be dropped from the
pipeline: remove the `python3 $(EMBED_POPPINS) $@` line from
`../Makefile.common` and delete this directory's `assets/fonts/` +
`utils/embed_poppins.py`.
