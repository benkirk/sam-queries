# samuel-dev: a routable k8s dev deployment

**Status: IN PROGRESS.** Written 2026-09-12 as the implementation handoff; the repo
side (§4) landed the same day as eight commits on `k8s_dev_plan` → `staging`, each
leaving the prod render byte-identical. Since 2026-09-23 Argo CD application
`sam-query-dev` (AppProject `csg`) deploys samuel-dev into its own namespace,
`sam-queries-dev`, and `make deploy-dev` is retired; kubectl RBAC there landed
2026-09-26. Open: the separate Entra registration — see §10. This is Stage 5 of `docs/plans/implemented/POSTGRES_MIGRATION.md`:
a second install of the `samuel` chart on nwc1, serving `samuel-dev.k8s.ucar.edu`
from the CNPG `sam_dev` Postgres copy, deployable from any branch without touching
production.

Prior art this doc leans on rather than repeats: `docs/CIRRUS_PUBLISHING.md` (how
`cirrus` is pinned and locked), `docs/README-k8s.md` (the chart's runbook),
`docs/AUTHENTICATION.md` (OIDC per environment), `helm/README.md`, and the memory
notes on the tasks CronJob inheriting nothing from the webapp.

## 1. Why

Local compose covers one developer. A growing team needs a shared, internet-routable
instance that runs feature branches with real OIDC, real plugin databases, and the
real ingress, without any path to production data or production mail. The dual-ops
work (#549–#551) made SAM run on Postgres with an empty expected-failures list; the
`sam_dev` database on `csg-postgres` already exists and is rebuilt by
`make -C containers/sam-sql-dev clone clone-pg`. What was missing was the chart overlay, a second git pointer for
the GitOps controller, the safety gates, and the runbook.

## 2. Decisions (Ben, 2026-09-12)

| Question | Decision | Why |
|---|---|---|
| Same chart or a second app | **Same chart, a `values-dev.yaml` overlay** | Every object name already comes from values (`webapp.name`, `cache.name`, `tasks.name`), never `.Release.Name`. A second chart would copy ten templates to change six values. |
| Namespace | **Own `sam-queries-dev`** (bootstrapped in `sam-queries`; CIRRUS moved it 2026-09-23 when Argo adopted it) | Ben's RBAC covered every kind the chart creates in `sam-queries`, so the laptop bootstrap went there. The platform team provisioned `sam-queries-dev` with the Argo app; kubectl RBAC there followed on 2026-09-26. |
| Deploy path | **Bootstrap, then GitOps** (done) | CI grows a `target` and pins a locked `cirrus-dev` branch. A laptop `make deploy-dev` bootstrapped it; Argo Application `sam-query-dev` deploys it since 2026-09-23 and the make target is gone. Same value files both ways. |
| What reaches dev | **push to `staging` → dev; `gh workflow run --ref <branch>` → dev by default** | Staging regains a real deploy target. Prod only from push to `main`, `v*` tags, or an explicit `target=prod`. |
| SAM database | CNPG `sam_dev` on Postgres | The Stage 5 premise. Postgres-dev / MySQL-prod skew is accepted. |
| system_status | **New `system_status_dev` on the same CNPG cluster, seeded at each refresh** | Same host, so cheap; gives dev its own `task_run` ledger, so the dev dispatcher never settles a prod slot. |
| jobhistory, fs-scans | `csg-postgres-ro`, unchanged | Read-only from SAM; too heavy to duplicate. |
| Mail | **Off** (`NOTIFY_ENABLED=0`, mail tasks disabled) | The relay reaches any internet address. The redirect valve stays a one-line opt-in. |
| Entra | New app registration (`csg/sam-dev-oidc`) | Interim: add the dev reply URL to the prod registration and copy its client id/secret into the dev path, with a fresh `FLASK_SECRET_KEY` either way. |
| JupyterHub token | **Inherit prod `csg/jh-api-token`** | Read-only token; the JupyterHub status card works on dev with no new OpenBao entry. Pinned by `test-dev-render.sh`. |

## 3. Verified facts (2026-09-12)

Cluster, from `kubectl` as `oidc:benkirk@ucar.edu` on `nwc1`:

- Visible namespaces: `sam-queries`, `pg-testing`. Ben **can** create Deployments,
  Services, Ingresses, ExternalSecrets, Certificates, CronJobs, NetworkPolicies in
  `sam-queries`. Ben **cannot** create namespaces, Argo Applications, SecretStores in
  another namespace, or CNPG `Cluster`/`Database` objects.
- Prod is applied by Argo CD Application **`sam-query`** (annotation
  `argocd.argoproj.io/tracking-id: sam-query:apps/Deployment:sam-queries/samuel`);
  there is no helm release object. Namespace is Capsule-managed (`capsule.clastix.io/managed-by: csg`).
- `SecretStore csg-ro` exists in `sam-queries` (Valid, ReadWrite).
- Prod ingress: class `nginx-external`, LB `128.117.41.126`, one multi-SAN cert
  `incommon-cert-samuel` (Ready), ClusterIssuer `incommon`.
- `csg-postgres` lives in `pg-testing`: Services `csg-postgres-rw`/`-ro`/`-r` plus two
  LoadBalancers, `csg-postgres-service` (`128.117.217.105`, = `csg-postgres.k8s.ucar.edu`)
  and `csg-postgres-ro-service` (`128.117.217.107`).
- DNS: `samuel-dev.k8s.ucar.edu` does not resolve yet and there is no wildcard. Ben's
  understanding is that `*.k8s.ucar.edu` names are assigned automatically from the
  Ingress hosts (consistent with `samuel.k8s.ucar.edu` → the LB with no DNS annotation
  on the Ingress). Treat as automatic; verify after the first apply (§7).

Repo:

- `helm/` has no `_helpers.tpl`, no subcharts, no HPA. Names: `webapp.name` →
  Deployment/Service/Ingress/PDB/ConfigMap and all seven `<name>-*-credentials[-esos]`
  Secrets/ExternalSecrets; `cache.name` → Redis Deployment/Service/NetworkPolicy;
  `tasks.name` → CronJob; `webapp.group` label everywhere; selectors are exact-match
  `app:` labels.
- `helm/templates/cronjob-tasks.yaml` renders `.Values.tasks.env` plus a hand-listed set
  copied from `webapp.env`: `NOTIFY_*` by prefix, five `MAIL_*` keys by hand (not a
  prefix loop — one would reorder prod bytes), `STATUS_DB_DRIVER/SERVER`,
  `SAM_DB_DRIVER/SERVER/REQUIRE_SSL`, `CACHE_REDIS_URL`, `XRAS_*`. Before §4.1 **it
  never saw `SAM_DB_NAME`, `SAM_DB_PORT`, or `STATUS_DB_NAME`**, and the chart set none
  of them (app defaults: `sam`, `system_status`). A dev overlay without the template
  fix would have run the dev tasks pod, including the nightly
  `cleanup_status_snapshots` DELETEs, against **prod** `system_status`.
- Prod render baseline: `helm template samuel helm -f helm/values.yaml -n sam-queries | md5`
  = `311be05e3e320cb9aa7c613eb6512fc7` (helm 4.1.3, commit ae9452ca). Prod's
  `webapp.env` carries exactly five `SAM_DB_*`/`STATUS_DB_*` keys.
- CI: `.github/workflows/build-images-cirrus-deploy.yaml` triggers on push to `main`,
  `v*` tags, and `workflow_dispatch` (only input: `images`). `update-helm` checks out
  `github.ref`, seds the one `image:` line in `helm/values.yaml`, and force-pushes
  `cirrus` as GitHub App `cirrus-benkirk-deployer`. **So today every dispatch deploys
  prod.** Ruleset `16783890` ("Lock cirrus to deploy workflow") locks `refs/heads/cirrus`
  with the App as sole bypass actor. `sync-staging-to-main.yaml` force-pushes `staging`
  with `GITHUB_TOKEN`, which triggers no workflow.
- Auth: `ProductionConfig.validate()` refuses to start unless `AUTH_PROVIDER=oidc`,
  `DISABLE_AUTH!=1`, and `OIDC_CLIENT_ID/SECRET/ISSUER` + `FLASK_SECRET_KEY` are set.
  `FLASK_CONFIG=production` is therefore the interlock that makes a routable host safe;
  `values-local.yaml`'s `development` + `DISABLE_AUTH=1` must never reach the cluster.
  `OIDC_REDIRECT_URI` is deliberately unset; the callback derives per host via ProxyFix.
- `sam-admin cache --refresh` posts to `SAM_API_BASE`, default `https://samuel.k8s.ucar.edu`.
- `containers/sam-sql-dev/load_postgres.py::swap()` terminates its **own role's**
  sessions on `sam_dev` before the rename and refuses if any **other** role holds a
  session. So pods that connect as the loader's role are evicted by a refresh and
  reconnect through `pool_pre_ping`; no loader change is needed.
- Alembic for system_status: `make migrate-status-up` / `migrate-status-current`
  (`migrations/system_status/`), driven by `STATUS_DB_*` env — **but the make targets
  `source etc/config_env.sh`, which `set -a; source .env` and clobbers any `STATUS_DB_*`
  passed on the command line.** The seed (§4.7) therefore carries the schema and
  `alembic_version` in the dump instead; `ALEMBIC_SYSTEM_STATUS_URL=... alembic current`
  is the way to check a non-`.env` database.
- `make clone` / `make clone-pg` are targets of `containers/sam-sql-dev/Makefile`, not
  the top level; `SAM_DEV_API_PASS` was a name this doc invented (the CLI reads
  `SAM_API_USER` / `SAM_API_PASS` / `SAM_API_BASE`), so `refresh-dev` maps it.
- API keys: `scripts/gen_api_key.py` prints a bcrypt hash for `API_KEYS_<USER>`.
- `scripts/lib/cirrus_common.sh` hardcoded prod names; `scripts/cirrus_healthcheck.sh`
  adopted the **first** helm release it found in the namespace when `helm status samuel`
  failed (it always fails, prod is Argo-applied) — but only `$RELEASE`, so it would
  have reported dev's release name while probing prod's objects. Its ExternalSecret
  list was six literal names (the XRAS one unchecked). The watch DB host lived in
  `cirrus_watch.sh`, not the lib, and its TCP preflight `exit 0`s the whole tick.
  All fixed in §4.5.
- `docs/README-k8s.md` already uses `samuel-dev` as the **local Docker Desktop**
  namespace name.
- `tests/unit/test_docs.py` treats `docs/plans/` as records (exempt from the path,
  spelling, phrasing, and length gates) but checks every other doc's back-ticked
  paths, so files and the docs that cite them land in the same PR.

## 4. Implementation (one PR, in this order)

As built, 2026-09-12 — one commit per subsection: 4.1 chart; 4.3's `lib/assert.sh`
alone (so the extraction is reviewable with unchanged assertions); 4.2 + the rest of
4.3 (22 rejections proven, `jupyterhubCredentials` pinned); 4.4 + `CIRRUS_PUBLISHING.md`;
4.5; 4.6 + `helm/README.md`; 4.7; 4.8. Deviations from the text below are noted in
place.

### 4.1 Chart: close the CronJob drift trap first

`helm/templates/cronjob-tasks.yaml`, after the `SAM_DB_REQUIRE_SSL` entry (~line 169)
and before the `CACHE_REDIS_URL` block, forward the rest by prefix, the same shape as
the `NOTIFY_*` loop above it. Helm comment, not `#` (a `#` comment renders into the
output the tests grep):

```yaml
              {{- /* Every other non-empty SAM_DB_* / STATUS_DB_* key in webapp.env
                     (NAME, PORT, ...) forwards by prefix, like NOTIFY_* above.
                     WARNING: without this an overlay that points the webapp at a
                     different database leaves the task pod on the app defaults
                     (sam / system_status), i.e. on production. */}}
              {{- range $key, $value := .Values.webapp.env }}
              {{- if and (or (hasPrefix "SAM_DB_" $key) (hasPrefix "STATUS_DB_" $key)) $value
                         (not (has $key (list "SAM_DB_DRIVER" "SAM_DB_SERVER" "SAM_DB_REQUIRE_SSL" "STATUS_DB_DRIVER" "STATUS_DB_SERVER"))) }}
              - name: {{ $key }}
                value: {{ $value | quote }}
              {{- end }}
              {{- end }}
```

Prod sets none of the extra keys, so prod bytes do not change. No `_helpers.tpl`: a
helper consumed by the Deployment would reorder its alphabetical `range` and change
prod bytes; a helper consumed only by the CronJob is indirection with one caller.

Also in `helm/values.yaml`: the comment near line 346 ("inherits NOTHING") becomes
"hand-listed keys plus `NOTIFY_*` / `SAM_DB_*` / `STATUS_DB_*` by prefix"; the comment
near line 441 names `csg/sam-dev-oidc` and `values-dev.yaml`.

**Gate:** render prod before this edit to a scratch file; after this and every later
step, `helm template samuel helm -f helm/values.yaml -n sam-queries | diff -u <before> -`
must be empty.

### 4.2 The overlay: `helm/values-dev.yaml (new)`

Never an `image:` line: CI pins the image in `values.yaml` on the `cirrus-dev` branch,
and the sed would rewrite a second line too.

```yaml
replicaCount: 1
podDisruptionBudget: {enabled: false}      # minAvailable 1 on 1 replica blocks drains
cache:
  name: samuel-dev-redis
  maxmemoryMB: 64
  requests: {memory: 32Mi, cpu: 25m}
  limits:   {memory: 128Mi, cpu: 250m}
webapp:
  name: samuel-dev
  group: samuel-dev
  tls: {fqdn: samuel-dev.k8s.ucar.edu, extraHosts: [], secretName: incommon-cert-samuel-dev}
  container:
    requests: {memory: 1024M, cpu: 1}
    limits:   {memory: 4G, cpu: 4}
  env:
    FLASK_CONFIG: "production"             # ProductionConfig.validate() is the auth interlock
    AUTH_PROVIDER: "oidc"
    DISABLE_AUTH: "0"
    SAM_DB_DRIVER: "postgresql"
    SAM_DB_SERVER: "csg-postgres.k8s.ucar.edu"
    SAM_DB_PORT: "5432"
    SAM_DB_NAME: "sam_dev"
    SAM_DB_REQUIRE_SSL: "true"
    STATUS_DB_NAME: "system_status_dev"
    NOTIFY_ENABLED: "0"
    NOTIFY_XRAS_CC: ""
    NOTIFY_XRAS_REPLY_TO: ""
    XRAS_OUTGOING_ENABLED: "0"
    XRAS_WRITE_ENABLED: "0"
    XRAS_ACTIONS_CAPTURE_ONLY: "1"
    API_KEYS_COLLECTOR: "<dev bcrypt hash>"   # scripts/gen_api_key.py; plaintext in .env as SAM_DEV_API_PASS
    SAM_API_BASE: "https://samuel-dev.k8s.ucar.edu"   # sam-admin cache --refresh defaults to prod
  samDbCredentials: {secretPath: csg/sam-dev-pg}
  oidcCredentials:  {secretPath: csg/sam-dev-oidc}
  xrasApiCredentials: {enabled: false}     # lever off; do not hold the prod key
tasks:
  name: samuel-dev-tasks
  env:
    SAM_API_BASE: "https://samuel-dev.k8s.ucar.edu"
    SAM_TASKS_DISABLED: "expiration_notices,xras_notices,xras_sweep"
```

Inherited on purpose: `ingress.visibility: external`, the rate limits, `TZ`,
`READ_MODEL_ENABLED`, `dbCredentials`/`jhDbCredentials`/`fsDbCredentials`
(`csg/pg-appuser`), `JOB_HISTORY_*`/`FS_SCAN_*`, `STATUS_DB_DRIVER/SERVER`,
`FLASK_ADMIN_ENABLED=0` (a later knob, behind OIDC), `OIDC_REDIRECT_URI` absent.
`xras_sweep` is disabled because `test-cronjob-render.sh` treats the sweep and the
outgoing lever as one decision. Keep the comment budget: the constraint and the legal
value, nothing more.

### 4.3 Render tests

- `helm/tests/lib/assert.sh (new)`, in a subdirectory so the `helm/tests/*.sh` glob in
  the Makefile and `ci-staging.yaml` skips it: `red`/`green`/`assert_contains`/
  `assert_not_contains`, the helm presence check, and
  `render <prod|dev|local> [helm args]` that strips `#` comments. The two existing
  scripts `source` it; their assertions do not change.
- `helm/tests/test-dev-render.sh (new)`, every check per-manifest
  (`-s templates/deployment.yaml`, `-s templates/cronjob-tasks.yaml`):
  1. `FLASK_CONFIG` = `production`, `AUTH_PROVIDER` = `oidc`, `DISABLE_AUTH` = `0`;
     no `OIDC_REDIRECT_URI`; no `auth/oidc/callback` literal.
  2. `NOTIFY_ENABLED` = `0` **or** a non-empty `NOTIFY_REDIRECT_TO`, on both manifests
     (so flipping to the redirect valve later does not need a test edit).
  3. `XRAS_OUTGOING_ENABLED` = `0` on both; `XRAS_WRITE_ENABLED` = `0`;
     `XRAS_ACTIONS_CAPTURE_ONLY` = `1`; no `XRAS_API_KEY` anywhere; no
     `samuel-dev-xras-api-credentials` ExternalSecret.
  4. `API_KEYS_COLLECTOR` differs from the hash grepped out of `values.yaml`.
  5. `SAM_API_BASE` = the dev URL on both manifests.
  6. `SAM_TASKS_DISABLED` identical on both; contains `expiration_notices`,
     `xras_notices`, and `xras_sweep` unless `XRAS_OUTGOING_ENABLED` is `1`.
  7. `replicas: 1`; no `PodDisruptionBudget` rendered.
  8. Each of `SAM_DB_{DRIVER,SERVER,PORT,NAME,REQUIRE_SSL}` and
     `STATUS_DB_{DRIVER,SERVER,NAME}` present on **both** manifests with identical
     values; pinned `sam_dev`, `system_status_dev`, `postgresql`. Then set-equality of
     every `SAM_DB_*`/`STATUS_DB_*` name between the two manifests, which catches a
     future key the exclusion list swallows.
  9. `samuel-dev-sam-db-credentials` referenced by both; ExternalSecret `remoteRef.key`
     = `csg/sam-dev-pg` and `csg/sam-dev-oidc`; `csg/sam-writeuser` and `csg/sam-oidc`
     absent from the whole dev render.
  10. `CACHE_REDIS_URL` on both contains `samuel-dev-redis.` (the namespace comes
      from `.Release.Namespace`, `sam-queries-dev` under Argo);
      NetworkPolicy `samuel-dev-redis-allow-webapp` admits `app: samuel-dev` and
      `app: samuel-dev-tasks`.
  11. Exactly two `image: ghcr.io/.../webapp:` refs in the dev render and zero `image:`
      lines in the overlay file.
  12. Collision: the `(kind, metadata.name)` sets of the prod and dev renders are
      disjoint; every dev name starts with `samuel-dev`; `app:` label values, ingress
      hosts, and `secretName` values pairwise disjoint; dev hosts = exactly
      `samuel-dev.k8s.ucar.edu`; `group: samuel` absent from dev.
  13. Prod proxy, permanent: the prod CronJob carries exactly the five original
      `SAM_DB_*`/`STATUS_DB_*` value keys, so the forward loop stays a no-op for prod.
  - Negative loop: factor the checks into `check_dev "$@"` run in a subshell; the
    positive path calls it once, then each of these `--set` overrides must exit 1:
    `webapp.env.DISABLE_AUTH=1`, `webapp.env.FLASK_CONFIG=development`,
    `webapp.env.AUTH_PROVIDER=stub`, `webapp.env.NOTIFY_ENABLED=1`,
    `webapp.env.XRAS_OUTGOING_ENABLED=1`, `webapp.env.XRAS_WRITE_ENABLED=1`,
    `webapp.env.XRAS_ACTIONS_CAPTURE_ONLY=0`, `webapp.env.SAM_DB_NAME=sam`,
    `webapp.env.SAM_DB_NAME=null`, `webapp.env.STATUS_DB_NAME=system_status`,
    `webapp.samDbCredentials.secretPath=csg/sam-writeuser`,
    `webapp.oidcCredentials.secretPath=csg/sam-oidc`, `webapp.name=samuel`,
    `webapp.tls.secretName=incommon-cert-samuel`, `webapp.tls.fqdn=samuel.k8s.ucar.edu`,
    `tasks.env.SAM_TASKS_DISABLED=xras_notices`, `webapp.env.API_KEYS_COLLECTOR=<prod hash>`.
    An assertion that has never been seen to fail proves nothing.
- `.github/workflows/ci-staging.yaml`, `helm-render` job: add
  `helm template samuel helm/ -f helm/values.yaml -f helm/values-dev.yaml > /dev/null`.
  `make helm-test` globs the new script by itself.

### 4.4 CI: a `target` for `build-images-cirrus-deploy.yaml`

- Triggers: `push: branches: [main, staging]`, `tags: ['v*']`; `workflow_dispatch` gains
  `target` (`type: choice`, `options: [dev, prod]`, `default: dev`) beside `images`.
- `setup` job, a bash step emitting `target` and `cirrus_branch`: push `main` or a
  `v*` tag → `prod` / `cirrus`; push `staging` → `dev` / `cirrus-dev`; dispatch →
  `inputs.target`, **empty → dev**; anything else → `::error` and exit 1. Explicit
  `prod` from a non-`main` ref is allowed (the triage-week loop uses it) but writes a
  `::warning` and a step-summary line. Prod is never inferred.
- `update-helm`: `concurrency.group: cirrus-branch-push-${{ needs.setup.outputs.target }}`,
  `cancel-in-progress: false`; `git checkout -B "$CIRRUS_BRANCH"` and
  `git push origin "$CIRRUS_BRANCH" --force`; the commit subject gains
  ` target=<t> from <ref-name>` ahead of the existing skip-ci token, so
  `git log -1 origin/cirrus-dev` names the branch that is live on dev.
  The sed pattern, the tag set, and `latest`-only-on-main are unchanged.
- Prod invariance for a push to `main`: identical to today except the concurrency
  group name and the subject suffix. The reviewer checks this by eye; the expected
  output block in `docs/CIRRUS_PUBLISHING.md` is updated for the suffix.
- The empty-input default is what makes the transition safe: `-f target=...` is
  rejected until the workflow file is on the default branch (`main`), so until the
  promotion a feature branch is dispatched bare,
  `gh workflow run "Publish Images and CIRRUS Deploy" --ref <branch>`, and lands on dev.
  The merge to `staging` is itself the first automatic `cirrus-dev` pin. A later
  promotion does not redeploy dev (`GITHUB_TOKEN` pushes trigger nothing; the trees are
  identical anyway).

### 4.5 Ops scripts (must merge before the first helm install)

- `scripts/lib/cirrus_common.sh`: replace the literal name block with one table keyed on
  `SAM_ENV` (`prod` default, `dev`) inside a `cirrus_set_env()` called at source time
  and again by a new `--env` common flag. Dev row: release `samuel-dev`, webapp
  `samuel-dev`, redis `samuel-dev-redis`, tasks `samuel-dev-tasks`, host
  `samuel-dev.k8s.ucar.edu` only, TLS secret `incommon-cert-samuel-dev`,
  `XRAS_ES_EXPECTED=0`, and an empty `DEFAULT_WATCH_DB_HOST` (dev SAM is Postgres and
  XRAS never posts to dev).
- `scripts/cirrus_healthcheck.sh`: derive the ExternalSecret list from `$WEBAPP_NAME`
  (six, plus the XRAS one when `XRAS_ES_EXPECTED`); scope the usage and ExternalSecret
  listings to the release's pods/objects; **delete the adopt-the-first-release
  fallback** (prod: info that Argo applies the chart; dev: warn). As built it also
  fixed the limits map, whose nested jsonpath never yielded a pod name.
- `scripts/cirrus_watch.sh`: Redis pod by `-l app=$REDIS_NAME`; `$TASKS_NAME` in messages;
  one state file per env; with an empty DB host the VPN preflight probes the ingress
  and the XRAS and db-load reads print one-line skips.
- `scripts/README.md`: document `SAM_ENV=dev` / `--env dev`.

### 4.6 Phase-1 laptop deploy: `scripts/deploy_dev.sh (new)` + `make deploy-dev`

**Retired 2026-09-23.** Argo `sam-query-dev` owns samuel-dev; the script and make
target are deleted. Its render gates all live in `helm/tests/test-dev-render.sh`,
which `ci-staging` runs via `make helm-test`. As built, for the record:

The header says TEMPORARY until Argo Application `sam-query-dev` exists. Literals, not
flags: release `samuel-dev`, namespace `sam-queries`, context `nwc1`, source
`origin/cirrus-dev`.

1. Current kube context must be `nwc1`; `git fetch origin cirrus-dev`.
2. Self-retire: abort if `deploy/samuel-dev` already carries an
   `argocd.argoproj.io/tracking-id` annotation.
3. Deploy the **`cirrus-dev` tree, not the working tree**:
   `git archive origin/cirrus-dev helm | tar -x -C "$TMP"`; require the overlay file to
   exist with `name: samuel-dev`, and a `sha-[0-9a-f]{7}` pin in its `values.yaml`.
4. Render-and-refuse before touching the cluster: no `name: samuel`, `samuel-redis`, or
   `samuel-tasks`; no prod hosts; no `csg/sam-oidc`; no `csg/sam-writeuser`; `sam_dev`
   and `system_status_dev` in both manifests; `NOTIFY_ENABLED "0"`;
   `XRAS_ACTIONS_CAPTURE_ONLY "1"`. Any miss exits 2.
5. `helm upgrade --install samuel-dev "$TMP/helm" -f values.yaml -f values-dev.yaml -n sam-queries --atomic --timeout 10m`,
   then `kubectl -n sam-queries rollout status deploy/samuel-dev`.

As built: step 4 also runs the tree's own `helm/tests/test-dev-render.sh`; `--render-only`
stops after step 4; `DEPLOY_DEV_SOURCE_REF` overrides the source for `--render-only`
tests only (a real deploy always uses `origin/cirrus-dev`).

Retirement when Argo adopts the objects: delete the helm release Secrets
(`kubectl -n sam-queries delete secret -l name=samuel-dev,owner=helm`) and remove the
make target.

### 4.7 Refresh and seed tooling

- `load_postgres.py`: print the number of own-role sessions terminated before the
  swap (today it is silent), so a refresh log shows the dev pods being evicted.
- `scripts/seed_status_dev.sh (new)`:
  `pg_dump -Fc --exclude-table-data=task_run ... system_status | pg_restore -d system_status_dev --clean --if-exists --no-owner --no-privileges`,
  then an idempotent re-grant to `pguser`. `task_run` is excluded so dev's ledger is
  its own; prod rows would settle dev's slots. The dump carries the schema and
  `alembic_version`, so there is no Alembic step (§3: the migrate targets clobber
  `STATUS_DB_*`). Runs as the superuser via libpq env: explicit `PG*` win (the
  §6.1 runbook), else `PGHOST`/`PGUSER`/`PGPASSWORD` fall back to `.env`'s
  `PROD_STATUS_DB_*` (which name the postgres superuser), so the make targets need
  no separate `PGPASSWORD`; `PGSSLMODE` defaults to `require`.
- Top-level `Makefile`, `refresh-dev`: `$(MAKE) -C containers/sam-sql-dev clone clone-pg`
  → `scripts/seed_status_dev.sh` → `SAM_API_USER=collector SAM_API_PASS=$SAM_DEV_API_PASS
  SAM_API_BASE=https://samuel-dev.k8s.ucar.edu sam-admin cache --refresh`
  (`SAM_DEV_API_PASS` in `.env`, documented in `.env.example`). Cadence: on demand,
  floor weekly. It runs from a VPN'd laptop because `clone` reads prod MySQL as
  `hpc-reader`, so it is not a cluster CronJob. Do not `make -n refresh-dev`: the
  recipe contains `$(MAKE)`, which GNU make runs even under `-n`.
- Top-level `Makefile`, `sync-dev`: a superset of `refresh-dev` that also loads the
  local compose Postgres (127.0.0.1:5433) for offline work. It runs
  `$(MAKE) -C containers/sam-sql-dev pg-up clone clone-pg-local clone-pg` (bringing
  the local Postgres up first, then one `clone` feeding both the local and CNPG
  loads) → `scripts/seed_status_dev.sh` → the same `sam-admin cache --refresh`.
  Same secrets as `refresh-dev` plus docker for the local bring-up. Same
  `make -n` caveat.

### 4.8 Docs

- `docs/CIRRUS_PUBLISHING.md`: trigger table (push `staging` → dev; dispatch `target`,
  default dev; tags → prod), a two-branch diagram, a "deploy a feature branch to dev"
  recipe, the ruleset section covering both refs, a failure row "dispatch rejects
  `target` → the file is not on `main` yet", and that promotion does not redeploy dev.
- `docs/README-k8s.md`: the ESO table is stale (says `csg/pg-superuser` and
  `csg/sam-readuser`; the chart reads `csg/pg-appuser` and `csg/sam-writeuser`, and the
  jh/fs/xras rows are missing). Fix it, add a dev column, replace the "Future: k8s
  staging" row with `CIRRUS k8s dev (samuel-dev)`, add the `samuel-dev-tasks` row to the
  tasks table, and rename the local Docker Desktop namespace example from `samuel-dev`
  to `samuel-local`.
- `docs/AUTHENTICATION.md` deployment matrix: a `samuel-dev` row
  (`https://samuel-dev.k8s.ucar.edu`, OIDC, `csg/sam-dev-oidc`, callback
  `https://samuel-dev.k8s.ucar.edu/auth/oidc/callback`).
- `helm/README.md`: layout entries for the overlay and the two test files; a dev
  deploy line. `CLAUDE.md` tasks paragraph: "hand-listed set" → "plus `NOTIFY_*` /
  `SAM_DB_*` / `STATUS_DB_*` by prefix". This doc's status line and the Stage 5 line in
  `docs/plans/implemented/POSTGRES_MIGRATION.md` point at each other.

## 5. Outside the repo

| # | Owner | Item | Blocks |
|---|---|---|---|
| 1 | Ben | Ruleset: `gh api -X PUT /repos/benkirk/sam-queries/rulesets/16783890` with `conditions.ref_name.include` = `[refs/heads/cirrus, refs/heads/cirrus-dev]`, same four rules, same single Integration bypass actor. Repeat the negative push test from `docs/CIRRUS_PUBLISHING.md` against `cirrus-dev`. | the first dev-target run (do it before merging the workflow change) |
| 2 | Ben | OpenBao `csg/sam-dev-pg` = `{username, password}` of the `sam_dev` owner role, the same role as `SAM_DEV_PG_USER` in `.env` | pods start |
| 3 | Ben | OpenBao `csg/sam-dev-oidc` = `{client_id, client_secret, issuer, flask_secret_key}`; `flask_secret_key` from `python -c 'import secrets; print(secrets.token_hex(32))'`. Interim fill: prod's three values copied over. | pods start (`ProductionConfig.validate()`) |
| 4 | Ben, superuser creds (`csg/pg-superuser`) | `system_status_dev`: see §6 | status pages only: since #556 `/ready` gates on `sam` and reports a missing `system_status_dev` as `degraded` (200) |
| 5 | UCAR IT (Andrew Tamagni) | Entra app registration "SAM dev": reply URL `https://samuel-dev.k8s.ucar.edu/auth/oidc/callback`, post-logout `https://samuel-dev.k8s.ucar.edu/status/`, scopes `openid email profile`, claims `preferred_username`, `email`, `sub` (checklist in `infrastructure/README.md`). Interim: add both dev URLs to the prod registration. | browser login only |
| 6 | automatic (verify) | DNS `samuel-dev.k8s.ucar.edu` → `128.117.41.126`, expected to appear from the Ingress host. If it has not resolved a few minutes after the first apply, it becomes a platform ticket. | cert, reachability |
| 7 | automatic | cert-manager issues `incommon-cert-samuel-dev` from the Ingress annotation once DNS resolves. | — |
| 8 | CSG platform | Argo Application `sam-query-dev` (§6.3) — done 2026-09-23, namespace `sam-queries-dev`; Ben has Argo UI access and, since 2026-09-26, kubectl RBAC there | Phase 2 only |

**Entra is not a blocker for the first deploy.** With item 3 filled from prod's values
the pods start, health is green, API-key access and anonymous pages work; only browser
login fails (AADSTS50011) until item 5 lands. Item 6 is the real gate.

## 6. Runbooks

### 6.1 `system_status_dev` (once)

```bash
# as postgres (OpenBao csg/pg-superuser); default privileges BEFORE the restore
export PGHOST=csg-postgres.k8s.ucar.edu PGUSER=postgres PGSSLMODE=require PGPASSWORD='...'
psql -d postgres -c 'CREATE DATABASE system_status_dev'
psql -d system_status_dev <<'SQL'
GRANT CONNECT ON DATABASE system_status_dev TO pguser;
GRANT USAGE ON SCHEMA public TO pguser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pguser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO pguser;
SQL
scripts/seed_status_dev.sh                    # schema + data + alembic_version; task_run empty
psql -d system_status_dev -c '\dp task_run'   # pguser: arwd
ALEMBIC_SYSTEM_STATUS_URL="postgresql://postgres:...@csg-postgres.k8s.ucar.edu/system_status_dev?sslmode=require" \
  alembic -c migrations/system_status/alembic.ini current   # expect (head); optional
```

The grant block mirrors the one above `dbCredentials` in `helm/values.yaml`; the seed
script carries the explicit `GRANT ... ON ALL TABLES` as the idempotent re-grant.

### 6.2 Refresh with live pods

`make refresh-dev`. The loader evicts the dev pods' sessions (same role) before the
rename; `/api/v1/health/ready` returns 200 again within one readiness period. Dev
writes since the last refresh (XRAS capture-only rows, test edits) are discarded by
design. `make sync-dev` does the same for the k8s DBs and additionally refreshes the
local compose Postgres.

### 6.3 Argo Application for the platform team (Phase 2)

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: sam-query-dev
  namespace: argocd                 # wherever sam-query lives
spec:
  project: csg
  source:
    repoURL: https://github.com/benkirk/sam-queries.git
    targetRevision: cirrus-dev
    path: helm
    helm:
      releaseName: samuel-dev
      valueFiles: [values.yaml, values-dev.yaml]
  destination:
    server: https://kubernetes.default.svc
    namespace: sam-queries-dev
  syncPolicy:                       # mirror sam-query's
    automated: {prune: true, selfHeal: true}
```

As applied, CIRRUS deployed it into a new namespace rather than adopting the
Phase-1 objects in `sam-queries`; the old release and its `incommon-cert-samuel-dev`
Secret were removed from `sam-queries` (2026-09-23), and `deploy-dev` is retired (§4.6).

### 6.4 Fresh install: the Redis race

On the first install (2026-09-13) the webapp pod PINGed Redis before the Redis pod
and its NetworkPolicy answered and logged `Redis is unreachable (Error 1 … Operation
not permitted)`. `Caching.__init__` and `Limiting.init_app`
(`src/webapp/caching/__init__.py`, `src/webapp/limiter/__init__.py`) decide the
fallback once per process, so the pod ran on per-worker caches and `memory://`
limits until `kubectl -n sam-queries-dev rollout restart deploy/samuel-dev`. Later
deploys find Redis already up; after a fresh install, look for that log line and
restart once. Not chart-fixed on purpose: the fallback is load-bearing and the
race is one-time per fresh install.

### 6.5 Hammering dev

The overlay raises `RATELIMIT_AUTHED`, `RATELIMIT_M2M` and `RATELIMIT_ANON` to
100000/min; `RATELIMIT_AUTH_LOGIN` keeps the prod default (it guards the OIDC
callback). The limiter still runs, so Admin → Configuration → Rate limits keeps
counting, and an API-key load run against `/api/v1/*` never sees a 429. To test
throttling behavior itself, use compose or restore the prod tiers in the overlay
temporarily. Interactive/authenticated profiling uses `scripts/dev_capture_session.py`
+ `scripts/dev_session_load.py` (capture one OIDC session, replay at concurrency);
the `profile-dev` skill carries the process and the target ranking. Two things the
app does not own: the Ingress annotations declare 100 req/s (burst ×5) and 200
connections per client IP, though 180 req/s from one IP was served without a
rejection, and `kubectl port-forward` is not a load transport (8 s tails at 32
clients). Numbers from the campaigns: `DEV_LOAD_CAMPAIGN.md`.

## 7. Sequencing

1. §4.1 → prod identity gate → §4.2, §4.3 → §4.4 → §4.5, §4.6, §4.7 → §4.8 → PR to
   `staging`. Inert on merge: overlay, tests, scripts, make targets, docs. Changes prod
   behavior: the workflow (a `staging` push now builds images; `target` plumbing in
   `update-helm`). Item 1 of §5 before merging the workflow change.
2. Merge to `staging` = the first automatic `cirrus-dev` pin.
3. §5 items 2–4, then `make deploy-dev` (since retired), then watch item 6 resolve and item 7 issue.
4. Promote `staging → main` so `-f target=...` becomes usable and push-to-main runs the
   new file (Ben owns the mechanics).
5. Phase 2: item 8, adoption, retirement — done 2026-09-23.

## 8. Verification

Before merge:

```bash
helm template samuel helm -f helm/values.yaml -n sam-queries | diff -u <before> - && echo PROD-IDENTICAL
helm lint helm/
helm template samuel-dev helm -f helm/values.yaml -f helm/values-dev.yaml -n sam-queries-dev > dev.yaml
grep -E '^kind:|^  name:' dev.yaml | paste - - | sort        # eyeball the object set once
make helm-test                                               # incl. the negative loop
python -m pytest tests/unit/test_xras_admin_client.py tests/unit/test_task_*.py tests/unit/test_docs.py -q
bash -n scripts/lib/cirrus_common.sh scripts/cirrus_healthcheck.sh scripts/cirrus_watch.sh
SAM_ENV=dev bash -c 'source scripts/lib/cirrus_common.sh; echo $NAMESPACE $RELEASE $WEBAPP_NAME $TASKS_NAME $INGRESS_HOST $TLS_SECRET'
bash -c 'source scripts/lib/cirrus_common.sh; echo $RELEASE $WEBAPP_NAME'   # prod unchanged
```

After the first deploy:

- `kubectl -n sam-queries-dev get externalsecret,certificate -l group=samuel-dev`: all Ready.
- `dig +short samuel-dev.k8s.ucar.edu` = `128.117.41.126`.
- `SAM_ENV=dev scripts/cirrus_healthcheck.sh -v` passes; plain
  `scripts/cirrus_healthcheck.sh` never mentions `samuel-dev`.
- `curl -s https://samuel-dev.k8s.ucar.edu/api/v1/health/ready | jq .checks`: `sam` and
  `system_status` healthy.
- Browser: login → Entra → back on `samuel-dev` (no AADSTS50011, no
  MismatchingStateError); logout lands on `/status/`. Admin → Configuration shows
  `SAM_DB_NAME=sam_dev`, `STATUS_DB_NAME=system_status_dev`, notify and XRAS levers off.
- `kubectl -n sam-queries-dev create job --from=cronjob/samuel-dev-tasks samuel-dev-tasks-smoke`:
  `task_run` rows appear in `system_status_dev`, and prod `system_status` has none with a
  `samuel-dev-tasks-*` runner.
- Isolation, in `pg_stat_activity` as postgres: `application_name like 'sam-webapp:samuel-dev%'`
  touches only `sam_dev` and `system_status_dev`. On MySQL prod, the processlist shows
  no dev-pod connection as the write user.
- Refresh drill with pods live: `make refresh-dev` completes and prints the evicted
  sessions; `/ready` is 200 within one probe period; the cache-refresh POST returns 200
  against dev.
- `git push origin HEAD:cirrus-dev` from a laptop is rejected (GH013);
  `git log -1 origin/cirrus-dev --format='%an %s'` shows the bot with `target=dev`.

## 9. Not in this PR

Deleting the retired AWS staging workflow, terraform, and `docs/STAGING.md`; collectors
dual-posting to dev; a Flask-Admin toggle for dev; the mail redirect valve (a one-line
overlay edit the test already tolerates); moving the refresh into the cluster (needs the
`hpc-reader` MySQL credential in OpenBao); a second namespace; pointing samuel-dev at
the XRAS test instance — priced side by side (outbound levers, render-test
assertions, an inbound `ROLE_XRAS` credential that survives the refresh, the ask to
Steve) in `XRAS_SUBMISSION.md` § 5 (this directory).

## 10. Status

| Item | State | Date |
|---|---|---|
| Plan written, decisions taken | done | 2026-09-12 |
| §4 implemented, eight commits on `k8s_dev_plan`; prod render byte-identical | done | 2026-09-12 |
| Living PR opened (`k8s_dev_plan` → `staging`) | done (#555) | 2026-09-13 |
| Ruleset covers `cirrus-dev` (before the CI commit merges) | done; negative push test rejected (GH013), branch not created | 2026-09-13 |
| OpenBao `csg/sam-dev-pg` (`username`, `password`), `csg/sam-dev-oidc` (`client_id`, `client_secret`, `issuer`, `flask_secret_key`) | done | 2026-09-13 |
| `system_status_dev` created and seeded (§6.1) | done; 18 tables, alembic `0006_task_run`, `task_run` empty, 1m49s | 2026-09-13 |
| Living PR merged (#555); first automatic `cirrus-dev` pin (sha-4d30e9d, `target=dev`) | done | 2026-09-13 |
| First `make deploy-dev`; DNS + cert live | done 15:04Z; 6 ExternalSecrets synced, cert Ready <1 min, A record by external-dns, `/` + `/ready` healthy on `sam_dev` + `system_status_dev` (101 models, no drift), `pg_stat_activity` isolation clean, first CronJob run touched dev only; one `rollout restart` for §6.4 | 2026-09-13 |
| Entra dev registration | interim: dev reply URL on prod's registration, verified (no AADSTS error); separate registration open | 2026-09-13 |
| Argo `sam-query-dev`; `deploy-dev` retired | done; own namespace `sam-queries-dev`, auto-follows the `cirrus-dev` pin; stale `incommon-cert-samuel-dev` Secret deleted from `sam-queries`; scripts' `--env dev` targets the new namespace | 2026-09-23 |
| kubectl RBAC in `sam-queries-dev` | done; `cirrus_watch.sh --env dev` and `cirrus_healthcheck.sh --env dev` read every section (the `watch-dev` skill) | 2026-09-26 |
