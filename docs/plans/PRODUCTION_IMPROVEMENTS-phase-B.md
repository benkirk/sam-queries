# SAMuel Hardening — Phase B (blast radius + detection)

> **Standalone restart plan.** Phase A (fail-closed auth, security headers, SRI
> registry, CSRF, Flask-Admin kill-switch, route authz, OIDC polish) shipped in
> PR #296 and is verified in production. The original merged assessment —
> including the PR295 finding IDs referenced below (P1-42, P2-86, P0-7/8/14/15,
> P1-10/55) — lives at `docs/plans/implemented/PRODUCTION_IMPROVEMENTS.md`.
> This doc is self-contained; you should not need the old one to execute.

## Context

SAMuel runs at https://samuel.k8s.ucar.edu/ (CIRRUS cluster, kubectl context
`nwc1`, namespace `sam-queries`, Helm release `samuel`). Phase A closed the
application-layer gaps. **Phase B is infrastructure hardening** — shrink the
blast radius if the app is ever compromised, and (separately) make audit/detection
durable. It is mostly Helm + Dockerfile, deployable only via the
`staging → main → cirrus` promotion path (helm changes do NOT ride ECS-staging).

**Reprioritization (2026-06-10 fresh-context reassessment).** The original Phase B
lumped five things together; reassessment against the live cluster reshuffled them:

- **Redis NetworkPolicy moved UP** — Redis DB 0 caches *rendered HTML fragments*
  (`user_aware_cache_key` views), so write access to Redis ≈ stored-XSS injected
  into logged-in dashboards, and there is no CSP yet. DB 1 holds the login
  brute-force rate-limit counters. The cluster CNI is **Cilium** (verified:
  `CiliumNetworkPolicy` CRDs present), so a NetworkPolicy is genuinely *enforced*.
  There are currently **zero** netpols in the namespace; Redis is reachable by any
  pod via its ClusterIP service with no auth.
- **`readOnlyRootFilesystem` dropped** from the non-root work — high emptyDir
  friction (matplotlib cache, gunicorn temp, audit-log fallback), deferred.
- **Redis `--requirepass` dropped** — needs a secret plumbed through both the
  redis args and the webapp's `CACHE_REDIS_URL`/`RATELIMIT_STORAGE_URI`; the
  NetworkPolicy gets ~90% of the value without the OpenBao/ExternalSecret friction.
- **Durable audit logs split into their own follow-up** — see the dedicated
  section near the bottom; the mechanism is undecided and bigger than YAML.
- **Auth-event logging and Sentry/5xx deferred** — lower urgency than the YAML
  hardening; noted at the end.

---

## Phase B-lite — the agreed implementation (ONE helm + Dockerfile PR)

Pure infra; no app-logic changes. Low risk, testable with `helm template` +
`scripts/cirrus_healthcheck.sh`. **Start on a fresh branch off `staging`**
(local `hardening` is the merged Phase A branch).

### Item 1 — Non-root container user

**File:** `containers/webapp/Dockerfile` (the `production` stage; `base` is shared
with `development`/webdev, so prefer setting `USER` in the `production` target to
keep webdev unaffected, OR set it in `base` and verify webdev still writes
`./logs/dev` — see ownership note).

Current state: no `USER` directive → gunicorn runs as **UID 0**. Gunicorn binds
`0.0.0.0:5050` (unprivileged port, verified in `containers/webapp/gunicorn_config.py`),
so a non-root user needs no extra capabilities.

Add to the production stage:
- `RUN useradd ...` (or numeric) a fixed non-root UID, e.g. **1000**.
- `ENV MPLCONFIGDIR=/tmp/mpl` (matplotlib writes a cache at import; without a
  writable home it warns/stalls). `/tmp` is world-writable in the image.
- `chown` `/var/log/sam` (audit-log dir) to the UID, OR rely on the existing
  graceful temp-dir fallback in `src/webapp/audit/logger.py:33`.
- `USER 1000` as the last instruction before `CMD`.

