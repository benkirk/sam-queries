# Framework fixes from the SAMuel deck review: handoff

Untracked resume note, written 2026-10-02 at the end of the Appendix A/B session. The
retrospective found three framework fixes worth making and a few skill notes.

## Status (2026-10-02): merged

- **Merged:**
  - theme NCAR_beamer_template#9 (`d463234`);
  - framework #22 (`152b87c`). Re-vendoring from the theme's `main` changed nothing.
- **`samuel`:** rebased onto #22. The cleanup is `497acf3`, force-pushed.
- **The local-commit record follows** (the hashes predate the squash).

- **Theme** (`~/codes/NCAR_beamer_template`): branch `glyph-fallback`, `b5393d7`, v2.6.0.
  `make default quarto examples variants` builds.
- **Framework:** branch `framework-fixes`, `2ac87c8`, off `origin/main`.
  - It holds `single-body.lua`, the lint, the skill rows, the CLAUDE.md and README notes,
    and the theme files copied from `glyph-fallback`.
  - `sam_and_pbs` and `sample` report the same QA findings as `main`. Both already fail PDF
    checks there, which is out of scope.
- **`samuel`:** merged `framework-fixes` (`d7dba41`), then the cleanup (`b8b69fa`).
  - The 7 wrappers and the 4 raw-LaTeX titles are gone.
  - `make qa` passes on all 9 decks, and the pptx is byte-identical where wrappers were.
