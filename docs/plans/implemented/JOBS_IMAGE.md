# One `samuel` image — no separate jobs image

**Status:** implemented 2026-09-29. Prod, samuel-dev and the HPC dev lane run `samuel`.
**PRs:** #660 (drop collectors), #664 (rename), #665 (slim runtime), #666 (native multi-arch),
#668 (auto staging→main PR), #669 (a Postgres test-order flake it exposed), #670 (GHCR prune fix),
#672 (prune defaults).
**See also:** `containers/ncar-hpc-deploy/README.md` (what runs the HPC-side jobs),
`docs/CIRRUS_PUBLISHING.md` (the build workflow), `docs/nrit-review-2026-05/05_collector.md`
(A2, O6; action register P1-53, P2-67, P2-70, Q29).

## 1. Decision

One image serves the webapp, the k8s task CronJob (`helm/templates/cronjob-tasks.yaml`) and
the HPC cron lanes (`ncar-hpc-deploy` under apptainer on casper and derecho). There is **no
jobs-only image**: without gunicorn it is not meaningfully smaller. The size was the base
image, so the fix was a slim runtime stage for the one image, which every consumer shares.
It is named for the system, `samuel`, not for one of its consumers.

## 2. Where the size was

Measured on the pre-change production image (arm64, 2.07 GB uncompressed):

| component | size |
|---|---|
| `python:3` base: full Debian trixie + buildpack-deps (gcc, headers, git, ssh) | ~1.2 GB; 415 MB compressed (amd64) |
| site-packages | 326 MB, of which matplotlib + numpy + fontTools + PIL ≈ 150 MB |
| gunicorn | 3 MB |
| flask + werkzeug + wtforms + jinja2 | ~8 MB |
| `/code` | 30 MB |
| `python:3-slim`, for comparison | 43 MB compressed |

Dropping flask and gunicorn needs a dependency split to save under 1%. The base image
matters most on HPC: `ncar-hpc-deploy update` pulls with `apptainer pull --disable-cache`,
so every digest change downloads and squashes the whole image again, per lane.

## 3. What shipped

**Collectors image dropped (#660).** It was `python:3` + `collectors/` + three dependencies,
built on every push and pulled by nothing. The collectors run on the one image under
`ncar-hpc-deploy`, in spool mode (no ssh, no PBS client). That answers NRIT A2/Q29.
`collectors/run_collectors.sh` stays as the standalone/systemd runner; `collectors/cron_scripts/`
goes at the prod-lane cutover.

**Rename `webapp` → `samuel` (#664).** GHCR `sam-queries/samuel`, `containers/samuel/`,
compose services `samuel` / `samuel-dev`, the helm pin and its render tests, the HPC lane's
default repo (`libexec/lane.sh`) and SIF prefix (`samuel-<tag>-<digest12>.sif`). Unchanged:
the `src/webapp` package and the helm `.Values.webapp` keys and k8s resource names.
- The new package was publicly pullable on its first push (it follows the public repo), so
  no visibility change was needed. The dispatch-only `samuel-dev` image was never published.
- The lane's repo default lives in the GLADE host checkout, not the image. A lane switches
  only when csgteam pulls that checkout.
- `samuel_built` must match the image name exactly: `grep -w samuel` also accepts `samuel-dev`.

**Slim runtime, dependencies first (#665).** A build stage on `python:<minor>` installs the
dependency list read from `pyproject.toml` before any source is copied, then the plugins.
The runtime stage on `python:<minor>-slim` takes site-packages and console scripts, then
`COPY . /code` and `pip install --no-deps -e /code`. Traps:
- `tzdata` is installed explicitly: without it `TZ=America/Denver` silently resolves to UTC
  and every naive-Mountain date shifts. `smoke.sh base` asserts it.
- The editable install runs in the runtime stage; in the build stage its finder file would
  change the ~300 MB site-packages layer on every commit.
- `make` stays (CI runs `make clone-pg-test` in the image). `git` does not: `test_docs.py`
  skips when the binary is absent. `ssh` is absent; only `sam-admin accounting --verify-host`
  needs it.
- The ARG is `PY_MINOR`: the base images set `PYTHON_VERSION` as an ENV, which shadows an ARG.

**Native multi-arch (#666).** `setup` emits image × platform, amd64 on `ubuntu-24.04` and arm64
on `ubuntu-24.04-arm`; each builds natively and pushes by digest; `merge` joins them with
`docker buildx imagetools create`. The plugin SHAs and build date are resolved once in
`setup`, so both platforms build the same inputs.

## 4. Results

| | before | after |
|---|---|---|
| image, uncompressed (arm64) | 2.07 GB | 679 MB |
| image, compressed (amd64 / arm64) | 518 / 507 MB | 149 / 149 MB |
| HPC lane SIF | 492 MB | 145 MB |
| build wall time | 8.0 min (371 s of it arm64 pip under QEMU) | ~3 min |
| layers a source-only commit rebuilds | the whole pip layer | `COPY . .` (30 MB) + editable install (5 MB) |

## 5. Found along the way

- **The GHCR prune broke multi-arch images (#670).** A multi-arch tag is an index whose
  per-platform and attestation manifests are untagged versions, and the prune deleted every
  untagged version. The 2026-09-27 run left 12 of 47 `webapp` tags and all 15 `mysql` tags
  unpullable. It now deletes an untagged version only when no kept index references it, keeps
  everything when a kept manifest cannot be read, and dry-runs on any ref but `main`.
- **Promotion PRs open themselves (#668).** On every staging push, `open-staging-promotion.yaml`
  opens or refreshes the `staging → main` PR. It uses the cirrus-deploy App token, because a PR
  opened with `GITHUB_TOKEN` triggers no CI. The App needed Pull requests: write, and the new
  permission only applies once the installation owner accepts it.
- **`User.institutions` had no `order_by` (#669).** MySQL returns PK order and Postgres heap
  order, so a test expecting the older row first failed under xdist on Postgres.
- GHCR `collectors` and `webapp` packages deleted; `mysql` and `samuel` remain.

## 6. Open

- Prod HPC lane: not installed. It pulls `samuel:main` with no change.
- `--disable-cache` makes every lane update pull the whole image. A per-lane
  `APPTAINER_CACHEDIR` would fetch only changed layers; find out why `--disable-cache` was
  chosen before changing it.
