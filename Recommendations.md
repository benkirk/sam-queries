# Presentation Tooling Recommendations

Options evaluated for creating a technical presentation covering SAM project
capabilities, schema, and deployment — including code snippets, flowcharts,
shell commands, and architectural diagrams.

---

## Option 1: Quarto + RevealJS (HTML Slides) — Recommended for sharing/web

**Best for:** Browser-based delivery, live demos, sharing a URL

```bash
pip install quarto   # or: brew install quarto
quarto render presentation.qmd --to revealjs
```

**Pros:**
- Markdown source — version-controlled alongside the codebase
- Native Mermaid support (flowcharts, sequence diagrams, ER diagrams) — no extra filter needed
- Excellent syntax-highlighted code blocks (Python, SQL, bash)
- Single `.qmd` source can emit HTML, PDF, or PPTX

**Cons:**
- HTML output; printing/PDF export requires a headless browser (Chromium)
- Less typographic control than LaTeX

**YAML front matter:**
```yaml
---
title: "SAM: System for Allocation Management"
format:
  revealjs:
    theme: dark
    highlight-style: monokai
    slide-number: true
---
```

---

## Option 2: Quarto + Beamer — Recommended for polished PDF (familiar to LaTeX users)

**Best for:** PDF handouts, conference-style slides, LaTeX-comfortable authors

```bash
quarto render presentation.qmd --to beamer
```

**Pros:**
- Familiar LaTeX/Beamer output — no new paradigm for Beamer veterans
- Mermaid diagrams render natively (converted to PDF automatically)
- Clean code block rendering with syntax highlighting
- Same source can also emit PPTX or RevealJS if needed
- `\pause`, basic overlays, and raw LaTeX blocks all work

**Cons:**
- Complex TikZ animations/overlays are awkward (basic `\pause` works fine)
- Slightly less fine-grained frame layout control than raw Beamer

**YAML front matter:**
```yaml
---
title: "SAM: System for Allocation Management"
format:
  beamer:
    theme: metropolis
    colortheme: default
    aspectratio: 169
---
```

---

## Option 3: Pandoc + Beamer (Raw LaTeX) — For maximum LaTeX control

**Best for:** Authors who want full Beamer control and are comfortable with raw `.tex`

```bash
pandoc presentation.md -t beamer -o presentation.pdf \
  --pdf-engine=xelatex \
  --filter pandoc-mermaid-filter   # for Mermaid diagrams
```

**Pros:**
- Full Beamer/LaTeX power — overlays, TikZ, custom frame layouts
- No new tool to learn beyond Pandoc

**Cons:**
- Mermaid requires `pandoc-mermaid-filter` (extra install, needs Node/npm)
- Less ergonomic than Quarto for mixed code+diagram content
- No multi-format output without separate render commands

---

## Option 4: Quarto + PPTX (PowerPoint with template)

**Best for:** Audiences/contexts requiring `.pptx` files

Quarto uses Pandoc's `reference-doc` mechanism — works identically to
`pandoc --reference-doc=my-template.pptx`:

```yaml
---
format:
  pptx:
    reference-doc: ncar-template.pptx
---
```

**Pros:**
- Full PPTX template support (colors, fonts, master slides from `.pptx`)
- Familiar format for non-technical stakeholders

**Cons:**
- Mermaid diagrams must be pre-rendered to PNG/SVG (not inline)
- Layout control is limited compared to native PowerPoint editing

---

## Diagram Tools

| Need | Tool | Notes |
|---|---|---|
| Flowcharts, sequence diagrams | **Mermaid** (inline in Quarto) | Best integration; no extra install |
| Architecture boxes/arrows | Mermaid `graph TD` or `C4Context` | Covers most needs |
| DB schema / ER diagrams | `eralchemy2` | Auto-generates from SQLAlchemy models |
| Complex architecture | PlantUML | Requires Java; more powerful than Mermaid |
| Publication-quality figures | TikZ (LaTeX only) | Beamer/Pandoc+LaTeX only |
| Python dependency graph | `pydeps` | Useful for codebase structure slides |

### Mermaid example (flowchart)
```mermaid
graph TD
    A[sam-search CLI] --> B[CLI Core / Context]
    B --> C[User Commands]
    B --> D[Project Commands]
    B --> E[Allocation Commands]
    C & D & E --> F[SQLAlchemy ORM]
    F --> G[(SAM MySQL DB)]
```

### Auto-generate ER diagram from live DB
```bash
pip install eralchemy2
eralchemy2 -i 'mysql+pymysql://root:root@127.0.0.1/sam' -o schema.pdf
```

---

## Summary Recommendation

| Priority | Choice |
|---|---|
| Familiar workflow, PDF output | **Quarto + Beamer (Metropolis theme)** |
| Web sharing / live demo | **Quarto + RevealJS** |
| PowerPoint required | **Quarto + PPTX + reference-doc template** |
| Maximum LaTeX control | **Pandoc + Beamer** |

**Bottom line for a Beamer veteran:** Start with Quarto + Beamer. You keep
familiar PDF output, gain Mermaid for architecture diagrams without TikZ,
and preserve the option to emit PPTX or HTML from the same source file later.

---

*Generated during initial presentation planning session, 2026-04-18.*
