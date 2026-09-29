# Jobs image — drop the collectors container; a `jobs` target if one is ever wanted

**Status:** finding, 2026-09-28. No code yet; not urgent.
**See also:** `containers/ncar-hpc-deploy/README.md` (what runs the HPC-side jobs today),
`docs/nrit-review-2026-05/05_collector.md` (A2 and O6 asked the same question).
**Decision to make:** keep the collectors image and make it fit the cron jobs, or drop it.
Recommendation: **drop it**. If a jobs-only image is ever wanted, add a target to the webapp
Dockerfile rather than revive this one.

## 1. What exists

`containers/collectors/Dockerfile` is `python:3` plus `collectors/` copied to `/collectors`,
three pip dependencies (`requests`, `python-dotenv`, `PyYAML`) and
`CMD /collectors/run_collectors.sh`. The build matrix in
`.github/workflows/build-images-cirrus-deploy.yaml` builds it by default on every push to
`main` and `staging`. Nothing pulls it: no chart, compose file or cron line references
the image.

The HPC-side jobs (collectors, `jobhist-sync`, `accounting-comp`, `accounting-disk`) run from
the **webapp** image through `ncar-hpc-deploy` under apptainer on casper and derecho.

## 2. Could the collectors image run those jobs?

No, not without becoming the webapp image:

| the jobs need | collectors image |
|---|---|
| `collector.py` + its three dependencies | yes, but at `/collectors`; the jobs expect `/code/collectors` |
| the `sam` package, `sam-admin` / `sam-search` | no |
| `hpc-usage-queries[postgres]` (`jobhist-sync`; `accounting-comp` reads job_history) | no |
| MySQL / Postgres drivers | no |
| `containers/ncar-hpc-deploy/{jobs,etc,smoke}` under `/code` | no |
| `GIT_SHA` / `BUILD_DATE` (`image_facts`, `update-history`) | no |

Adding those is the webapp Dockerfile's `base` stage under another name, on the same
`python:3` base, so it would not be smaller.

## 3. A `jobs` image, if one is wanted

"The webapp without gunicorn" is cheapest as a target in `containers/webapp/Dockerfile`:
`FROM base AS jobs` with no `CMD`. It shares every cached layer, so it costs almost nothing
in CI, and `ncar-hpc-deploy` would point `NCAR_HPC_DEPLOY_IMAGE_REPO` at it.

What that buys is small:
- **Not a smaller image.** `flask*` and `gunicorn` are core `dependencies` of the single
  `sam` distribution (`pyproject.toml`), so installing `sam` installs them. Dropping them
  needs a dependency split first (e.g. a `webapp` extra). The smoke step imports `webapp`
  (`smoke/smoke.sh`), so check what the CLI imports before splitting.
- **Not fewer pulls.** The payload is the sam code itself, so a jobs digest changes on nearly
  every merge, as the webapp digest does (about 470 MB pulled and smoked per change on the dev lane).
- **Not the runtime user.** Apptainer runs as the calling account, so the `production`
  stage's `USER 1000` and `CMD` already have no effect under `ncar-hpc-deploy`.

A real reason would be a smaller attack surface (no test extras, no private
`hpc-scheduling-tools` plugin) or a Python/base pin that differs from the webapp's.

## 4. Dropping the collectors image (the follow-up PR)

- Delete `containers/collectors/` (Dockerfile, Makefile).
- Remove `collectors` from the build matrix, its defaults and the dispatch help text in
  `.github/workflows/build-images-cirrus-deploy.yaml`, which stops a build on every push.
- Prune the GHCR package `sam-queries/collectors` by hand or with the `clean-ghcr`
  workflow (needs a token with `read:packages`).
- Answer NRIT A2 in `docs/nrit-review-2026-05/05_collector.md`: production is `ncar-hpc-deploy`
  on the webapp image.
- Keep `collectors/run_collectors.sh`. `collectors/README.md` documents it as the standalone
  and systemd runner, and `collectors/cron_scripts/run_ncar_collectors.sh` calls it.
  `collectors/cron_scripts/` goes when the host-checkout collectors cron that still posts
  to prod is retired (the prod-lane cutover).
