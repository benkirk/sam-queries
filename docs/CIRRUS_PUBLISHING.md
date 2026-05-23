# CIRRUS Publishing & Deployment

How a code change on `main` becomes a running pod on the CIRRUS k8s cluster, and how the deploy path is locked down so only the intended workflow can touch it.

## Overview

The `cirrus` branch is **not** for human edits — it is a machine-maintained pointer that the CIRRUS k8s cluster's GitOps controller watches. The branch's only contents that ever change are `helm/values.yaml` image tags. Every successful build on `main` rewrites that file with the freshly-built SHA tag and force-pushes the result to `cirrus`. The k8s cluster reconciles on the new pin and rolls the workload.

```
main (push or tag)            ┌──────────────────────────┐         cirrus
─────────────────────────────►│ Publish Images and        │────────────────►  (k8s reconciles
                              │ CIRRUS Deploy             │   force-push      from this branch)
workflow_dispatch ──────────► │ .github/workflows/        │   as GitHub App
                              │ build-images-cirrus-      │
                              │ deploy.yaml               │
                              └──────────────────────────┘
                                  │      │       │
                                  ▼      ▼       ▼
                                ghcr.io: webapp, collectors, …
                                (tagged sha-<short>, latest, branch, semver)
```

## The pipeline

The single workflow is `.github/workflows/build-images-cirrus-deploy.yaml`. It has four jobs:

| Job | What it does | Notes |
|---|---|---|
| `setup` | Reads the image table (lines 48–66 of the workflow) and emits a build matrix | `DEFAULT[]=true` images run on every trigger; others are dispatch-only |
| `build` (matrix) | Multi-arch (`linux/amd64,linux/arm64`) Docker build, pushes to `ghcr.io/benkirk/sam-queries/<image>` with tags: `sha-<short>`, branch, semver components (on tags), and `latest` (only on `main`) | Uses the default `GITHUB_TOKEN` for `packages: write` |
| `summary` | Aggregates per-image artifacts into the workflow run's summary page | Read-only |
| `update-helm` | Rewrites `helm/values.yaml` to pin `webapp` to `sha-<short>`, then **force-pushes `cirrus`** | Runs only if `webapp_built==true`; pushes as the GitHub App, not `github-actions[bot]` |

### Triggers

| Trigger | Effect |
|---|---|
| Push to `main` | Builds default images (`webapp`, `collectors`), pins `cirrus`, deploy follows |
| Tag `v*` | Same as above, plus emits semver-tagged images |
| `workflow_dispatch` (Actions → "Publish Images and CIRRUS Deploy" → Run workflow) | Optional `images` input (e.g. `webapp mysql webdev`) overrides the default set; empty input = same as a push |

### Image tags emitted per build

Per image, `docker/metadata-action@v5` emits:
- `sha-<short>` — *always*; this is the tag `update-helm` pins into `helm/values.yaml`
- `<branch>` — e.g. `main`
- `<semver>` family — only on `v*` tags (`1.2.3`, `1.2`, `1`)
- `latest` — only on pushes to `main`

The k8s reconciler does not need `latest`. It only follows whatever `helm/values.yaml` on `cirrus` says, which is always a `sha-*` pin — immutable and traceable to the exact commit that produced it.

### Why `cirrus` is force-pushed every time

`cirrus` carries no history of its own — it is `helm/values.yaml` from `main` HEAD with one line rewritten. A linear history would just be a redundant copy of `main`. Each deploy resets the branch to the current `main` tree, rewrites the image line, and force-pushes a single commit. A `concurrency` guard (`group: cirrus-branch-push`, `cancel-in-progress: false`) serializes concurrent deploys so two pushes can't race.

## Branch protection

`cirrus` is locked by a repository ruleset. Combined with the workflow-side change to push as a dedicated GitHub App, the only thing that can write to `cirrus` is *this workflow*.

### The GitHub App

