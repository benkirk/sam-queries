---
name: pr-review
description: >-
  Reviewing one of this repo's pull requests comprehensively but not
  pedantically: read it as one design, trace the few places a logic gap hides,
  report in plain terms first, let Ben pick what lands, land it as one commit
  per finding on the PR branch, post one findings comment, record the outcome.
  Load when Ben gives a PR number to review (`/pr-review 803`).
---

# Review a pull request

`/pr-review N`. The output is a verdict Ben can act on, plain terms first, and the fixes he
picks, landed on the PR branch. Comprehensive means every claim in the PR body was checked and
every consumer of a changed contract was read; not pedantic means a finding is a logic gap, a
missing proof or a piece that will be copied, never a style note. "Nothing to change" is a
complete answer.

## 1. The brief

```bash
scripts/pr_review_brief.py N          # PR, commits, checks, body, diff stat, rules, skills, memory
scripts/pr_review_brief.py N --json
```

Then, before reading any code:

- **Read every rule it lists** with the Read tool. A rule loads only when a matching file is
  Read or edited (`CLAUDE.md`, Topic rules); the brief matching a path is not the same thing.
- **`cat` the memory files** behind the index lines it prints: an earlier session may have
  shaped the PR (the design choice, the deferral, the incident). The brief matches the PR
  number, the head branch and the PRs the body cites; when it prints none, the PR is new to
  memory, so scan `MEMORY.md` for its area (#806 had two lines under the predecessor, #800).
- **Load the skills it names.** `wire-dashboard-feature` is the checklist for any dashboard
  path. `unplanned-city-sweep` is the lens for every PR: its "read as one design" seams
  (prose describing an earlier step, a silent path promoted to the default, the assumed branch
  being the untested one, a route covered only by a route-map pin) and its lift / consolidate /
  propagate passes answer Ben's standing question, "will this be repeated?".

## 2. Read the diff by layer

```bash
git diff origin/<base>...origin/<head> -- src/sam/ src/scheduling/   # models, queries, manage
git diff origin/<base>...origin/<head> -- src/webapp/ src/cli/        # routes, templates, CLI
git diff origin/<base>...origin/<head> -- tests/                      # then the proofs
git diff origin/<base>...origin/<head> -- docs/ '*.md'                # then the record
```

Source first, so the tests are read as claims about code already understood. A diff over
~30 KB lands in the persisted-output file; `sed -n '/^diff --git a\/<path>/,$p'` on that file
is cheaper than a second diff. Shell notes that cost a turn each: zsh expands `=word`, so a
separator is `echo '-----'`; `$files` does not word-split (use `${=files}`); `--include=*.py`
is globbed, so quote it; read pytest's exit code from `${pipestatus[1]}`, never from a
`| tail`.

**If the head moves during the review** (the owner pushes while you read), `git fetch` and
diff `<reviewed head>..origin/<head>`: run step 3 on that delta only, and say in the report
and the comment which head the review covers.

## 3. Trace where a gap hides

The list below is where the three reviews that shaped this skill found their findings. Trace
each one that applies; say which ones did not.

- **Every failure path ends in a status, never a 500.** Follow each exception class a route
  can see to the handler that catches it; a subclass the handler does not know is a 500 (#801:
  every XRAS error subclasses one base, so one `except` covers them).
- **Dedup, ordering, concurrency.** A row gets its id at INSERT and becomes visible at
  COMMIT, so "lowest id wins" has a window (#803). A dedup key narrower than the unit of work
  lets two live rows coexist (#800: `(username, purpose)` let a submission and a reactivation
  both stay open). Ask what two concurrent callers see at the moment each one checks.
- **Every consumer of a new vocabulary member.** A new status, purpose or kind: grep each
  consumer (`status.in_`, `== '…'`, `*_STATUSES`, facet orders, badge dicts, CLI styles, the
  digest) and decide per consumer whether the new member belongs (#803 `duplicate` in the
  worklist; #800 `reactivation` in `resolve_requests`).
- **Windows and clocks.** What a date filter keys on and whether both feeds carry that date;
  naive-Mountain versus UTC (`CLAUDE.md` §1); whether "first seen" means the thing the label
  says (#800 keyed the pill on the queue row's `creation_time` because XRAS has no approval
  timestamp).
- **Cached snapshots.** A template reading a key the last sweep did not write must guard by
  truthiness, or the first render after deploy breaks (`wire-dashboard-feature`, step 12.5).
- **The PR body's own claims.** "No extra round trip", "the same people", "cannot both pass",
  "byte-identical": each is one check, and the review says which held and which did not.
- **PII, `no-store`, logs.** What leaves the DB and lands in a cache, a card, a mail or a log
  (#801: counts and type codes cached, never filenames; the proxy logs who fetched what).
- **Layer boundaries.** `sam` never imports `webapp`; `src/scheduling/` imports no Flask,
  Click or rich; `sam/queries/__init__.py` stays empty (the import-graph gates in
  `tests/unit/gates/`).
- **Dashboard PRs.** `wire-dashboard-feature` steps 5-7 (modal and collapse traps, table
  columns and action cells), 9 (CSP, static assets) and 12 (smoke and gates) as a checklist.

## 4. Verify the tests prove the claim

For each fix the PR describes, find the test and ask what assertion the bug would have
passed. A test that imports a new name cannot have failed on the old code, so it proves the
name exists, not the fix. A visual change regenerates its fingerprint or snapshot in the same
commit (`CLAUDE.md`, Tripwires). Report CI from the brief; when it is still pending, say so.

## 5. Report

In this order, in the terminal:

1. **In plain terms.** Four to eight sentences on what the PR does and what was checked, with
   no file paths, column names or status strings. Ben relays this to people who do not read
   the code, and it comes before any question.
2. **Verified sound.** Bullets, each with a `file:line`, one per trace in step 3 that held.
3. **Findings.** Ranked `F1..Fn`. Each carries a tag, *fix now* / *note, no change* / *ops*,
   the mechanism in two or three sentences, and the fix with its size (files, lines, whether a
   snapshot moves). Recommend on merit; the cost is a separate fact in the description.

Then one `AskUserQuestion`, multi-select, the recommended fixes first and "None, merge as is"
last. Never ask while an Explore agent is still running: every option must rest on finished
research.

An *ops* finding that one browser look would settle (a phone-width cell, a dialog hosted on
a new page) gets that look after the comment, offered, not assumed: a samuel-dev login is
Ben's (Playwright window, his 2FA). When no live row carries the data, inject the markup
into the live cell and measure with `getBoundingClientRect` (`wire-dashboard-feature`,
step 12.3); the screenshots carry dev's names and emails, so they stay in the session.

## 6. Land the picks

- **Get onto the PR head.** Already checked out here: `git pull --ff-only origin <head>`.
  Elsewhere: `git checkout <head>`. When `git worktree list` shows it checked out in another
  worktree (another session's), do not touch that tree: `git checkout -B review-<head>
  origin/<head>`, and push later with `git push origin HEAD:<head>`. Say so in the comment,
  since that worktree is then behind.
- **One commit per finding**, named paths staged (`git add <paths>`), the house message:
  a one-line title, `## Summary` saying the mechanism and the fix, `### Test Results`, the
  attribution lines. Apply the comment budget (`CLAUDE.md` §12): the fix keeps the trap in a
  compressed comment, never a changelog.
- **Tests.** Run what the diff touches plus the gates it implicates: `test_docs.py` for any
  comment or markdown; the `wire-dashboard-feature` step 12 list for templates and CSS; the
  route-map snapshot for a route. CI runs the full suite; the review does not.
- **Push**, then read the SHAs for the comment.

## 7. Comment

```bash
gh pr comment N --body-file - <<'EOF'
## Review
<one-sentence verdict>; <what landed, with SHAs>; <what was noted>.

### Verified sound
- ...

### F1 — landed (`<sha>`)
### F2 — note, no change
### F3 — ops
...
🤖 Generated with [Claude Code](https://claude.com/claude-code)

<session link>
EOF
```

Always, after the picks land, even when nothing was picked: the comment is the record. Write
the F sections as the terminal report had them, plus the SHA. Never write a bare skip-ci token
in it (`CLAUDE.md`, Skipping CI).

## 8. Close out

Update the PR's project memory, or write one: reviewed on which date, what landed (SHAs), what
was noted and why it was left. One index line in `MEMORY.md` says the same in a few words.
When Ben merges while the session is open, add the squash SHA to both. The local branch is
then gone; the worktree stays, whatever `gh pr merge` suggests about removing it.

## Boundaries

- Legacy-compat blueprints take additive changes only; a fix there must not move response bytes.
- Do not build for a failure nobody has seen: a finding names a trace with a concrete input,
  not a hypothetical.
- A fix lands on the PR branch, never on `staging`; the PR owner merges.
- The review changes nothing in another session's worktree.