**Drop `AVD-DS-0002`** from `.trivyignore` (the globally-suppressed HIGH
"non-root user" rule). Leave the other AVD entries (DS-0001 tag pinning,
DS-0013/0015/0017/0020) unless separately addressed.

**Local bind-mount ownership (handled by Ben):** compose mounts
`./logs/{prod,dev}:/var/log/sam` (`compose.yaml`). A non-root UID won't own the
host dir → `RotatingFileHandler` falls back to `/tmp`. Ben will
`chmod ugo+rwX ./logs/{prod,dev}` locally. Document this one-liner in the PR /
compose docs. In k8s there is no bind mount — `/var/log/sam` is the writable
container layer, writable by any UID.

### Item 2 — Pod + container `securityContext`

**Files:** `helm/templates/deployment.yaml` and `helm/templates/redis-deployment.yaml`.
Neither has a `securityContext` today (verified). Add to BOTH pod specs.

Pod-level (`spec.template.spec`):
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000          # must match the Dockerfile UID for the webapp;
                           # for redis use the redis:7-alpine UID (999) — verify
  seccompProfile:
    type: RuntimeDefault
```
Container-level (each `containers[]` entry):
```yaml
securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
  # readOnlyRootFilesystem: true   # DEFERRED — needs emptyDir for /tmp,
  #                                # MPLCONFIGDIR, /var/log/sam fallback
```
Make these helm-values-driven (e.g. `webapp.securityContext` / a shared block)
so they're tunable. `runAsNonRoot: true` is the partner to Item 1: it makes the
kubelet *enforce* non-root even if a future image rebuild loses the `USER`.

**Redis caveat:** the official `redis:7-alpine` image runs as UID 999 and expects
to write `/data`; with `--save ""` (no persistence, verified in the chart) it
should tolerate a locked-down context, but verify the pod still starts —
`runAsUser` must match what the image expects, and drop-ALL-caps is usually fine
for redis. If redis fights it, ship the webapp securityContext first and iterate
on redis separately.

### Item 3 — Disable ServiceAccount token automount

**Files:** both pod specs (`spec.template.spec`):
```yaml
automountServiceAccountToken: false
```
The app never calls the k8s API; the default SA token is auto-mounted into the
pod, handing any code-exec foothold a cluster credential. One line, zero risk.

### Item 4 — Redis NetworkPolicy (highest value)

**New file:** `helm/templates/redis-networkpolicy.yaml` (guard with
`{{- if .Values.cache.enabled }}`). Use the portable `networking.k8s.io/v1`
`NetworkPolicy` (Cilium enforces it) rather than the Cilium CRD, for portability.

Selectors (verified in the chart):
- webapp pods carry `app: {{ .Values.webapp.name }}` → `app: samuel`
- redis pods carry `app: {{ .Values.cache.name }}` → `app: samuel-redis`
- redis listens on `{{ .Values.cache.port }}` → `6379`

Policy: select redis pods, default-deny ingress, allow ingress only from
`app: samuel` on TCP 6379:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ .Values.cache.name }}-allow-webapp
  namespace: {{ .Release.Namespace }}
spec:
  podSelector:
    matchLabels:
      app: {{ .Values.cache.name }}
  policyTypes: ["Ingress"]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: {{ .Values.webapp.name }}
      ports:
        - protocol: TCP
          port: {{ .Values.cache.port }}
```
This is default-deny-by-selection: once a NetworkPolicy selects the redis pods,
all non-matching ingress is dropped. **No `--requirepass`** (deferred — see
reprioritization). Confirm the webapp can still reach Redis post-deploy via the
healthcheck (Redis PING section) and by exercising a cached dashboard route.

### Verification (all of Phase B-lite)

1. `helm template helm/ | kubectl apply --dry-run=client -f -` (or `helm lint`) —
   chart renders, securityContext/netpol well-formed.
2. Local: `docker compose build webapp && docker compose up webapp` — gunicorn
   boots as the non-root UID; `docker compose exec webapp id` shows UID 1000;
   a cached dashboard route renders (Redis reachable). Ben pre-`chmod`s
   `./logs/{prod,dev}`.
