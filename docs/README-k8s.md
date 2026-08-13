# Kubernetes Deployment Guide

This guide covers deploying the SAM webapp via Helm in two environments:
- **Local Development** — Docker Desktop k8s on your Mac (learn k8s without a remote cluster)
- **CIRRUS** — Remote production cluster at NCAR

The Helm chart lives in `helm/`. Both environments use the same templates; a layered
values file approach handles the differences.

---

## Local Development (Docker Desktop)

### Why This Works Without the Remote Cluster

The production chart depends on three things that don't exist locally:

| Dependency | Production | Local |
|---|---|---|
| **Secrets** | External Secrets Operator pulls creds from OpenBao | `helm/local-secrets.sh` creates k8s Secrets from `../.env` |
| **Ingress** | `nginx-external` ingress controller + InCommon TLS via cert-manager | Skipped — use `kubectl port-forward` instead |
| **Databases** | `sam-sql.ucar.edu`, `csg-postgres.k8s.ucar.edu` | Local MySQL via `host.docker.internal` |

`helm/values-local.yaml` sets `useExternalSecret: false` on all three credential blocks,
which suppresses the `ExternalSecret` CRD resources while keeping the `secretKeyRef`
env-var injection in the Deployment (secrets are created manually instead).

> **Note:** k8s pods cannot reach `127.0.0.1` on the Mac host. The values-local.yaml
> already uses `host.docker.internal` for all DB server addresses.

### Prerequisites

- **Docker Desktop** with Kubernetes enabled (Settings → Kubernetes → Enable Kubernetes)
- **kubectl**: `brew install kubectl`
- **helm**: `brew install helm`
- Local `../.env` file with database credentials (already set up if you use `docker compose`)

Verify k8s is running:
```bash
kubectl cluster-info
```

### First-Time Setup

```bash
# 1. Create a dedicated namespace
kubectl create namespace samuel-dev

# 2. Inject secrets from ../.env (creates the 3 k8s Secrets that ESO would normally provide)
bash helm/local-secrets.sh samuel-dev

# 3. Install the chart with local overrides
helm install samuel ./helm \
  -f helm/values.yaml \
  -f helm/values-local.yaml \
  -n samuel-dev

# 4. Verify the pod is running
kubectl get pods -n samuel-dev
kubectl logs -n samuel-dev -l app=samuel
```

### Accessing the App

`kubectl port-forward` creates a tunnel from your Mac to the pod. It must stay running
while you use the browser — leave it open in a dedicated terminal tab:

```bash
kubectl port-forward -n samuel-dev svc/samuel 6050:5050
```

Then open **http://localhost:6050** in your browser.

> Auth is disabled locally (`DISABLE_AUTH: "1"` in values-local.yaml), so no login is required.

### Daily Use

**Check status:**
```bash
kubectl get pods -n samuel-dev
kubectl describe pod -n samuel-dev -l app=samuel   # detailed events/errors
kubectl logs -n samuel-dev -l app=samuel --follow  # live log stream
```

**After changing `helm/` templates or `values-local.yaml`:**
```bash
helm upgrade samuel ./helm \
  -f helm/values.yaml \
  -f helm/values-local.yaml \
  -n samuel-dev
```

**After changing `../.env` credentials:**
```bash
bash helm/local-secrets.sh samuel-dev   # re-creates/updates the k8s Secrets
kubectl rollout restart deployment/samuel -n samuel-dev  # picks up new secret values
```

**Preview rendered manifests without deploying:**
```bash
helm template samuel ./helm \
  -f helm/values.yaml \
  -f helm/values-local.yaml \
  -n samuel-dev
```

### Destroy / Clean Up

```bash
helm uninstall samuel -n samuel-dev
kubectl delete namespace samuel-dev
```

This removes all k8s resources (Deployment, Service, Ingress, Secrets). Re-run
First-Time Setup to start fresh.

### Local vs Production Differences

