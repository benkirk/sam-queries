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
| **Ingress** | Traefik + InCommon TLS cert-manager | Skipped — use `kubectl port-forward` instead |
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

The three `ExternalSecret` CRD resources rendered by the chart instruct ESO to pull
credentials from OpenBao and create k8s Secrets automatically:

| k8s Secret | OpenBao Path | Contains |
|---|---|---|
| `samuel-db-credentials` | `csg/pg-superuser` | `STATUS_DB_USERNAME`, `STATUS_DB_PASSWORD` |
| `samuel-sam-db-credentials` | `csg/sam-readuser` | `SAM_DB_USERNAME`, `SAM_DB_PASSWORD` |
| `samuel-jh-credentials` | `csg/jh-api-token` | `JUPYTERHUB_API_TOKEN` |

ESO refreshes these every hour (`refreshInterval: 1h`). You never manage these secrets
manually on CIRRUS — rotating credentials in OpenBao is sufficient.

### Accessing the App

No port-forward needed. Once deployed, Traefik routes HTTPS traffic automatically:

**https://samuel.k8s.ucar.edu**

TLS is provisioned automatically by cert-manager using the `incommon` ClusterIssuer.
The certificate is stored in the `incommon-cert-samuel` k8s Secret.

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
kubectl logs -n <namespace> -l app=samuel --tail=50
```

### Destroy

```bash
helm uninstall samuel -n <namespace>
```

Note: The `ExternalSecret` resources (and the k8s Secrets they manage) are deleted with
the release. OpenBao credentials are unaffected.
