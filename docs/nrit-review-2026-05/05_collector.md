# Phase 5 — Collector (`collectors/`)

> Sibling pyproject. Pulls usage data from Derecho, Casper, JupyterHub via SSH + PBS. Feeds the status tier audited in Phase 3. ~2,600 LOC across ~20 Python files. Cron-driven on host SSH-tunneled from `casper.hpc.ucar.edu` / `derecho.hpc.ucar.edu` every 5 minutes.

## Scope

- `collectors/lib/` — `base_collector.py`, `api_client.py`, `config.py`, `exceptions.py`, `logging_utils.py`, `pbs_client.py`, `ssh_utils.py`, `parallel_ssh.py`, `parsers/` (7 parser modules)
- `collectors/{casper,derecho,jupyterhub}/` — entry points + config.yaml
- `collectors/cron_scripts/` — `crontab`, `run_ncar_collectors.sh`
- `collectors/run_collectors.sh` — while-loop runner
- `collectors/pyproject.toml`, `requirements.txt`, `README.md`, `.env.example`
- `containers/collectors/` — Dockerfile + Makefile
- `tests/unit/test_collector_queue_parser.py` (only collector test)

## Method

Read end-to-end:

1. **Foundation:** `base_collector.py`, `api_client.py`, `config.py`, `exceptions.py`, `logging_utils.py`.
2. **One concrete collector trace:** Derecho → SSH/PBS → parse → POST to `/api/v1/status/derecho`. Then sanity-checked Casper and JupyterHub variants for the same patterns.
3. **SSH/PBS layer:** `pbs_client.py`, `ssh_utils.py`, `parallel_ssh.py`.
4. **Deployment surface:** `Dockerfile`, `Makefile`, `cron_scripts/crontab`, `run_ncar_collectors.sh`, `run_collectors.sh`, `.env.example`, `README.md`.
5. **Test coverage:** the single test file and the importlib gymnastics around it.

## Lenses applied

- Architecture
- Security (credentials to upstream systems + injection surface)
- Operability (primary — this is a cron-fed pipeline)
- Testing

---

## Findings

### Headline

The collector subsystem is **architecturally clean** — `BaseCollector` with abstract `_collect_node_data`, three concrete subclasses, a tight 9-module library, parallel SSH for login nodes, exponential-backoff retries on API errors that correctly skip non-retriable status codes, and an explicit `TZ=UTC` enforcement on the runner that ties into Phase 3's naive-UTC storage convention. The shape is good.

But this is the **operationally riskiest subsystem in the audit.** The data quality of the entire status tier depends on these collectors, and:

1. **Three concrete failure-handling bugs** — every concrete collector's exception handler in `_collect_node_data` substitutes all-zeros for node counts. A transient SSH timeout turns the dashboard from "we don't know right now" into "the system is fully down."
2. **Two shell-injection-by-config patterns** in `pbs_client.py:40` and `ssh_utils.py:113-116` — args come from `config.yaml` (trusted today) but f-string interpolation under `shell=True` is the classic foot-gun.
3. **Cron paths reference a personal Glade directory** (`/glade/work/benkirk/repos/sam-queries/collectors/cron_scripts`) — if `benkirk` leaves/rotates, collectors break silently.
4. **One test file** (`test_collector_queue_parser.py`). `api_client`, `base_collector`, `ssh_utils`, `pbs_client`, error paths — all untested. README §"Next Steps (Deferred)" explicitly admits this.
5. **No alerting on persistent failure** — a failing collector logs "✗ Failed" to the wrapper script and the cron job continues. Six hours of failures look identical to the dashboard as one hour.
6. **`verify=False` on the JupyterHub API call** with `urllib3.disable_warnings(...)` — SSL verification disabled with a "Matches existing behavior" comment.

None of these are showstoppers; all are within reach of focused PRs. The architecture survives Phase 5's audit. The ops posture less so.

### Architecture

**Strengths**

