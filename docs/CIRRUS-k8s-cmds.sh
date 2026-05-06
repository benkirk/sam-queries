#!/usr/bin/env bash
# ============================================================================
#  CIRRUS / k8s troubleshooting reference for the SAM webapp deployment.
#
#  This is a runbook, NOT a script to execute end-to-end. Pick the section that
#  matches your symptom and copy/paste the relevant command.
#
#  Cluster: nwc1   ·   Namespace: sam-queries   ·   Site: samuel.k8s.ucar.edu
#
#  Tested commands from the 2026-05-06 troubleshooting session that traced an
#  /allocations OOMKill back to a gunicorn worker over-provisioning bug
#  (multiprocessing.cpu_count() returning the node's 64 cores instead of the
#  pod's 16-core cgroup limit) and then verified the fix end-to-end.
# ============================================================================

# Set these once per shell so every command below is short. If you're already
# on a kubeconfig with a default context, you can drop --context.
CTX=nwc1
NS=sam-queries
DEPLOY=samuel


# ── 1. Sanity: am I connected? what's in the namespace? ─────────────────────
# 'get ns' lists every namespace you can see — confirms cluster access.
kubectl --context "$CTX" get ns
# 'get all -n …' shows pods, services, deployments, replicasets in one view.
kubectl --context "$CTX" -n "$NS" get all
# 'get pods -o wide' adds the node and pod IP — useful when traffic looks
# wrongly distributed or one node is suspect.
kubectl --context "$CTX" -n "$NS" get pods -o wide


# ── 2. Snapshot resource usage (CPU / memory) ───────────────────────────────
# Requires metrics-server in the cluster. Updates ~every 15 s, so two reads
# 30 s apart give a useful before/after.
kubectl --context "$CTX" -n "$NS" top pods
# Per-container view (matters when a pod has sidecars — sam doesn't, but the
# habit is good).
kubectl --context "$CTX" -n "$NS" top pods --containers
# Per-node usage — confirms the node isn't itself starved before blaming the pod.
kubectl --context "$CTX" top nodes


# ── 3. What image / env / limits is the deployment actually using? ─────────
# The deployment is the source of truth; the pod just inherits.
kubectl --context "$CTX" -n "$NS" get deploy "$DEPLOY" \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
# Print all environment variables in name=value form.
kubectl --context "$CTX" -n "$NS" get deploy "$DEPLOY" \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'
# CPU / memory requests + limits (look here when the pod OOMKills — confirm
# the cgroup limit you THINK is in effect actually is).
kubectl --context "$CTX" -n "$NS" get deploy "$DEPLOY" \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}' | jq .

# Full deployment dump (verbose; useful for `diff` between branches).
kubectl --context "$CTX" -n "$NS" get deploy "$DEPLOY" -o yaml > /tmp/deploy.yaml


# ── 4. Pod state, restart history, and OOMKill detection ───────────────────
# 'get pods' shows RESTARTS column at a glance.
kubectl --context "$CTX" -n "$NS" get pods
# Drill into one pod: lastState.terminated tells you WHY it died.
# Look for: reason: OOMKilled, exitCode: 137. That's the smoking gun.
POD=$(kubectl --context "$CTX" -n "$NS" get pods -l app="$DEPLOY" \
        -o jsonpath='{.items[0].metadata.name}')
kubectl --context "$CTX" -n "$NS" get pod "$POD" -o json \
  | jq -r '.status.containerStatuses[0]
           | "lastState=\(.lastState)  state=\(.state)  restartCount=\(.restartCount)"'

# 'describe pod' gives a human-readable summary including events at the bottom.
kubectl --context "$CTX" -n "$NS" describe pod "$POD"

# Recent namespace-wide events (shows OOMKill, FailedScheduling, image pull
# errors, etc.). --sort-by=.lastTimestamp because default order is meaningless.
kubectl --context "$CTX" -n "$NS" get events --sort-by=.lastTimestamp | tail -25


# ── 5. Inspect what's running INSIDE the pod ───────────────────────────────
# Process list sorted by RSS — finds memory hogs and confirms gunicorn worker
# count. The number of `gunicorn -c` lines = master + workers.
kubectl --context "$CTX" -n "$NS" exec "$POD" -- \
  ps -eo pid,ppid,rss,etime,cmd --sort=-rss | head -30

# Just the worker count.
kubectl --context "$CTX" -n "$NS" exec "$POD" -- \
  sh -c 'ps -e -o cmd | grep -c "gunicorn -c"'

# Total in-pod RSS in MiB (sums all processes — note this is NOT the cgroup
# accounting, which dedupes COW-shared pages from preload_app=True; it IS what
# `ps` would show summed naively).
kubectl --context "$CTX" -n "$NS" exec "$POD" -- \
  sh -c 'ps -e -o rss | awk "NR>1 {s+=\$1} END {printf \"%.0f MiB\n\", s/1024}"'

