# CIRRUS Publishing & Deployment

How a code change becomes a running pod on the CIRRUS k8s cluster, for both the production release (`samuel`) and the dev release (`samuel-dev`), and how the deploy path is locked down so only the intended workflow can touch it.

## Overview

The `cirrus` and `cirrus-dev` branches are **not** for human edits — they are machine-maintained pointers that the CIRRUS k8s cluster's GitOps controller watches. The only content that ever changes on either is the `helm/values.yaml` image tag. Every successful build rewrites that file with the freshly-built SHA tag and force-pushes the result to the branch its **target** selects. The k8s cluster reconciles on the new pin and rolls the workload.

```
main (push or tag) ───────────► target=prod ──────────────────────► cirrus
                              ┌──────────────────────────┐          (samuel,
staging (push) ──────────────►│ Publish Images and        │          prod)
                              │ CIRRUS Deploy             │
workflow_dispatch ───────────►│ .github/workflows/        │
  --ref <any branch>          │ build-images-cirrus-      │
  target=dev (default)        │ deploy.yaml               │
  target=prod (explicit)      └──────────────────────────┘
                                  │      │       │      target=dev ─► cirrus-dev
                                  ▼      ▼       ▼                    (samuel-dev)
                                ghcr.io: webapp, collectors, …
                                (tagged sha-<short>, latest, branch, semver)
```

The two branches carry the same chart; `samuel-dev` reads it with `helm/values-dev.yaml` layered on top (see `docs/plans/K8S_DEV_ENVIRONMENT.md`).

## The pipeline

The single workflow is `.github/workflows/build-images-cirrus-deploy.yaml`. It has four jobs:

| Job | What it does | Notes |
|---|---|---|
| `setup` | Resolves the deploy **target** (`dev`/`prod`) and its branch (`cirrus-dev`/`cirrus`), reads the image table and emits a build matrix | `DEFAULT[]=true` images run on every trigger; others are dispatch-only |
| `build` (matrix) | Multi-arch (`linux/amd64,linux/arm64`) Docker build, pushes to `ghcr.io/benkirk/sam-queries/<image>` with tags: `sha-<short>`, branch, semver components (on tags), and `latest` (only on `main`) | Uses the default `GITHUB_TOKEN` for `packages: write` |
| `summary` | Aggregates per-image artifacts into the workflow run's summary page | Read-only |
| `update-helm` | Rewrites `helm/values.yaml` to pin `webapp` to `sha-<short>`, then **force-pushes the target branch** | Runs only if `webapp_built==true`; pushes as the GitHub App, not `github-actions[bot]` |

### Triggers and targets

Prod is never inferred. Only a push to `main` or a `v*` tag selects it on its own.

| Trigger | Target | Effect |
|---|---|---|
| Push to `main` | `prod` | Builds default images (`webapp`, `collectors`), pins `cirrus`, prod deploy follows |
| Tag `v*` | `prod` | Same, plus semver-tagged images |
| Push to `staging` | `dev` | Builds default images, pins `cirrus-dev`, `samuel-dev` follows |
| `workflow_dispatch` on any ref | `dev` unless `target=prod` | Optional `images` input overrides the default set; `target=prod` from a ref other than `main` is allowed but writes a `::warning` and a step-summary line |

A `staging → main` promotion does **not** redeploy dev: the post-merge sync force-pushes `staging` with `GITHUB_TOKEN`, which triggers no workflow, and the two trees are identical anyway.

### Image tags emitted per build

Per image, `docker/metadata-action@v6` emits:
- `sha-<short>` — *always*; this is the tag `update-helm` pins into `helm/values.yaml`
- `<branch>` — e.g. `main`, `staging`
- `<semver>` family — only on `v*` tags (`1.2.3`, `1.2`, `1`)
- `latest` — only on pushes to `main`

The k8s reconciler does not need `latest`. It only follows whatever `helm/values.yaml` on its branch says, which is always a `sha-*` pin — immutable and traceable to the exact commit that produced it.

### Why the branches are force-pushed every time