- **Changes from the plan below:**
  - Code blocks never split, so the filter ignores them. The 3 Appendix B wrappers held code.
  - The theme is vendored, so the glyph fix went to the theme repo first (CLAUDE.md's rule).
- **Left:** optionally restore arrows in the deck.

## Where things are

- **Framework repo:** `~/Documents/quarto-docs-framework`.
  - The deck is in `docs/samuel`, on branch `samuel` (draft PR #16).
  - The local branch tracks `origin/main` by mistake. Push with `git push origin HEAD:samuel`,
    or run `git branch -u origin/samuel` once.
  - Shared code lives in `docs/common/`:
    - filters: `notes-last.lua`, `strip-raw-figure.lua`, `slide-layout.lua`,
      `linked-images.lua`;
    - QA: `utils/deck_qa.py`;
    - the beamer theme: `_extensions/benkirk/ncar/`.
  - The QA skill is `.claude/skills/deck-polish/SKILL.md`.
- **sam-queries:** `docs/plans/SAMUEL_PRESENTATION.md` is the deck plan (§10 log, §11 tone and
  recipes). The open deck items are in `docs/plans/implemented/SAMUEL_HANDOFF.md`.
- **Activate the env:** from the framework root, run
  `eval "$(conda shell.zsh hook)"; conda activate ./conda-env`. Sourcing only `activate.d` is
  not enough, because quarto then can't find deno.
- **Run QA:** `make -C docs/samuel qa DECKS="..."`. It checks that pptx, PDF and HTML have the
  same slide count, overflow, and names, and writes contact sheets to `docs/samuel/_qa/`.
- **The shared-edit trap:** Ben edits the `.qmd` files in Emacs.
  - `.#file` (a lock) and `#file#` (an autosave) next to a file mean he has it open.
  - Run `git diff` before staging, and stage named paths only.
  - Auto-revert skips a modified buffer, so tell him to revert if you change a file he has
    open.

## Delivery

- **Branch:** `framework-fixes` off `origin/main` (not `samuel`), one PR against `main`, like
  #21. `sam_and_pbs` and future decks get it that way.
- **Then:** merge `main` into `samuel` and do the deck cleanups below in one commit there.
- **Pushing:** commit locally and push only when Ben asks. Use commit messages in the house
  format: `## Summary`, `### Test Results`.

## Fix 1: a pptx filter that stops content after a table, figure or code block from splitting the slide

- **The cost:** pandoc's pptx writer puts anything after a table, a diagram or a code block on
  an untitled continuation slide. QA reports this as a pptx count one higher than PDF and HTML.
  The deck works around it by hand: the body goes in `:::: {.columns}` /
  `::: {.column width="100%"}`. There are 7 of these wrappers today, and they cost about 3 QA
  reruns this session:

  | File | Wrappers |
  |---|---|
  | `_3-databases.qmd` | 2 |
  | `_4-systems.qmd` | 1 |
  | `_A-peers.qmd` | 1 |
  | `_B-apis.qmd` | 3 |

- **The fix:** a new `docs/common/single-body.lua`, modeled on `notes-last.lua` (which already
  fixed a sibling trap: notes before a columns div).
  - **pptx only.** Per level-2 slide, take the body blocks, excluding `.notes` divs.
  - If the body has no columns div, and a Table, Figure, CodeBlock or diagram output is
    followed by more blocks, wrap the whole body in one 100% column.
  - Other formats are untouched.
  - Find out how quarto's `{dot}` and `{mermaid}` output reaches the filter: a Figure, an
    Image in a Para, or raw. Note that `strip-raw-figure.lua` runs earlier.
- **Wiring:** add it after `notes-last.lua` in `docs/samuel/_quarto.yml` and
  `docs/sam_and_pbs/_quarto.yml`.
- **Out of scope:** content after an existing `.columns` block also splits. Moving it inside
  changes the layout, so that case stays a skill rule.
- **Deck cleanup:** remove the 7 wrappers, then re-run QA. Keep any wrapper the PDF layout
  depends on.

## Fix 2: glyph fallback in beamer for ⚠ and arrows

- **The cost:** Poppins has no U+26A0 or arrows. Four titles carry this markup:
  - `_2-concepts.qmd` ×2
  - `_3-databases.qmd` ×1
  - `_4-systems.qmd` ×1

  ```
  ## `{\fontspec{DejaVuSans-Bold.ttf}\symbol{"26A0}}`{=latex}[⚠︎\ ]{.content-hidden when-format="beamer"} Title
  ```

  The slides also write "to" where an arrow would read better.
- **The fix:** in `docs/common/_extensions/benkirk/ncar/ncar_branding.sty`, after fontspec
  loads (around lines 104 and 138), use `\newunicodechar` to map:
  - U+26A0 and → ← ↔ ⇒ to `DejaVuSans-Bold.ttf` / `DejaVuSans.ttf`, loaded by file name as
    the deck does today;
  - U+FE0E to nothing.

  HTML and pptx already fall back on their own. After this, `## ⚠︎ Title` works in all three
  formats.
- **Check:**
  - that it works with `themeoptions: [fonts=bundled]`;
  - that the frame title and the PDF bookmark both survive (hyperref may want
    `\texorpdfstring`);
  - that `pdffonts` shows DejaVu embedded.
- **Deck cleanup:**
  - simplify the 4 titles;
  - shrink the §11 warning-triangle recipe in `SAMUEL_PRESENTATION.md` to one line;
  - optionally restore arrows where "to" reads worse.

## Fix 3: a source lint in `deck_qa.py` for bare `<placeholder>`

- **The cost:** a speaker note said `projects/<code>/admin` without backticks. In revealjs,
  `<code>` became a real tag and swallowed every slide after it. `deck_qa` reported the result
  as "content past the right edge" on three unrelated slides, which was misleading. Three more
  placeholders (`<pod>`, `<machine>`, `<short>`) were silently dropped from the HTML notes.
  All four are fixed in framework `2ddeead`.
- **The fix:** before the render checks, scan the deck's sources: `<deck>.qmd` plus its
  `{{< include >}}`d `_*.qmd`.
  - Fail on `</?[a-z_]+>` outside backtick spans, fenced code blocks (including `{dot}` and
    `{mermaid}`), `{=html}` spans and lines starting with `<`.
  - Report `file:line`.
  - The current sources must pass. Watch for the HTML-like labels in `_er_core.qmd` and
    `_er_balance.qmd`, which sit inside `{dot}` fences.

## Skill notes, not framework changes

Add rows to the symptom → fix table in `.claude/skills/deck-polish/SKILL.md`:

- **A dot diagram's text is squashed in the PDF:**
  - The cause: `fig-width` and `fig-height` disagree with the graph's natural aspect.
  - Set only `fig-width`, or reshape. A vertical chain in a 40% column beside the bullets works
    (Appendix A, "`job_history`: logs to charges").
- **The pptx count is one higher, and content follows a `.columns` block:** move that content
  into the columns.
- **Bare `<placeholder>` in text or notes:** put it in backticks. Fix 3 catches it.

## Not framework debt

Leave these alone here:

- **`concepts_data.py` reads "now":** run after the FY27 start, it draws 0% used. It needs an
  as-of date, and is already in `SAMUEL_HANDOFF.md`.
- **The `samuel` branch's upstream:** local git config, covered above.
- **The repeated `{.vcenter scale="1.15"}`:** explicit, and `deck_qa` already hints it. An
  alias would hide the knob.

## Verification

1. **Fix 1:** remove the wrappers on 2–3 affected slides, then run
   `make -C docs/samuel qa DECKS="3-databases B-apis"`. The counts should match. After that,
   run it on `samuel` and `sam_and_pbs`, whose counts must not change.
2. **Fix 2:** the PDF title and the outline show ⚠ on a test slide, and `pdffonts` lists
   DejaVu. `make qa` passes.
3. **Fix 3:** put a bare `<code>` in a note in a scratch copy, and the lint fails with
   `file:line`. On the current sources it passes.
4. **Last:** a full `make qa` across all decks before the PR.
