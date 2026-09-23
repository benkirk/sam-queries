# `samuel` Helm Chart

Kubernetes chart for the SAM webapp. This README covers the chart layout
only — the full deployment guides live elsewhere:

- **[docs/README-k8s.md](../docs/README-k8s.md)** — how to deploy, both
  locally (Docker Desktop Kubernetes) and to CIRRUS
  (dependency matrix: External Secrets/OpenBao, nginx-external ingress +
  cert-manager, `csg-postgres` for fs-scans).
- **[docs/CIRRUS_PUBLISHING.md](../docs/CIRRUS_PUBLISHING.md)** — how a
  merge to `main` (or `staging`) becomes a GHCR image and a force-pushed
  `cirrus` (or `cirrus-dev`) branch that GitOps reconciles onto the cluster.
  **Helm changes reach production only via that `main` → `cirrus` flow** —
  there is no direct `helm upgrade` against either release. The dev
  release (`samuel-dev`) follows the same flow from `cirrus-dev`, applied by
  Argo CD application `sam-query-dev` into namespace `sam-queries-dev` (see
  [docs/plans/K8S_DEV_ENVIRONMENT.md](../docs/plans/K8S_DEV_ENVIRONMENT.md)).

## Layout

```
helm/
├── Chart.yaml               # Chart metadata (name: samuel)
├── values.yaml              # Production/CIRRUS defaults (ingress, resources,
│                            #   gunicorn workerClass/workers/threads, probes)
├── values-dev.yaml          # samuel-dev overlay: dev names/host, Postgres sam_dev,
│                            #   mail + XRAS levers off, own OpenBao paths
├── values-local.yaml        # Local overrides: DISABLE_AUTH=1, no ExternalSecret,
│                            #   no cert-manager — see AUTHENTICATION.md
├── local-secrets.sh         # Creates the local Secret the chart expects
├── files/capacities.json    # Machine capacities, mounted via the ConfigMap
├── templates/
│   ├── deployment.yaml      # Webapp Deployment (gunicorn env plumbing)
│   ├── service.yaml  ingress.yaml  pdb.yaml  configmap-capacities.yaml
│   ├── external_secret.yaml # OpenBao-backed credentials (7 ExternalSecrets, prod/dev)
│   ├── cronjob-tasks.yaml   # Hourly scheduled-task dispatcher (values: tasks.*)
│   └── redis-*.yaml         # Redis cache Deployment/Service/NetworkPolicy
└── tests/
    ├── lib/assert.sh           # Shared helpers + `render <prod|dev|local>` (not a test)
    ├── test-oidc-render.sh     # Render assertions for the OIDC wiring
    ├── test-cronjob-render.sh  # Render assertions for the task CronJob
    └── test-dev-render.sh      # samuel-dev is authenticated, mute, on its own data,
                                #   disjoint from prod — and each check is proven to fail
```

## Quick reference

```bash
# Local install (Docker Desktop k8s) — details in docs/README-k8s.md
./local-secrets.sh
helm install samuel . -f values-local.yaml

# Render-check every assertion script without a cluster
make -C .. helm-test

# Inspect what production / dev would render
helm template samuel . -f values.yaml | less
helm template samuel-dev . -f values.yaml -f values-dev.yaml | less
```

Gunicorn concurrency (worker class/count, threads) is set through
`values.yaml → deployment.yaml` env vars consumed by
`containers/webapp/gunicorn_config.py`; the rationale for the gthread
model is recorded in
`docs/plans/implemented/K8S_DEPLOYMENT_HARDENING.md`.