3. Post-deploy (after `staging → main → cirrus`): `scripts/cirrus_healthcheck.sh`
   — pods Ready (securityContext didn't break boot), Redis PING still passes
   (NetworkPolicy allows webapp→redis), no new error-level log lines. Confirm a
   logged-in dashboard still serves cached fragments.
4. Trivy: `AVD-DS-0002` no longer suppressed and no longer fires (image is non-root).

---

## Deferred follow-ups (NOT in the Phase B-lite PR)

### Durable model-audit logs — own effort, mechanism undecided

Durability **is** wanted: prod's SAM DB is read-only *now* but won't always be,
and the `before_flush` ORM-change audit (`src/webapp/audit/`) currently writes
`/var/log/sam/model_audit.log` to the **ephemeral container writable layer** in
k8s (no volume mounts it) → lost on every redeploy.

Mechanism analysis (the fork to resolve before implementing):
- **DB audit table — RECOMMENDED.** Write audit rows via the existing
  `before_flush` hook into a dedicated audit table. Inherently durable,
  replica-agnostic, no PVC, no log-pipeline dependency. Bigger change: schema +
  decide whether it lives in the SAM DB being audited or a separate store.
  (See the existing `docs/plans/implemented/` for Alembic/schema conventions.)
- **stdout `StreamHandler`** — cheapest and multi-replica-clean, BUT durable
  *only if* nwc1 ships pod stdout off-node to a log backend (Loki/ELK/Splunk).
  **Unverified** — kubectl access is RBAC-scoped to `sam-queries`; could not see
  a log-shipping daemonset or storageclasses. Confirm with cluster admins before
  relying on this.
- **PVC at `/var/log/sam`** — AVOID. A single RWO PVC can't attach to 2 pods on 2
  nodes; it fights `replicaCount: 2` + topologySpread. Would need RWX (NFS/CephFS).

### Auth-event logging + Sentry

- Auth events (login success / failure / logout) currently hit `app.logger` only.
  For an internet-exposed app these are the events you most want visible — route
  them into the audit stream / structured stdout. Modest app-code change in the
  auth blueprint. [PR295 P1-10]
- Sentry behind an off-by-default `SENTRY_DSN`, or at minimum a loud
  `logger.error` + log-based alert for 5xx. Its own small project; depends on
  whether a Sentry instance exists. [PR295 P0-14]
- Healthcheck-failure alerting (503s logged as routine INFO today). [PR295 P1-55]

### Also still open (from the original assessment, lower priority)

- Two tiny silent prod bugs: `ProjectListSchema.get_admin_username` empty body
  [P0-5]; `ProjectSchema.get_panel` 500s on orphan projects [P0-6].
- Supply chain: SHA-pin GitHub Actions, server-side protect `cirrus` branch,
  migrate `BENKIRK_GITHUB_TOKEN` PATs, add workflow `permissions:` blocks
  [P0-10/12, P1-45/46/47].
- CSP — now generatable from `src/webapp/vendor_assets.py` origins (Phase A landed
  the prerequisite); inline-script audit still required first. Full discussion in
  `docs/plans/DEFERRED-CSP-discussion.md`.
- Ingress hardening annotations (TLS protocols, proxy-body-size, edge rate-limit);
  OIDC account-linking via Entra `oid`/`sub` [P1-2]; pip-audit/lockfile/Trivy gates.
- Entra app-registration: register `https://samuel.k8s.ucar.edu/status/` as a
  post-logout redirect URI (logout currently parks on Microsoft's Sign-out page —
  surfaced during Phase A prod verification; config, not code).

---

## Workflow reminders

- New work → fresh branch off `staging`; PR `--base staging`. Promotion to `main`
  is a separate manual PR, and only `main → cirrus` carries helm changes to the
  live cluster. (So Phase B's value isn't realized until that promotion.)
- Docs-only commits: `[skip ci]` in the message.
- Trivy / mega-linter run in CI; the deploy-path TruffleHog scan runs
  unconditionally (not skippable).