| Setting | Local (`values-local.yaml`) | Production (`values.yaml`) |
|---|---|---|
| `replicaCount` | 1 | 2 |
| `DISABLE_AUTH` | `"1"` | `"0"` |
| `FLASK_DEBUG` | `"1"` | `"0"` |
| `SAM_DB_SERVER` | `host.docker.internal` | `sam-sql.ucar.edu` |
| `SAM_DB_REQUIRE_SSL` | `false` | `true` |
| `useExternalSecret` | `false` | `true` |
| CPU request | 0.5 | 4 |
| Memory request | 512M | 4096M |
| Ingress | Rendered but inactive | Active via Traefik |
| TLS | None | InCommon cert via cert-manager |

---

## CIRRUS (Remote Production)

### Infrastructure Already Present on the Cluster

CIRRUS provides the dependencies the chart expects:

- **External Secrets Operator (ESO)** — syncs secrets from OpenBao into k8s Secrets
- **SecretStore `csg-ro`** — read-only OpenBao connection for the `csg/` secret path
- **Traefik ingress controller** (`traefik-internal`) — routes traffic to pods
- **cert-manager** — auto-provisions TLS certificates via the `incommon` ClusterIssuer

You do not manage any of these directly. They are cluster-wide services.

### Prerequisites

- `kubectl` configured with a context pointing at CIRRUS
- Access to the deployment namespace (contact your cluster admin)

Verify your context:
```bash
kubectl config current-context
kubectl cluster-info
```

### Deploy

```bash
# Use only the production values (no -f values-local.yaml)
helm install samuel ./helm -f helm/values.yaml -n <namespace>
```

### How Secrets Work on CIRRUS

The four `ExternalSecret` CRD resources rendered by the chart instruct ESO to pull
credentials from OpenBao and create k8s Secrets automatically:

| k8s Secret | OpenBao Path | Contains |
|---|---|---|
| `samuel-db-credentials` | `csg/pg-superuser` | `STATUS_DB_USERNAME`, `STATUS_DB_PASSWORD` |
| `samuel-sam-db-credentials` | `csg/sam-readuser` | `SAM_DB_USERNAME`, `SAM_DB_PASSWORD` |
| `samuel-jh-credentials` | `csg/jh-api-token` | `JUPYTERHUB_API_TOKEN` |
| `samuel-oidc-credentials` | `csg/sam-oidc` | `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ISSUER`, `FLASK_SECRET_KEY` |

ESO refreshes these every hour (`refreshInterval: 1h`). You never manage these secrets
manually on CIRRUS — rotating credentials in OpenBao is sufficient.

**Force an immediate sync** (after rotating a value in OpenBao instead of waiting up to 1h):

```bash
kubectl annotate externalsecret samuel-oidc-credentials-esos -n <ns> \
  force-sync=$(date +%s) --overwrite
kubectl rollout restart deployment/samuel -n <ns>
```

### OIDC SSO

The k8s deployment authenticates via Microsoft Entra (Azure AD) OIDC. Config:

| Env var | Source | Purpose |
|---|---|---|
| `AUTH_PROVIDER=oidc` | `values.yaml` | Selects OIDC provider in the Flask app |
| `OIDC_REDIRECT_URI` | *(deliberately unset)* | Leaving it unset makes the callback follow the request host, so each ingress alias returns to itself. **Do not set it** — see below |
| `OIDC_USERNAME_CLAIM` | `values.yaml` | Default `preferred_username`; SAM resolves on the part before `@` |
| `OIDC_SCOPES` | `values.yaml` | Default `openid email profile` |
| `FLASK_CONFIG=production` | `values.yaml` | Required for HTTPS — sets `SESSION_COOKIE_SECURE=True` |
| `OIDC_CLIENT_ID` | `samuel-oidc-credentials` Secret | Microsoft Entra app client ID |
| `OIDC_CLIENT_SECRET` | `samuel-oidc-credentials` Secret | Microsoft Entra app client secret |
| `OIDC_ISSUER` | `samuel-oidc-credentials` Secret | e.g. `https://login.microsoftonline.com/{tenant}/v2.0` |
| `FLASK_SECRET_KEY` | `samuel-oidc-credentials` Secret | Per-environment session signing key (32-byte hex) |