# Confirm cgroup vs host CPU — the bug that started this whole investigation.
# Inside a pod, multiprocessing.cpu_count() returns the host's CPUs (e.g. 64),
# NOT the cgroup quota (e.g. 16). cpu.max format is "<quota_us> <period_us>";
# quota / period = effective CPUs.
kubectl --context "$CTX" -n "$NS" exec "$POD" -- python3 -c '
import multiprocessing as m
print("cpu_count:", m.cpu_count())
print("cgroup_cpu.max:", open("/sys/fs/cgroup/cpu.max").read().strip())
'

# Drop into a pod for interactive poking.
kubectl --context "$CTX" -n "$NS" exec -it "$POD" -- bash


# ── 6. Logs (current + previous container) ─────────────────────────────────
# Live tail of stdout/stderr.
kubectl --context "$CTX" -n "$NS" logs -f "$POD"
# After a pod restart, --previous shows the dead container's logs (often where
# the actual error/traceback before the OOMKill lives).
kubectl --context "$CTX" -n "$NS" logs --previous "$POD"
# Last N lines (handy for grepping).
kubectl --context "$CTX" -n "$NS" logs --tail=200 "$POD" | grep -i error
# Log everything from all pods of the deployment (useful when a load-balancer
# rotates traffic across pods and you don't know which served the bad request).
kubectl --context "$CTX" -n "$NS" logs -l app="$DEPLOY" --tail=100 --prefix


# ── 7. Rollout history and rollback ────────────────────────────────────────
# Every prior image / spec change is preserved as a ReplicaSet. Useful when
# you suspect a recent deploy regressed something.
kubectl --context "$CTX" -n "$NS" rollout history deploy "$DEPLOY"
# Show the spec of a specific revision.
kubectl --context "$CTX" -n "$NS" rollout history deploy "$DEPLOY" --revision=5
# Show all replicasets — newest is at the top, prior ones have replicas=0.
# 'IMAGE' column tells you which sha shipped in each.
kubectl --context "$CTX" -n "$NS" get rs -l app="$DEPLOY" \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,REPLICAS:.spec.replicas,CREATED:.metadata.creationTimestamp

# Rollback to the previous good revision (DESTRUCTIVE — make sure!).
# kubectl --context "$CTX" -n "$NS" rollout undo deploy "$DEPLOY"
# Rollback to a specific revision.
# kubectl --context "$CTX" -n "$NS" rollout undo deploy "$DEPLOY" --to-revision=5


# ── 8. Watch a deploy roll out in real time ────────────────────────────────
# After pushing a new image / Helm upgrade.
kubectl --context "$CTX" -n "$NS" rollout status deploy "$DEPLOY" --timeout=5m
# Live watch on pod state changes (handy in a second terminal during deploys).
kubectl --context "$CTX" -n "$NS" get pods -w


# ── 9. Helm-specific (since this app is deployed via Helm) ─────────────────
# What releases exist?
helm --kube-context "$CTX" -n "$NS" list
# Show the values currently in effect for a release.
helm --kube-context "$CTX" -n "$NS" get values "$DEPLOY"
# Show every manifest the release rendered (full applied state).
helm --kube-context "$CTX" -n "$NS" get manifest "$DEPLOY"
# Render the chart locally without applying — best for verifying that an
# env-var or limits change templates as you expect BEFORE deploying.
helm template helm -f helm/values.yaml | grep -A1 GUNICORN_WORKERS
helm template helm -f helm/values-local.yaml | grep -A1 GUNICORN_WORKERS

# Dry-run an upgrade (computes diff vs cluster, never applies).
helm --kube-context "$CTX" -n "$NS" upgrade "$DEPLOY" helm \
  -f helm/values.yaml --dry-run --debug | less


# ── 10. The investigation pattern, in summary ───────────────────────────────
# When a pod is misbehaving and you don't know why, run these in order:
#   1.  kubectl get pods                  → restart count > 0?
#   2.  kubectl get pod $POD -o json | jq .status.containerStatuses[0].lastState
#                                         → exitCode 137 / OOMKilled?
#   3.  kubectl get events --sort-by=.lastTimestamp
#                                         → recent OOM / Pull / FailedSchedule?
#   4.  kubectl top pods                  → memory pressure right now?
#   5.  kubectl exec $POD -- ps -eo rss,cmd --sort=-rss | head
#                                         → which process is hogging?
#   6.  kubectl exec $POD -- cat /sys/fs/cgroup/cpu.max
#                                         → does the app think it has more
#                                           CPUs than the cgroup actually grants?
#   7.  kubectl logs --previous $POD      → what did it say before dying?
#   8.  kubectl rollout history deploy    → did this only start after a recent
#                                           deploy? roll back to confirm.
#
# References:
#   - kubectl cheat sheet:    https://kubernetes.io/docs/reference/kubectl/cheatsheet/
#   - JSONPath syntax:        https://kubernetes.io/docs/reference/kubectl/jsonpath/
#   - Helm command reference: https://helm.sh/docs/helm/helm/
