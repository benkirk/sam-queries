# One `samuel` image — no separate jobs image

**Status:** decided 2026-09-29. Track A (drop the collectors image) lands with this doc;
B–D are follow-up PRs, one each.
**See also:** `containers/ncar-hpc-deploy/README.md` (what runs the HPC-side jobs),
`docs/nrit-review-2026-05/05_collector.md` (A2, O6; action register P1-53, P2-67, P2-70, Q29).

## 1. Decision

One image serves the webapp, the k8s task CronJob (`helm/templates/cronjob-tasks.yaml`) and
the HPC cron lanes (`ncar-hpc-deploy` under apptainer on casper and derecho). There is **no
jobs-only image**: a jobs target without gunicorn is not meaningfully smaller. The size is
in the base image, so the fix is a slim runtime stage for the one image.

The image and its surfaces are renamed `webapp` → `samuel` (GHCR package, `containers/`
directory, compose services). The old GHCR packages are removed only after the rename is
fully deployed.

## 2. Where the size is

Measured on the production image (arm64, 2.07 GB uncompressed):

| component | size |
|---|---|
| `python:3` base: full Debian trixie + buildpack-deps (gcc, headers, git, ssh) | ~1.2 GB; 415 MB compressed (amd64) |
| site-packages | 326 MB, of which matplotlib + numpy + fontTools + PIL ≈ 150 MB |
| gunicorn | 3 MB |
| flask + werkzeug + wtforms + jinja2 | ~8 MB |
| `/code` | 30 MB |
| `python:3-slim`, for comparison | 43 MB compressed |

Dropping flask and gunicorn would need a dependency split (`sam` imports flask only lazily,
but `smoke/smoke.sh` imports `webapp`) to save under 1%. The base image matters more on HPC
than on k8s: `ncar-hpc-deploy update` pulls with `apptainer pull --disable-cache`, so every
digest change downloads and squashes the whole base again, per lane.

Build time is the other cost. A staging build takes about 8.3 min, of which 371 s is the
arm64 `pip install` layer under QEMU (28.5 s on amd64). It reruns on every commit because
`COPY . .` precedes it.

## 3. Sequence

**A. Drop the collectors image.** It was `python:3` + `collectors/` + three dependencies,
built on every push, and pulled by nothing. It could not run the HPC jobs without becoming
the webapp image's `base` stage (they need `sam`, `hpc-usage-queries`, the DB drivers and
`containers/ncar-hpc-deploy/` under `/code`). This answers NRIT A2/Q29: production is
`ncar-hpc-deploy` on the one image, and the collectors run there in spool mode, needing
neither ssh nor a PBS client. `collectors/run_collectors.sh` stays (the standalone/systemd
runner, and `collectors/cron_scripts/` calls it); `collectors/cron_scripts/` goes at the
prod-lane cutover. The GHCR package `sam-queries/collectors` is pruned by hand.

**B. Rename `webapp` → `samuel`.** GHCR `samuel` / `samuel-dev`, `containers/samuel/`,
compose services `samuel` / `samuel-dev`, the helm pin and its render tests, the HPC lane's
default repo and SIF prefix. Out of scope: the `src/webapp` package and the helm
`.Values.webapp` keys and k8s resource names. A new GHCR package is private by default and
both k8s and GLADE pull anonymously, so it must be made public before the first deploy.

**C. Slim runtime, dependencies first.** Build stage on `python:<minor>` installs the
dependencies from `pyproject.toml` alone (cached across commits), then the plugins. Runtime
stage on `python:<minor>-slim` copies site-packages, then `COPY . /code` and
`pip install --no-deps -e /code`, so a commit changes only the top ~30 MB. Traps:
- `tzdata` must be installed. Without it `TZ=America/Denver` silently falls back to UTC and
  every naive-Mountain date shifts; `smoke.sh base` asserts it.
- The editable install belongs in the runtime stage; in the build stage its finder file
  would change the site-packages layer on every commit.
- CI runs pytest in this image, and `test_docs.py` shells out to `git`, which slim lacks:
  a missing binary must skip, not error.
- `sam-admin accounting --verify-host` needs `ssh`; no job uses it.

**D. Native multi-arch.** Build each platform on its own runner (`ubuntu-24.04-arm` is free
for public repos), push by digest, and merge with `docker buildx imagetools create`. Resolve
the plugin refs and the build date once in `setup`, so both platforms build the same inputs.

**Later, maybe.** With C, a commit changes ~30 MB of layers, but `--disable-cache` still pulls
the whole image. A per-lane `APPTAINER_CACHEDIR` would fetch only changed layers; find out
why `--disable-cache` was chosen first.
