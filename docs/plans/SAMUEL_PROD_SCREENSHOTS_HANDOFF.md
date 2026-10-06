# SAMuel deck: production screenshots with redaction — handoff

**Status (2026-10-06):** proposed, not started. Blocked on Ben's sign-off (§2).
The deck is `docs/presentations/samuel/` (211 slides). The plan and log are in
`docs/plans/SAMUEL_PRESENTATION.md`.

## 1. Why

Two appendices need screenshots that the obfuscated test DB (port 3307) cannot supply.

**Appendix F, "Running the Shop" (#715), has no screenshots.** The anonymizer empties exactly the
tables its pages show:
- account requests and events;
- `notification_log` and the addressing rows;
- role grants.

The details are in `containers/sam-sql-dev/config.yaml` and `containers/sam-sql-dev/anonymize_sam_db.py`. Production
has the rows, and shows real names and email addresses.

**Appendix E's My Data slide (#714) has no screenshot.** My Data is pinned server-side to the
signed-in user. Ben's own data is a few files, and the plugin databases are real anyway.

Ben's idea (2026-10-03) is to shoot these in production and redact the names.

## 2. Decision needed first

`SAMUEL_PRESENTATION.md` §6 says never to screenshot production. This handoff proposes amending
that to "production only under this protocol":
- read-only;
- redacted in the page;
- checked by OCR;
- approved by Ben, image by image.

**Record Ben's sign-off and the date in §6 before any shot is taken.**

## 3. What already exists — reuse it

| Need | Tool |
|---|---|
| A real SSO session | `scripts/dev_capture_session.py --base https://sam.hpc.ucar.edu --out <scratchpad>/prod_state.json` |
| Scripted shots | `scripts/ui_snapshots.py` |
| OCR | `tesseract`, installed (`/opt/homebrew/bin/tesseract`) |
| A browser | Playwright's own Chromium (the `[e2e]` extra; `playwright install chromium`) |
| Name check | the deck's `qa-names.txt`, scanned by `make qa` |
| pptx preview | the scratchpad LibreOffice profile with Poppins (memory `reference_soffice_sandbox_fonts`) |

**The session script.** You log in once, in a headed window, with Entra and 2FA. It then writes a
Playwright `storage_state.json` and refuses a path inside the repo.

**The shot script.** It already takes `--storage-state`, `--base-url`, `--layout`, `--theme`,
`--element`, `--step`, `--modal` and `--recipes FILE` (see `scripts/ui_snapshots_modals.json`).

**No new browser is needed for screenshots.** The "headless Chrome" fix in §8 is a different
problem: quarto's renders.

## 4. What to build (one small PR, `scripts/` only)

Extend `ui_snapshots.py` with three options. Each is opt-in, so local use is unchanged.

**`--read-only`: the write guard.**
- `context.route("**/*", ...)` aborts every request whose method is not GET or HEAD. A stray
  click on Reject, Send or Deactivate never reaches production.
- Print each aborted request, so a shot that needed a POST shows up in a dry run.
- `--modal` and `--step` click today, which is why the guard sits at the network layer and not
  in the recipes.
- Make `--read-only` required whenever `--base-url` is not localhost.

**`--redact FILE.js`: redaction in the page, before capture.**
- Run it with `context.add_init_script`, plus a `MutationObserver`, so content that htmx swaps in
  later is redacted too. Capture after the network is idle and the observer has run.
- **Emails:** any `x@y.z` in text nodes, attributes (`title`, `href`, `value`) and SVG `<text>`
  becomes `user_<hash8>@example.org`.
  - `hash8` is a stable hash of the original, so the same person reads the same on every page.
  - That matches the `user_xxxxxxxx` look of the deck's other shots.
- **Names and usernames:** cells chosen per page by a selector in the recipe (columns such as
  User, Name, Email, Owner, Sponsor, Lead) become `user_<hash8>`. Prefer selectors to guessing
  from text.
- **Paths:** a path segment that matches a username on the page (for example under `/glade/` or
  `/campaign/`) gets the same pseudonym.
- **Never blur.** Blurred text can be recovered, and a box misses content that moved. Here the
  real text never reaches the PNG.
- Keep the originals in memory only; they feed the check below and are never written.

**`--verify`: the check after capture.**
- OCR each PNG with Tesseract. Fail the shot on:
  - any email-shaped token;
  - any original the redactor replaced (the in-memory list);
  - any `qa-names.txt` name.
- A failed shot is deleted, not kept.

**Where files go.**
- Captures go to the scratchpad only.
- Ben approves each image by eye before it is copied into `docs/presentations/samuel/images/`.
- No unredacted file ever sits under a repo path. Both repos are public.

## 5. The shot list

Write it as a recipes JSON, e.g. `scripts/ui_snapshots_prod_deck.json`. Each entry gives:
- the URL;
- any steps (tabs to open);
- an element crop;
- the redaction selectors;
- the size: 1440×810, light, as in Part 1. Set `sam_theme=light` and emulate light.

**Appendix F:**

| Slide | Page | Redact |
|---|---|---|
| Account requests | `/admin/account-requests`, a few rows | names, emails, sponsors |
| Events and invitations | `/admin/events`, plus one event's enrollees | enrollee names, emails |
| Expirations | the Expirations section on `/admin/projects` | lead and admin names (project codes are fine) |
| Mail | the Notifications log and Addressing tabs | every address |
| Templates | the Notifications Templates tab | nothing: it shows `src/sam/notify/samples.py` data |
| Tasks | `/admin/htmx/tasks` run history | nothing: no personal data |
| Configuration | a crop of a few cards | skip the audit-log lines and env addresses, or redact them |
| Configuration | the Rate limits card | actor names |
| Configuration | Last seen (`/admin/users/last-seen`) | the bucket counts only, or redacted rows |

**Appendix E:** a disk-scans view with real volume.
- My Data is pinned to the signed-in user, so use the project-mode explorer of a busy project
  (`/dashboards/user/disk-scans/...`) or Status → Filesystem Scans.
- Redact username path segments.
- Do not use impersonation for this.

## 6. Procedure

1. Ben signs off (§2).
2. Capture the session once:
   ```bash
   python scripts/dev_capture_session.py --base https://sam.hpc.ucar.edu --out <scratchpad>/prod_state.json
   ```
3. Dry-run without saving images:
   ```bash
   ui_snapshots.py --read-only --storage-state ... --recipes ...
   ```
   Fix any recipe that needs a blocked request.
4. Shoot with `--read-only --redact ... --verify` into the scratchpad.
5. Ben reviews every image.
6. Copy the approved images in, then wire them into `_F-admin.qmd` and `_E-jobs.qmd`. E's My Data
   notes say the shot waits for this shoot.
7. Run `make -C docs/presentations/samuel qa` and read every page of E and F, including the
   names scan and the pptx render.
8. Delete the session file, and open the PR.

## 7. Risks

| Risk | Mitigation |
|---|---|
| The session file leaks | It is a credential: scratchpad only, never committed, deleted after the shoot |
| Login lockout | The login tier is 5/min per IP, and every request shares one IP bucket (the lost client IP). Capture once and reuse the cookie |
| A write to production | `--read-only` blocks every non-GET request; navigate by URL |
| Redaction misses content | The observer catches late htmx content, `--step` opens lazy tabs, then OCR, then Ben's review |
| Real data in a public repo | Only redacted, reviewed PNGs are committed, under the §6 protocol |
| Ben's views are recorded as "seen" | Harmless; it is his own activity |

## 8. Separate prerequisite: reliable deck builds

On 2026-10-03, a render of the combined PDF hung for nearly two hours on the system Chrome.
Quarto uses headless Chrome for mermaid.

- **The fix:** install quarto's own browser in the framework env, as the framework's CI does:
  ```bash
  quarto install chrome-headless-shell --no-prompt
  ```
  This belongs in a small quarto-docs-framework PR: in its Makefile's env setup and README. It is
  independent of the screenshots.
- **Until then:** wrap each render in `gtimeout 900 make <target>`, so a hang fails in minutes.

## 9. Hot context

**Worktrees and branches.**
- Work in a scratchpad worktree; `devel/` holds Ben's own branches.
- sam-queries branches start from `origin/staging`; framework branches from `origin/main`.
- Initialize the submodule in a new worktree:
  `git submodule update --init docs/presentations/framework`.

**Environment.**
- Builds use the framework env: `conda activate ~/Documents/quarto-docs-framework/conda-env`
  (real activation; quarto needs deno). Set `PYTHONDONTWRITEBYTECODE=1`.
- The local `samuel` container (port 7050) runs the plugins against the local
  `hpc-usage-postgres` copy. That is real data: Ben's own pages only.

**Fixing the framework.** Fix it upstream, then move the pin. #718 is the model for a pin move.

**Open elsewhere:** the fix-later list in `SAMUEL_PRESENTATION.md` §14. Its top items are the
per-IP API-key rate limit, NRIT findings with no tracker, and deploy-aware cache invalidation.