- **`BaseCollector` abstract pattern** with `_collect_node_data` as the single subclass extension point. The three concrete collectors are tight (Derecho 59 lines, Casper 107, JupyterHub 372 — only JupyterHub is heavy because of the JH-specific API path).
- **Custom typed exception hierarchy** (`CollectorError` → `PBSError`, `APIError`, `SSHError`, `ConfigError`). Used consistently throughout. Clean.
- **Retry with exponential backoff on `api_client.post_status`** (`api_client.py:49-99`) — 3 attempts, 2/4/8s waits, correctly skips retry on 400 (validation) and 401/403 (auth). Right shape.
- **Parallel SSH via ThreadPoolExecutor** for login nodes (`parallel_ssh.py`) — README says ~2-3s parallel vs ~20s sequential for 8 nodes. Good optimization.
- **`flock -xn`** in cron prevents overlapping runs.
- **`TZ=UTC export`** at the top of `run_collectors.sh:14`, with a 25-line README section explaining why (Phase 3's naive-UTC convention). Strong defensive choice, documented.

**Findings**

- **A1 [Med] `base_collector.py` try/except ImportError dance** for relative-vs-direct imports (lines 12-33), mirrored in `pbs_client.py`, `ssh_utils.py`, `config.py`. Smells like the package isn't installed in editable mode. `pyproject.toml` declares `[project.scripts]` entry points (`sam-collector-casper`, `sam-collector-derecho`) but the actual scripts use `sys.path.insert(0, ...)` (`derecho/collector.py:9`, `casper/collector.py:9`). Pick one path: `pip install -e collectors/` and use relative imports, OR delete the pyproject entry points and document the `python collectors/derecho/collector.py` invocation.

- **A2 [Med] Two parallel deployment models with unclear winner.** `containers/collectors/Dockerfile` + `Makefile` exist (build → `samuel-collectors:latest`), but `collectors/cron_scripts/crontab` runs from `benkirk`'s Glade directory via SSH from a different host. Either the container is dead code or there are two production deployments. Worth documenting which is canonical and either removing the other or noting the dev-vs-prod split.

- **A3 [Low] JupyterHub collector overrides `collect()`** (`jupyterhub/collector.py:343-364`) to skip `_collect_login_node_data`, `_collect_filesystem_data`, `_collect_reservation_data`. Comment explains why; works. But it means the BaseCollector contract is "all 5 collection steps OR override `collect()` entirely" — not the cleanest extension point. A "skip flags" pattern (`_collect_filesystem_data` no-ops when `self.config.filesystems == []`) might be tidier.

### Security

- **S1 [Med-High] Shell injection by config in `pbs_client.py:40` and `ssh_utils.py:113-116`** —

  ```python
  # pbs_client.py:40
  full_cmd = f'ssh -o ConnectTimeout={self.timeout} {self.host} "{cmd}"'
  result = subprocess.run(full_cmd, shell=True, ...)

  # ssh_utils.py:113-116
  cmd = (
      f"ssh -o ConnectTimeout={self.timeout} {self.base_host} "
      f'"ssh {node_name} \'cat /proc/loadavg; ...\'" '
  )
  result = subprocess.run(cmd, shell=True, ...)
  ```

  `self.host` / `self.base_host` come from `config.yaml`; `node_name` comes from yaml's `login_nodes` list. The yaml is checked into the repo, so this is trusted-input today. But a single yaml typo with shell metacharacters (e.g., a node name with a `;` or `$()`) executes arbitrary code on the cluster head node. Fix: `subprocess.run([list, of, args])` without `shell=True`, or `shlex.quote()` every interpolated value. The Casper login-node list is short and innocuous today — this is risk-by-pattern, not by-active-exploit.

- **S2 [Med] `verify=False` on JupyterHub API call** (`jupyterhub/collector.py:289`) with `urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)` at module level (`:26`). API token is sent in the `Authorization` header (`:288`) regardless. Comment: "Matches existing behavior." Either the JupyterHub deployment uses a cert NCAR's CA hasn't signed (fix: trust the CA, or pin a cert path with `verify=/path/to/ca.crt`) or someone disabled verification as a quick patch. Open question for Ben.

- **S3 [Med] Default `STATUS_API_URL=http://localhost:5050` over HTTP** (`config.py:37`, `.env.example:6`). The API key is sent in cleartext if anyone leaves the default in production. Production presumably uses HTTPS, but no enforcement / no warning when HTTP is detected. Fix: warn at startup if `STATUS_API_URL` is HTTP and the host isn't `localhost` / `127.0.0.1`.

- **S4 [Low] No `BatchMode=yes` or `StrictHostKeyChecking=accept-new` on SSH** — `pbs_client.py:40`, `ssh_utils.py:113`, `jupyterhub/collector.py:171`. Under cron, key-auth failure falls back to interactive password prompt or PAM challenge; first-connect to a new host requires manual `yes` confirmation. Both produce weird cron output and indefinite hangs. Fix: `-o BatchMode=yes -o StrictHostKeyChecking=accept-new`.

### Operability

- **O1 [High] Zero-substitution on collection failure** —

  ```python
  # derecho/collector.py:37-55, casper/collector.py:78-103, jupyterhub/collector.py:243-264
  except Exception as e:
      self.logger.error(f"Failed to collect node data: {e}", exc_info=True)
      data.update({
          'cpu_nodes_total': 0,
          'cpu_nodes_available': 0,
          # ... all-zeros for every counter ...
      })
  ```

  A transient SSH/PBS hiccup silently produces a status snapshot showing "0 nodes." The dashboard (which queries the latest snapshot) then shows "system fully down." `_collect_login_node_data` / `_collect_filesystem_data` in `base_collector.py:92-117` use empty lists instead of zeros, which is recoverable. But the top-level counters being zeroed corrupts the time series. Fix: skip the API POST entirely (return non-zero from `run()`) on `_collect_node_data` failure, OR set an explicit `degraded: True` flag on the record so dashboards can show "collection failed" vs "system down."

- **O2 [High] No alerting on persistent failure.** Failing collector → wrapper logs "✗ ${NAME} failed in ${elapsed}s" → cron job continues. Six hours of consecutive failures produce a stale dashboard and nothing else. Fix: integrate with NCAR's monitoring (Slack webhook, healthchecks.io heartbeat, …). Pairs with Phase 3's open question Q15 (is `cleanup_status_data.py` scheduled anywhere?). Open question for Ben.

- **O3 [High] `run_collectors.sh:91` swallows collector stdout/stderr** with `> /dev/null 2>&1`. The Python logger's stdout handler output is discarded. Only the `--log-file=` path survives — and if file logger creation fails (e.g., permissions), there's no fallback. Cron-level log files (`casper-collectors.log`, `derecho-collectors.err`) capture only the wrapper's progress lines, not the actual collector failures. Fix: redirect to a fallback stderr file, or use `tee`.

- **O4 [Med] Cron paths reference a personal Glade directory** —

  ```
  */5 * * * * flock -xn /tmp/benkirk-casper-status-collector.lock  ssh -tt casper.hpc.ucar.edu  "cd /glade/work/benkirk/repos/sam-queries/collectors/cron_scripts && ./run_ncar_collectors.sh ..."
  ```

  If `benkirk` rotates / departs / their Glade quota gets cleared, collectors break silently. Production should run from a shared/system location (`/glade/work/csg/` or a container with an immutable mount). Combined with O3, ops would not detect the failure for hours.

- **O5 [Med] Cron log redirection writes to working directory** — `1>casper-collectors.log 2>casper-collectors.err`. Where these land depends on the SSH session's `pwd` (which is `/glade/work/benkirk/repos/sam-queries/collectors/cron_scripts` per the preceding `cd`). Hard to find for ops without insider knowledge. Use absolute paths.

- **O6 [Med] Dockerfile unpinned + suboptimal** (`containers/collectors/Dockerfile`):
  - `FROM python:3` — unpinned major.minor. Builds drift across rebuilds. Fix: `FROM python:3.13-slim` (or whatever the project targets).
  - `apt-get update && rm -rf /var/lib/apt/lists/*` (`:26`) installs nothing — dead step.
  - `pip install -r requirements.txt` with no `--no-cache-dir` (image bloat).
  - No `USER` directive — runs as root.
  - Embeds a `list-large-directories` cleanup script (lines 3-24) that has nothing to do with collectors — debugging leftover.
  - No healthcheck, no labels, no signal-handling entrypoint.

- **O7 [Low] No idempotency key in API ingest.** Re-run at the same minute produces a duplicate `derecho_status` row. The Phase 3 span coalescer handles `user_proj_queue_status` cleverly; the top-level system-status tables just append. Probably fine (writes are append-only by design), but worth noting alongside Phase 3 Q17 (out-of-order ingest assumption).

### Testing

- **T1 [High] Test coverage is one file: `tests/unit/test_collector_queue_parser.py`.** `api_client.py` (retry logic, auth/error classification), `base_collector.py` (exception handling, partial-collection contract), `ssh_utils.py` (parallel collection + degraded entries), `pbs_client.py`, the JupyterHub statistics calculator (which has non-trivial logic in `_calculate_statistics`), parsers (nodes, jobs, filesystems, reservations, jupyterhub_nodes) — none have dedicated tests. README §"Next Steps (Deferred)" explicitly admits "Unit and integration tests" are deferred. Given that **the data quality of the entire status tier depends on this code**, this is the riskiest test gap in the audit.

- **T2 [Low] The one test file uses an importlib trick to load the parser without polluting sys.path** (`test_collector_queue_parser.py:14-22`) — the comment explains it: prepending `collectors/lib` would shadow `sam.config` because both name a module `config`. Sharp observation by the author. Confirms A1's "the import situation is messy" diagnosis.

### Strengths worth calling out (more)

- **README is honest about deferred work** — the "Next Steps (Deferred)" section lists 5 items (cron deployment, log rotation, healthchecks, deployment automation, tests). Most projects would hide this. Strength of documentation, weakness of execution.
- **`.env.example` carefully explains** that `STATUS_API_KEY` is an API key, not a password, and points to `scripts/gen_api_key.py`. The README §Configuration mistakenly says `your_password` instead — minor doc drift only.
- **`JobParser._extract_username`, `_extract_project_code` use sentinels** (`'_unknown_'` bucket for missing `Account_Name`) — clean handling of missing data, parsed in the test that exists.

### Doc drift

- **`docs/PBS_COLLECTORS_PLAN.md` and `docs/PBS_COLLECTORS_ADD_RESERVATIONS_PLAN.md`** in `collectors/docs/` are plan docs of the same shape as the Phase 1-flagged `src/webapp/IMPLEMENTATION_SUMMARY.md`. Probably stale post-implementation. Disposition pending Phase 7.
- **`README.md:159`** says `STATUS_API_KEY=your_password` in the Configuration section — contradicts the `.env.example:1-4` block that correctly identifies it as an API key. One-line README fix.
- **`README.md:11`** says "cron deployment documented in plan" — the plan is in `docs/PBS_COLLECTORS_PLAN.md`. The actual production cron is in `cron_scripts/crontab` and points at `benkirk`'s Glade dir. Reconcile.

---

## Cross-cutting tags raised

- `[XC: ops]` — No alerting on persistent collector failure; cron logs go to user-home relative paths; cron points at `benkirk`'s personal directory; Dockerfile vs cron-on-host deployment ambiguity. This is the biggest operational-gap concentration in the audit.
- `[XC: testing]` — Single test file for ~2,600 LOC; reliability of the status tier depends on this code. README's "Next Steps (Deferred)" makes this an acknowledged but uncovered gap.
- `[XC: prod-config-hardening]` — `verify=False` on JupyterHub API; default `STATUS_API_URL=http://`; no `BatchMode=yes` on SSH; `FROM python:3` unpinned. Pattern: defaults that work in dev / fail-open in prod.
- `[XC: convention-drift]` — Shell-string SSH commands under `shell=True` with f-string interpolation (`pbs_client.py:40`, `ssh_utils.py:113`); `try/except ImportError` import-path dance; `password` named arg that's really an API key.
- `[XC: docs-drift]` — README §Configuration contradicts `.env.example` on `STATUS_API_KEY` meaning; `docs/PBS_COLLECTORS_PLAN.md` likely stale.

## Open questions for Ben

1. **Which is canonical production — the container in `containers/collectors/` or the cron-from-`benkirk`'s-Glade setup?** Decides which deployment surface needs hardening.
2. **`verify=False` on the JupyterHub API call (S2)** — self-signed cert / NCAR CA / quick patch from a historical issue?
3. **Zero-substitution failure mode (O1)** — is "show all zeros on the dashboard when collection fails" the intended UX, or should the dashboard surface "stale" / "collection failed" instead? The fix differs based on intent.
4. **`/glade/work/benkirk/repos/...` in cron (O4)** — is this prod, or a transitional dev setup?
5. **Alerting on persistent failure (O2)** — what does NCAR ops use for "this service has been failing for 30 min"? Healthchecks.io heartbeat ping? Slack webhook? Should collectors hook into it?
6. **`STATUS_API_URL` HTTPS enforcement (S3)** — does production set HTTPS? Worth a startup warning if it's HTTP and host isn't localhost.
7. **`collectors/docs/PBS_COLLECTORS_*PLAN.md`** — implementation-plan docs from the build phase. Disposition: archive, delete, or keep as historical?