- **Name / slug**: `cirrus-benkirk-deployer` (slug `cirrus-benkirk-deployer`)
- **Installed on**: `benkirk/sam-queries` only
- **Permissions**: `Contents: Read & write`, `Workflows: Read & write` (Workflows is required because the `cirrus` tree carries `.github/workflows/`; without it the App push hits *"refusing to update workflow file"*)
- **Credentials in repo secrets**:
  - `CIRRUS_DEPLOY_APP_ID` — numeric App ID (App's General settings page)
  - `CIRRUS_DEPLOY_APP_PRIVATE_KEY` — full `.pem` contents (BEGIN/END lines included)

The `update-helm` job mints a short-lived token at runtime with `actions/create-github-app-token@v1`, then `actions/checkout@v4` is invoked with `token: ${{ steps.app-token.outputs.token }}` so `persist-credentials` wires the App token into the remote. The final `git push origin cirrus --force` authenticates as the App. The job-level `GITHUB_TOKEN` is downgraded to `contents: read`, so it physically cannot push.

Commits on `cirrus` are authored and committed by `<app-slug>[bot]` (`cirrus-benkirk-deployer[bot]@users.noreply.github.com`). This is the audit signal — any commit on `cirrus` not authored by the bot indicates something bypassed the workflow.

### The ruleset

A repository ruleset named **"Lock cirrus to deploy workflow"** (`enforcement: active`, target = branch, condition = `refs/heads/cirrus`) blocks `creation`, `update`, `deletion`, and `non_fast_forward` pushes. The single bypass entry is:

```json
{ "actor_type": "Integration", "actor_id": <CIRRUS_DEPLOY_APP_ID>, "bypass_mode": "always" }
```

`actor_type: "Integration"` is GitHub's term for "GitHub App". `actor_id` is the same numeric App ID stored in `CIRRUS_DEPLOY_APP_ID`. Repo admins (including the user who installed the App) cannot push directly — direct pushes fail with `GH013: Repository rule violations found for refs/heads/cirrus`.

**Caveat**: repo admins can still edit or disable the ruleset from Settings → Rules. This is not a guard against a determined admin — it blocks accidental direct pushes, *other* workflows that might try to write to `cirrus`, and non-admins, with a clear audit trail.

## Operating it

### Trigger a deploy manually

```bash
gh workflow run "Publish Images and CIRRUS Deploy" -R benkirk/sam-queries --ref main
gh run list -R benkirk/sam-queries --workflow="Publish Images and CIRRUS Deploy" --limit 3
gh run watch <run-id> -R benkirk/sam-queries
```

Optional image override: pass `-f images="webapp webdev mysql"` to build a non-default set. Leaving it empty matches the on-push behavior.

### Confirm a deploy succeeded

```bash
git fetch origin cirrus
git log -1 origin/cirrus --format='%h %ci %an%n%s'
```

Expected:
```
<sha> 2026-MM-DD HH:MM +0000 cirrus-benkirk-deployer[bot]
ci: pin webapp image to sha-<short> (2026-MM-DD HH:MM UTC) [skip ci]
```

If the author is **not** `cirrus-benkirk-deployer[bot]`, the App token path didn't engage on that run — investigate the `Mint GitHub App token` step in the run log.

### Negative push test (sanity check the lock)

```bash
git fetch origin cirrus
git branch -f /tmp-cirrus-test origin/cirrus
git commit-tree origin/cirrus^{tree} -p origin/cirrus -m 'should be blocked' > /dev/null
git push origin /tmp-cirrus-test:cirrus --force   # expect REJECTION
git branch -D /tmp-cirrus-test
```

Expected: `remote rejected ... push declined due to repository rule violations`. The remote `cirrus` is unchanged.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `update-helm` fails at `Mint GitHub App token` | Wrong/missing `CIRRUS_DEPLOY_APP_ID` or `CIRRUS_DEPLOY_APP_PRIVATE_KEY`, App not installed on this repo, or App ID is actually the Client ID | Verify secret values; reinstall the App on `benkirk/sam-queries`; confirm the App ID is from the App's General page, not the OAuth Client ID |
| `update-helm` fails at `git push origin cirrus --force` with `GH013: Repository rule violations` | Bypass actor mis-set (wrong `actor_id` or `actor_type`), or `actor_id` doesn't match `CIRRUS_DEPLOY_APP_ID` | `gh api /repos/benkirk/sam-queries/rulesets` → find the cirrus ruleset → confirm the bypass entry `actor_type: "Integration"`, `actor_id` = the App's numeric ID |
| `git push` fails with *"refusing to update workflow file"* | App lacks `Workflows: Read & write` permission | Edit the App's permissions; accept the install update on the repo |
| `update-helm` is skipped on a push to `main` | `webapp_built==false` because the dispatch input didn't include `webapp` | Re-trigger with empty `images` input, or include `webapp` explicitly |
| `cirrus` author is `github-actions[bot]` | Workflow ran before the App-token PR landed, or the App token was not threaded into checkout | Verify the workflow file on `main` includes the `app-token` step and `token:` line in the checkout |
| Two concurrent dispatches → confused cirrus state | Should not happen — the `concurrency` guard serializes pushes; if it does, the second waits | Inspect the workflow's `concurrency` block; both runs eventually push, last writer wins |

## Rollback

To revert a bad deploy, push a `main` change (a revert commit or a forward fix) — the next workflow run will repin `cirrus` to the new SHA. Editing `cirrus` directly is impossible by design.

To temporarily disable the lock (e.g. emergency hand-edit of `helm/values.yaml`):

```bash
gh api -X PUT /repos/benkirk/sam-queries/rulesets/<id> \
  -f enforcement=disabled
# … do the emergency push …
gh api -X PUT /repos/benkirk/sam-queries/rulesets/<id> \
  -f enforcement=active
```

Find the ruleset ID with `gh api /repos/benkirk/sam-queries/rulesets`. This requires repo-admin access.

## Cross-references

- Workflow: [`.github/workflows/build-images-cirrus-deploy.yaml`](../.github/workflows/build-images-cirrus-deploy.yaml)
- Helm values: [`helm/values.yaml`](../helm/values.yaml) — `webapp.container.image` is the line `update-helm` rewrites
- k8s overview: [`docs/k8s.md`](k8s.md), [`docs/README-k8s.md`](README-k8s.md)
- Staging environment (separate AWS ECS pipeline, not CIRRUS): [`docs/STAGING.md`](STAGING.md)
