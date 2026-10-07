# Handoff: SAMuel deck

Untracked resume note, refreshed 2026-10-01, late (Ben's review session, batch 1 and its follow-ups). The durable record is
`docs/plans/SAMUEL_PRESENTATION.md` (§4 outline, §8 phases, §10 log, §14 fix-later list), on
branch `samuel-presentation-part2`, draft PR #681 against `staging`.

## Where things stand

- **Parts:**
  - 1 Overview;
  - 2 Concepts;
  - 3 Databases;
  - **4 Who Talks to Whom** (systems and data flow, drafted);
  - **5 Deployment & Operations** (hosting, GitOps, deployment, CNPG; drafted 2026-10-01);
  - A Peers.

  The DECKS line is `samuel 1-overview 2-concepts 3-databases 4-systems 5-deployment A-peers B-apis C-performance`.
- **Framework `samuel`:** 13 deck commits on `main` (through #20), `ca66e38` pushed;
  framework #16's body refreshed.
- **PR #681:** rebased onto staging; plan doc records the layout controls (§13, §10).
- **Slide layout controls (2026-10-01):**
  - theme #8 (v2.5.0) and framework #20 add `.hcenter` / `.vcenter` / `.center` /
    `scale=` / `.fill`;
  - both merged 2026-10-01. `samuel` (`ca66e38`) is rebased onto `main`, and 22 short slides
    in Parts 1–4 use the controls.
- **Ben's Part 4 framing:**
  - legacy SAM is drawn as grey boxes everywhere;
  - we don't yet own the SQL schema.

## Ben's review session (2026-10-01, late)

- **The loop:** Ben edits the deck in Emacs. I read `git diff`, sweep the other parts for the
  same treatment, apply what he picks, run `make qa`, and commit locally. Nothing is pushed.
  - Framework commits `b1aef62`..`800b76f`; plan-doc commits `a6f93ff1`..`e4e45a97`. §10 logs
    batch 1.
- **Decisions that now apply to the whole deck:**
  - **Bold:**
    - bullets with bold lead terms: Part 1 and every part's closing slide only;
    - table row labels: everywhere.
  - **Tone:** plain in Part 1 (§11). PSA titles are now warning triangles: `## ⚠︎ Title`.
  - **Footnotes:** `†`, then `‡`, each its own paragraph. One after a table or diagram is fine
    (`single-body.lua`); one after a `.columns` block goes inside the last column.
  - **HSG:** HSG's cron downloads from SAMuel's API and runs none of our Python.
    `fsparsetree_mr` runs inside the API, behind `/api/v1/fairshare`. The 12-minute cron is
    Ben's playground; keep it off the slides.
  - **Hosts:** the VMs are `sam-app` (LDAP replica, transformer, `sam-ldap-syncd`),
    `sam-tomcat` (legacy SAM) and `sam-sql.ucar.edu` (MySQL). The directory is
    `ldap.ucar.edu`; the CNPG cluster is `csg-postgres.k8s.ucar.edu`.
- **New since the Part 5 session:**
  - Part 2's "Who, where, and who pays" slide;
  - the Casper shared-pool example (NMMM0080);
  - Part 3's "Every date is a timestamp" slide;
  - production sizes on Part 3's database map (2026-10-01);
  - placeholder Appendices B "Two Dialects" (APIs) and C "Keeping It Snappy" (performance).
    `DECKS` now lists `B-apis C-performance`; the combined deck is 149 slides.
- **Shared-edit trap:** check for Emacs `.#file` / `#file#` and `git diff` the file before
  staging (memory `feedback_concurrent_edit_check`).
- **Open:**
  - Ben reviews Appendix B (drafted 2026-10-02); fill Appendix C (FIXMEs in its notes);
  - `concepts_data.py` needs an as-of date: run after the FY27 start, it draws 0% used;
  - "The whole neighborhood" could get VM boxes, which needs a move to `dot`.

## Building

- **Activate the framework env:** `conda activate ~/Documents/quarto-docs-framework/conda-env`,
  or source `conda-env/etc/conda/activate.d/*.sh` with `conda-env/bin` on PATH.
- **Full build:** `make -C docs/samuel all` takes about 75 s for 21 outputs. One part alone:
  `make -C docs/samuel 4-systems.pdf 4-systems.pptx 4-systems.html`.
- **Refresh the frozen data:**

  ```bash
  SAMUEL_REPO=~/codes/project_samuel/devel ./refresh_data.sh
  ```

  Run it with the sam-queries Python. It now also writes `_out_schedule.qmd`.

## Next

- **2026-10-02: Appendix D "Failing Closed" drafted.**
  - Framework `3df2622`: 7 slides, wired into `DECKS` and `samuel.qmd`; the combined deck is 186.
  - Plan `ac5ec1d2`: §4, §10, and §14 (the API-key limit counted per IP, stale security docs,
    NRIT opens).
  - Both are local, not pushed. Ben reviews Appendix D.
- **2026-10-02: Appendix C drafted.**
  - Framework `38d623d`: 15 slides; QA ok, combined deck 178. Also fixes Appendix B's
    `directory_access` number.
  - Plan `099112c7` on `samuel-presentation-part2`: §4, §10, and §14 (the deploy-aware cache
    invalidation follow-on, and stale perf docs).
  - Both are local, not pushed. Ben reviews Appendix C.
  - Bold lead terms (Ben, 2026-10-02): an appendix bolds only its first and last slides.
    Done for C (`ca38dd0`). Appendix B still bolds every slide.

0. **Framework fixes: merged** (2026-10-02).
   - Theme NCAR_beamer_template#9 is v2.6.0; framework #22 is merged.
   - `samuel` is rebased onto #22 (`497acf3`) and now tracks `origin/samuel`, so a plain
     `git push` works.
   - **Optional:** restore arrows where "to" reads worse.
1. **Ben reviews Parts 2 and 4.** For Part 4, the points are:
   - the title;
   - the grey-box convention;
   - whether the scheduler hosts and the LDAP provisioner have repointed.
2. **Ben reviews Part 5** (framework `f49e8ac`, local, not pushed).
   - **Calls already made:** no PSAs; HPC deployment as one broad slide; backups "planned";
     the Claude skills slide.
   - **Open:** softening Part 4's ncar-hpc-deploy box and notes to match.
   - **Companion page** (private): https://claude.ai/artifact/Tp4XaBDH7BmvUbPT6UUbtS.
   - **HTML spot check:** not yet done.
3. **Ben reviews Appendix A** (drafted 2026-10-01, 12 slides; plan §4 has the facts).
   - **Settled:** the hooks and `samuel2sql` are HSG's; `hpc-scheduling-tools` keeps
     reference copies (Ben, 2026-10-02).
   - **Confirm:** HSG's production cron downloads the fairshare tree from `/api/v1/fairshare`
     (no repo shows it).
   - **Optional:** a `qhist` row on "Who's who".

## QA loop and traps

- **Short slides:** use `{.center .fill scale="S"}`, with S sized for the PDF (no autofit
  there).
- **Diagram shape:** keep diagrams near 2–3:1. Ben: the aspect can flex to fill the page.
  - Wide chains become snakes or rings with pinned `layout=neato` `pos="x,y!"`. Clusters are
    lost under neato.
- **Footnotes:** a `†` after a `.columns` block splits the pptx slide. Put it in the last
  column or in the notes. After a table or diagram it is fine.
- **Glyphs:** theme 2.6 draws ⚠ → ← ↔ ⇒ from DejaVu Sans in the PDF. Other symbols Poppins
  lacks still drop out.
- **Bare `<placeholder>`:** `make qa` fails on it; put it in backticks.
- **pptx demotion:** bullets before a table or image demote the slide.
- **Reshooting a screenshot:** use the §6 recipe (the `samuel-shots` container on `mysql-test`,
  port 5051). `docker compose run` uses the image's code, which can predate the feature. Add
  `-v <a clean origin/main worktree>:/code`; the image installs `/code` editable. Part 1's
  Allocations shot was redone this way on 2026-10-02, for #697.
- **Previewing the HTML:** serve it with `python3 -m http.server`. Playwright MCP may only
  write under the devel worktree's `.playwright-mcp/`; delete it afterwards.
- **zsh:** write `${ref}:path`, not `$ref:path` (a history modifier).
