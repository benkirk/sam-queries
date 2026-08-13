# `samuel` Helm Chart

Kubernetes chart for the SAM webapp. This README covers the chart layout
only — the full deployment guides live elsewhere:

- **[docs/README-k8s.md](../docs/README-k8s.md)** — how to deploy, both
  locally (Docker Desktop Kubernetes) and to CIRRUS production
  (dependency matrix: External Secrets/OpenBao, nginx-external ingress +
  cert-manager, `csg-postgres` for fs-scans).
- **[docs/CIRRUS_PUBLISHING.md](../docs/CIRRUS_PUBLISHING.md)** — how a
  merge to `main` becomes a GHCR image and a force-pushed `cirrus`
  branch that GitOps reconciles onto the cluster. **Helm changes only
  reach production via that `main` → `cirrus` flow** — there is no
  direct `helm upgrade` against CIRRUS.

## Layout

```
helm/
├── Chart.yaml               # Chart metadata (name: samuel)
├── values.yaml              # Production/CIRRUS defaults (ingress, resources,
│                            #   gunicorn workerClass/workers/threads, probes)
├── values-local.yaml        # Local overrides: DISABLE_AUTH=1, no ExternalSecret,
│                            #   no cert-manager — see AUTHENTICATION.md
├── local-secrets.sh         # Creates the local Secret the chart expects
├── templates/
│   ├── deployment.yaml      # Webapp Deployment (gunicorn env plumbing)
│   ├── service.yaml  ingress.yaml  pdb.yaml
│   ├── external_secret.yaml # OpenBao-backed OIDC credentials (prod only)
│   ├── cronjob-tasks.yaml   # Hourly scheduled-task dispatcher (values: tasks.*)
│   └── redis-*.yaml         # Redis cache Deployment/Service/NetworkPolicy
└── tests/
    ├── test-oidc-render.sh     # Render assertions for the OIDC wiring
    └── test-cronjob-render.sh  # Render assertions for the task CronJob
```

## Quick reference

```bash
# Local install (Docker Desktop k8s) — details in docs/README-k8s.md
./local-secrets.sh
helm install samuel . -f values-local.yaml

# Render-check every assertion script without a cluster
make -C .. helm-test

# Inspect what production would render
helm template samuel . -f values.yaml | less
```

Gunicorn concurrency (worker class/count, threads) is set through
`values.yaml → deployment.yaml` env vars consumed by
`containers/webapp/gunicorn_config.py`; the rationale for the gthread
model is recorded in
`docs/plans/implemented/K8S_DEPLOYMENT_HARDENING.md`.