The Entra app's reply URLs must include `https://samuel.k8s.ucar.edu/auth/oidc/callback`.
The post-logout redirect URI `https://samuel.k8s.ucar.edu/` must also be allowlisted
(see `infrastructure/README.md` "OIDC SSO Integration" for the IT handoff checklist).

Because the deployment serves a second hostname, the **same pair is registered
for `sam.hpc.ucar.edu`** — reply URL `https://sam.hpc.ucar.edu/auth/oidc/callback`
and post-logout `https://sam.hpc.ucar.edu/status/` — alongside the
`samuel.k8s.ucar.edu` entries.

#### The callback is derived per-host — do not pin it

`OIDC_REDIRECT_URI` is **intentionally absent** from `values.yaml`.
`webapp/auth/blueprint.py` treats it as an override
(`config.get('OIDC_REDIRECT_URI') or url_for('auth.oidc_callback', _external=True)`),
and `ProxyFix(x_host=1, x_proto=1)` in `webapp/run.py` resolves that from
`X-Forwarded-*`. So a login started on `sam.hpc.ucar.edu` returns to
`sam.hpc.ucar.edu`, and likewise for the platform alias.

Setting it back would break login on every host but the one named: Authlib
scopes the PKCE verifier / state cookie to the origin the login **started** on,
so a cross-host return cannot see that cookie and fails with
`MismatchingStateError`. Two guards exist — `helm/tests/test-oidc-render.sh`
asserts the var does not render, and
`tests/unit/test_oidc_auth.py::test_oidc_login_callback_follows_forwarded_host`
pins the derivation for both hosts.

Logout needs no equivalent setting: it already derives
`post_logout_redirect_uri` from the request host.

### Per-environment matrix

| Deployment | URL | Auth provider | OIDC creds source | Reply URL on Entra |
|---|---|---|---|---|
| Local Docker Compose (`webdev`) | `http://localhost:5050` | stub (`DISABLE_AUTH=1`) | n/a | n/a |
| Local k8s (Docker Desktop, `values-local.yaml`) | port-forwarded | stub (`DISABLE_AUTH=1`) | n/a | n/a |
| Fargate staging | `https://sam-staging.csgsam.ucar.edu` | oidc | AWS SSM `/sam/staging/oidc-*` | `https://sam-staging.csgsam.ucar.edu/auth/oidc/callback` |
| CIRRUS k8s (this chart) | `https://sam.hpc.ucar.edu` (advertised)<br>`https://samuel.k8s.ucar.edu` (platform alias) | oidc | OpenBao `csg/sam-oidc` | both `https://sam.hpc.ucar.edu/auth/oidc/callback` and `https://samuel.k8s.ucar.edu/auth/oidc/callback` |
| Future: ECS production | tbd | oidc | AWS SSM `/sam/production/oidc-*` | tbd |
| Future: k8s staging | tbd | oidc | OpenBao `csg/sam-staging-oidc` | tbd |

Scheduled tasks, by environment:

| Deployment | `tasks.enabled` | Notes |
|---|---|---|
| Local Docker Compose (`webdev`) | n/a — no chart | Run by hand: `sam-admin tasks --run-due` |
| Local k8s (Docker Desktop) | `false` | Nothing should silently DELETE local data |
| CIRRUS k8s (this chart) | `true`, kill-switched | `SAM_TASKS_DISABLED=cleanup_status_snapshots` until the soak completes |

When the per-environment Entra app strategy is adopted (separate `sam-production`
and `sam-staging` Entra apps), only the OpenBao / SSM values change — the chart
templates and Terraform modules stay identical.

### Accessing the App

No port-forward needed. Once deployed, the `nginx-external` ingress controller
routes HTTPS traffic automatically. Two hostnames serve the **same** deployment
in parallel — there is no redirect between them:

| Hostname | Role |
|---|---|
| **https://sam.hpc.ucar.edu** | **Advertised name** — the one to communicate to users |
| https://samuel.k8s.ucar.edu | Platform primary / cert CN. Kept for CIRRUS automation, health checks, and the parity + systems-integration tooling |

`sam.hpc.ucar.edu` is a CNAME to `samuel.k8s.ucar.edu`, so both resolve to the
same ingress. The chart renders one ingress rule per host from
`webapp.tls.fqdn` + `webapp.tls.extraHosts` (`helm/values.yaml`), and lists all
of them in a single `tls:` block.

TLS is provisioned automatically by cert-manager using the `incommon`
ClusterIssuer — one **multi-SAN** certificate covering every host above, stored
in the `incommon-cert-samuel` k8s Secret. To add another alias, append it to
`webapp.tls.extraHosts` and to `INGRESS_HOSTS` in
`scripts/lib/cirrus_common.sh`; cert-manager reissues to cover it.

> **Adding an alias is not just DNS + TLS.** A new host also needs its
> `/auth/oidc/callback` registered as an Entra reply URL, or login from it fails
> at the IdP with `AADSTS50011`. The callback itself needs no config change —
> it is derived per-host (see § OIDC above).

### Upgrade / Redeploy

```bash
# After chart or values changes
helm upgrade samuel ./helm -f helm/values.yaml -n <namespace>

# Check rollout status
kubectl rollout status deployment/samuel -n <namespace>

# Roll back if needed
helm rollback samuel -n <namespace>
```

### Verify Deployment

```bash
kubectl get pods -n <namespace>
kubectl get externalsecrets -n <namespace>   # check ESO sync status
kubectl get ingress -n <namespace>
kubectl get cronjob -n <namespace>           # samuel-tasks (see below)
kubectl logs -n <namespace> -l app=samuel --tail=50
```

### Scheduled tasks

The chart deploys **one** CronJob, `samuel-tasks`, which wakes hourly at `:07`
and asks each registered task "what slot are we in?". Individual schedules are
Python declarations in `src/scheduling/tasks/`, not chart values — adding a task
is a code change. A `task_run` ledger in `system_status` makes a late or
duplicate dispatch a no-op, so the CronJob's own cron string is arbitrary.

Design and rationale: `docs/plans/SCHEDULED_TASKS.md`.

```bash
# What exists, and when each task last ran. Reads the ledger; changes nothing.
kubectl exec -n <namespace> deploy/samuel -- sam-admin tasks --list
kubectl exec -n <namespace> deploy/samuel -- sam-admin --format json tasks --history | jq

# Don't wait for the top of the hour
kubectl create job -n <namespace> --from=cronjob/samuel-tasks tasks-manual-1
kubectl logs -n <namespace> job/tasks-manual-1
```

⚠️ **The kill switch.** `tasks.env.SAM_TASKS_DISABLED` is a comma-separated list
of task names to skip, flippable in `values.yaml` with no code deploy. It ships
**non-empty** (`cleanup_status_snapshots`) so that merging the chart deploys a
dispatcher which runs hourly, writes `skipped` rows and deletes nothing — the
24 h soak that proves credentials, DNS, image and Postgres reachability from a
pod that is not the webapp, with zero blast radius. Clearing it is a separate,
reviewable one-line commit.

`tasks.enabled: false` in `values-local.yaml`: on Docker Desktop nothing should
silently DELETE local data. To smoke-test it there:

```bash
helm upgrade --install samuel ./helm -f helm/values.yaml -f helm/values-local.yaml \
  -n samuel-dev --set tasks.enabled=true --set tasks.schedule='*/5 * * * *' \
  --set 'tasks.env.SAM_TASKS_DISABLED=cleanup_status_snapshots'
```

### Destroy

```bash
helm uninstall samuel -n <namespace>
```

Note: The `ExternalSecret` resources (and the k8s Secrets they manage) are deleted with
the release. OpenBao credentials are unaffected.