`cirrus` and `cirrus-dev` carry no history of their own — each is the triggering ref's tree with one line of `helm/values.yaml` rewritten. A linear history would just be a redundant copy. Each deploy resets the branch to that tree, rewrites the image line, and force-pushes a single commit. A `concurrency` guard per target (`group: cirrus-branch-push-<target>`, `cancel-in-progress: false`) serializes concurrent deploys to the same branch so two pushes can't race; a dev and a prod deploy may run side by side.

## Branch protection

Both branches are locked by one repository ruleset. Combined with the workflow-side change to push as a dedicated GitHub App, the only thing that can write to either is *this workflow*.

### The GitHub App

- **Name / slug**: `cirrus-benkirk-deployer` (slug `cirrus-benkirk-deployer`)
- **Installed on**: `benkirk/sam-queries` only
- **Permissions**: `Contents: Read & write`, `Workflows: Read & write` (Workflows is required because the pushed tree carries `.github/workflows/`; without it the App push hits *"refusing to update workflow file"*)
- **Credentials in repo secrets**:
  - `CIRRUS_DEPLOY_APP_ID` — numeric App ID (App's General settings page)
  - `CIRRUS_DEPLOY_APP_PRIVATE_KEY` — full `.pem` contents (BEGIN/END lines included)

The `update-helm` job mints a short-lived token at runtime with `actions/create-github-app-token@v3`, then `actions/checkout@v7` is invoked with `token: ${{ steps.app-token.outputs.token }}` so `persist-credentials` wires the App token into the remote. The final `git push origin <branch> --force` authenticates as the App. The job-level `GITHUB_TOKEN` is downgraded to `contents: read`, so it physically cannot push.

Commits on both branches are authored and committed by `<app-slug>[bot]` (`cirrus-benkirk-deployer[bot]@users.noreply.github.com`). This is the audit signal — any commit on `cirrus` or `cirrus-dev` not authored by the bot indicates something bypassed the workflow.

### The ruleset

A repository ruleset named **"Lock cirrus to deploy workflow"** (`enforcement: active`, target = branch, conditions = `refs/heads/cirrus` **and** `refs/heads/cirrus-dev`) blocks `creation`, `update`, `deletion`, and `non_fast_forward` pushes. The single bypass entry is:

```json
{ "actor_type": "Integration", "actor_id": <CIRRUS_DEPLOY_APP_ID>, "bypass_mode": "always" }
```

`actor_type: "Integration"` is GitHub's term for "GitHub App". `actor_id` is the same numeric App ID stored in `CIRRUS_DEPLOY_APP_ID`. Repo admins (including the user who installed the App) cannot push directly — direct pushes fail with `GH013: Repository rule violations found for refs/heads/cirrus`.

Adding a ref to the condition list is a `PUT` of the whole ruleset (find `<id>` with `gh api /repos/benkirk/sam-queries/rulesets`):

```bash
gh api /repos/benkirk/sam-queries/rulesets/<id> \
  | jq '{name, target, enforcement, bypass_actors, rules,
         conditions: {ref_name: {include: ["refs/heads/cirrus", "refs/heads/cirrus-dev"], exclude: []}}}' \
  | gh api -X PUT /repos/benkirk/sam-queries/rulesets/<id> --input -
```

**Caveat**: repo admins can still edit or disable the ruleset from Settings → Rules. This is not a guard against a determined admin — it blocks accidental direct pushes, *other* workflows that might try to write to the branches, and non-admins, with a clear audit trail.

## Operating it

### Deploy a feature branch to dev

```bash
gh workflow run "Publish Images and CIRRUS Deploy" -R benkirk/sam-queries --ref <branch>
gh run list -R benkirk/sam-queries --workflow="Publish Images and CIRRUS Deploy" --limit 3
gh run watch <run-id> -R benkirk/sam-queries
```

No `target` input needed: an empty input is `dev`. Pass `-f target=prod` only for a deliberate prod deploy from a ref other than `main` (the triage-week loop); the run warns about it. Optional image override: `-f images="webapp webdev mysql"` builds a non-default set.

### Trigger a prod deploy manually

```bash
gh workflow run "Publish Images and CIRRUS Deploy" -R benkirk/sam-queries --ref main -f target=prod
```

### Confirm a deploy succeeded

```bash
git fetch origin cirrus cirrus-dev
git log -1 origin/cirrus     --format='%h %ci %an%n%s'
git log -1 origin/cirrus-dev --format='%h %ci %an%n%s'
```

Expected (the subject names the target and the ref that is now live there):
```
<sha> 2026-MM-DD HH:MM +0000 cirrus-benkirk-deployer[bot]
ci: pin webapp image to sha-<short> (2026-MM-DD HH:MM MDT) target=dev from <branch> [skip ci]
```

If the author is **not** `cirrus-benkirk-deployer[bot]`, the App token path didn't engage on that run — investigate the `Mint GitHub App token` step in the run log.

### Negative push test (sanity check the lock)

Run once per locked branch (`cirrus`, `cirrus-dev`):

```bash
B=cirrus-dev
git fetch origin "$B"
NEW=$(git commit-tree "origin/$B^{tree}" -p "origin/$B" -m 'should be blocked')
git push origin "$NEW:refs/heads/$B" --force   # expect REJECTION
```

Expected: `remote rejected ... push declined due to repository rule violations`. The remote branch is unchanged.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `update-helm` fails at `Mint GitHub App token` | Wrong/missing `CIRRUS_DEPLOY_APP_ID` or `CIRRUS_DEPLOY_APP_PRIVATE_KEY`, App not installed on this repo, or App ID is actually the Client ID | Verify secret values; reinstall the App on `benkirk/sam-queries`; confirm the App ID is from the App's General page, not the OAuth Client ID |
| `update-helm` fails at the force-push with `GH013: Repository rule violations` | Bypass actor mis-set (wrong `actor_id` or `actor_type`), `actor_id` doesn't match `CIRRUS_DEPLOY_APP_ID`, or the ruleset does not yet list `cirrus-dev` | `gh api /repos/benkirk/sam-queries/rulesets` → find the cirrus ruleset → confirm the bypass entry `actor_type: "Integration"`, `actor_id` = the App's numeric ID, and both refs in `conditions.ref_name.include` |
| `gh workflow run ... -f target=...` is rejected as an unexpected input | The workflow file carrying `target` is not on `main` yet | Dispatch bare (`--ref <branch>`, no `-f target`), which lands on dev; `-f target=prod` needs the promotion first |
| `git push` fails with *"refusing to update workflow file"* | App lacks `Workflows: Read & write` permission | Edit the App's permissions; accept the install update on the repo |
| `update-helm` is skipped on a push | `webapp_built==false` because the dispatch input didn't include `webapp` | Re-trigger with empty `images` input, or include `webapp` explicitly |
| `setup` fails with `push from unexpected ref` | A push trigger fired from a branch other than `main`/`staging` | The `on.push.branches` list and the resolver disagree; fix both in one change |
| A branch author is `github-actions[bot]` | Workflow ran before the App-token PR landed, or the App token was not threaded into checkout | Verify the workflow file on `main` includes the `app-token` step and `token:` line in the checkout |
| Two concurrent dispatches → confused branch state | Should not happen — the per-target `concurrency` guard serializes pushes; if it does, the second waits | Inspect the workflow's `concurrency` block; both runs eventually push, last writer wins |

## Rollback

To revert a bad prod deploy, push a `main` change (a revert commit or a forward fix) — the next workflow run will repin `cirrus` to the new SHA. For dev, dispatch the workflow from whichever branch should be live. Editing either branch directly is impossible by design.

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
- Helm values: [`helm/values.yaml`](../helm/values.yaml) — `webapp.container.image` is the line `update-helm` rewrites; [`helm/values-dev.yaml`](../helm/values-dev.yaml) is layered on it for `samuel-dev`
- Dev deployment: [`docs/plans/K8S_DEV_ENVIRONMENT.md`](plans/K8S_DEV_ENVIRONMENT.md)
- k8s overview: [`docs/k8s.md`](k8s.md), [`docs/README-k8s.md`](README-k8s.md)
- Staging environment (separate AWS ECS pipeline, not CIRRUS): [`docs/STAGING.md`](STAGING.md)
