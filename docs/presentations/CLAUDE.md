# Presentations subtree — Claude context

Scope: this file is auto-loaded when working under `docs/presentations/`.
For user-facing docs, see `README.md` in this directory.

## Architecture at a glance

Each deck is its own sibling dir (`overview/`, …) with a ~3-line `Makefile`
that `include`s `../Makefile.common`. All render logic, the NCAR reference
template, Poppins blobs, and `embed_poppins.py` live under `common/`.
Never copy them into a deck dir.

Template: `common/branding/ncar/template.pptx`
Fonts:    `common/assets/fonts/poppins-*.fntdata`
Embedder: `common/utils/embed_poppins.py`
Recipes:  `Makefile.common` (pptx / html / pdf / clean)

## Template constraints — do not regress

The reference template must stay pandoc-compliant. Two rules pandoc is
strict about:

1. **`slideLayout1` must have five placeholders**: `ctrTitle`, `subTitle`,
   `dt`, `ftr`, `sldNum`. Pandoc emits empty `<p:sp/>` stubs for any missing
   placeholder — empty `<p:sp>` violates OOXML §19.3.1.43 and triggers
   PowerPoint's "Repair?" prompt on open.

2. **Layout display names must match pandoc's expected set** exactly
   (case-sensitive): `Title Slide`, `Title and Content`, `Section Header`,
   `Two Content`, `Comparison`, `Content with Caption`, `Blank`. Mismatches
   don't break the build but emit `Couldn't find layout named "X"` warnings
   and fall back to pandoc's bundled layout (losing NCAR styling on that slide).

**Edit the template via PowerPoint's Slide Master** (View → Slide Master),
not by patching XML. Hand-patched placeholders get stripped by pandoc on the
next render anyway — the fix must live in the template, not in the output.

Verify after any template edit:
```bash
unzip -p common/branding/ncar/template.pptx ppt/slideLayouts/slideLayout1.xml \
  | grep -oE '<p:ph[^/]*/>'     # expect 5 matches
```

## Path gotchas

- **`reference-doc` is resolved relative to `_quarto.yml`**, not CWD. From
  a deck subdir, that's `../common/branding/ncar/template.pptx`.
- **A `.qmd` frontmatter `format:` block overrides `_quarto.yml`.** If
  pandoc's debug output shows a wrong `reference-doc`, check the `.qmd`
  first — don't assume the yaml is authoritative.
- **`embed_poppins.py` finds fonts via `HERE.parent / "assets" / "fonts"`**
  (script at `common/utils/`, blobs at `common/assets/fonts/`). If you move
  either, update `FONTS_DIR` at the top of the script.
- **`Makefile.common` uses `$(dir $(lastword $(MAKEFILE_LIST)))`** to
  locate `common/utils/embed_poppins.py` regardless of which deck subdir
  invokes it. Don't hardcode relative paths that break if a deck is nested
  deeper.

## Font embedding is opt-in

The theme is wired to Poppins via `<a:fontScheme>` — that travels in the
template regardless. `embed_poppins.py` only adds the font *glyphs* so the
deck looks right on a viewer machine that doesn't have Poppins installed.
If portability stops mattering:

- drop the `python3 $(EMBED_POPPINS) $@` line from `Makefile.common`
- delete `common/assets/fonts/` and `common/utils/embed_poppins.py`

Don't gate this on a test run against your own machine — you have Poppins
in `~/Library/Fonts/`, so the substitution path is invisible here. Test on
a colleague's machine or a fresh VM.

## Adding a new deck

Three files, no template copying:

```bash
mkdir docs/presentations/<name> && cd docs/presentations/<name>
printf 'OUT := $(notdir $(CURDIR))\n\ninclude ../Makefile.common\n' > Makefile
cat > _quarto.yml <<'EOF'
project:
  type: default
format:
  pptx:
    reference-doc: ../common/branding/ncar/template.pptx
    slide-level: 2
    toc: false
EOF
# author <name>.qmd with NO format: block — inherits from _quarto.yml
```

If the user asks for a new deck, use this pattern — don't invent a new
Makefile shape or re-declare the render recipe.

## Minor behaviors worth knowing

- **Quarto regenerates `<deck>/.gitignore`** on every render with
  `/.quarto/` + `**/*.quarto_ipynb`. The top-level `.gitignore` already
  covers these, so the per-deck file is redundant but harmless. Don't
  bother deleting it — it'll come back.
- **`overview_files/` and similar `<deck>_files/`** are quarto scratch
  dirs. Covered by the top-level `**/*_files/` ignore. `make clean`
  removes them.
- **`~$<deck>.pptx`** is PowerPoint's lock file for an open deck. If it
  lingers, PowerPoint crashed; safe to delete.

## History (why things are the way they are)

- The "three empty `<p:sp/>` stubs cause PowerPoint to prompt for repair"
  issue was chased down in April 2026 — the fix was editing the NCAR
  template's slideLayout1 to add the three missing placeholders
  (`dt`/`ftr`/`sldNum`). The prior investigation mistakenly pursued font
  embedding as the root cause; it's orthogonal.
- `common/branding/<brand>/template.pptx` (with the `<brand>/` layer)
  exists to leave room for additional brand packages (UCAR, CISL-specific)
  without renaming the NCAR template.
