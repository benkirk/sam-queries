# Companion pages

Interactive pages that go deeper than a slide can. Each is published as a public claude.ai
Artifact and linked from a slide. **The file here is canonical:** edit it, then republish. Never
edit the live page and leave this copy behind.

| File | Live page | Linked from |
|---|---|---|
| `pipeline.html` | <https://claude.ai/artifact/Tp4XaBDH7BmvUbPT6UUbtS> | Part 5, "Walk the pipeline yourself" |

## `pipeline.html`: from a pull request to a running pod

**What it is:**
- Six stops (PR, merge, build, registry, pin, Argo to pods), with a production / samuel-dev
  toggle.
- Each stop says what runs there, what starts it, and what keeps it safe.
- Each stop links its workflow YAML and docs on sam-queries `main`.

**How it is built:**
- One hand-written file: its CSS and script are inline. There is no build step, no framework and
  no data feed.
- The facts are typed in by hand, as of 2026-10-01, from the sam-queries docs the Part 5 notes
  cite: `docs/CIRRUS_PUBLISHING.md`, `JOBS_IMAGE.md`, `CI_PARALLEL_SPLIT.md`,
  `K8S_DEV_ENVIRONMENT.md` and `helm/values*.yaml`.
- The content is the `T` (per-target values) and `STOPS` objects at the top of the script.
- **Facts that go stale:** the example image tag (`sha-1b624fc`), the test count, the timings.
  The source links track `main` and stay current.
- **Fonts:** Poppins (as in the deck) and IBM Plex, from Google Fonts.
- **Theme:** light and dark follow the viewer's setting. Below 760 px wide, the stops stack into
  a column.
- **Deep links:** `#<stop>-<target>`, for example `#pin-prod` or `#argo-dev`.

**Why the file has no `<!doctype>`, `<html>` or `<body>`:** the Artifact host wraps the page in
its own document skeleton at publish time, so the source is the page's contents only. Opened
straight from disk it still renders, without a doctype; the screenshot recipe below adds one.

**Publishing.** Ask Claude Code to publish `companion/pipeline.html` to the artifact URL above
(the Artifact tool's `publish`, with `url` set). That keeps the URL, so the slide's link holds.
Publishing without the URL creates a new artifact.

**Refreshing the slide screenshot** (`images/companion-pipeline.png`). Light theme, 1600×900 at
2×, on the Pin stop:

```bash
{ echo '<!doctype html><html lang="en" data-theme="light"><head><meta charset="utf-8">'
  echo '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
  cat companion/pipeline.html; echo '</html>'; } > /tmp/shot.html
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu \
  --hide-scrollbars --force-device-scale-factor=2 --window-size=1600,900 \
  --virtual-time-budget=5000 --screenshot=images/companion-pipeline.png \
  "file:///tmp/shot.html#pin-prod"
```

**Before republishing,** grep the file the same way as the deck sources. The repo is public, so
there must be no IPs, no secret paths and no skip-ci tokens. HPC lane mechanics stay out too:
Part 5 keeps HPC deployment to one broad line.
