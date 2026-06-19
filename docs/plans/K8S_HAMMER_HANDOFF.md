# Handoff: Hammer k8s (fs-scans load + protection-chain verification)

**Purpose.** Reproduce, from a fresh session, the load-pressure test we ran against
the live `samuel` deploy on nwc1 — exercise the gthread worker model, the fs-scans
`statement_timeout`, and the route-level graceful-degradation, under deliberately
heavy concurrency. Use this AFTER peer `hpc-usage-queries` DB-query optimization to
re-baseline and confirm the protection chain still holds (and that scans got faster).

> Context: this verifies SAM PR #323 (gthread + `statement_timeout`) and the
> `csg-postgres-ro` replica repoint. See `docs/plans/K8S_DEPLOYMENT_HARDENING.md`
> for the design + the original findings. Peer CNPG hardening = hpc-usage-queries #78.

---

## 0. Preconditions (ask the user / confirm)

- **VPN up** (needed to reach `samuel.k8s.ucar.edu` and the nwc1 ingress).
- `kubectl` context is **nwc1**, namespace **sam-queries** (`kubectl config current-context`).
- **Peer is watching CNPG** before you apply load — confirm with the user first; this
  hits the production `campaign` DB **replica** (`csg-postgres-ro`). Coordinate.
- **Playwright MCP** available for an authenticated browser session (routes are
  `@login_required`). The user does the 2FA step.
- Re-baseline mindset: peer query optimization may have made the slow path **faster**,
  so you may need **harder** load (more concurrency / bigger scopes) to trip the 100s cap.

---

## 1. Structural verification (in-pod — no browser needed)

```bash
POD=$(kubectl -n sam-queries get pod -l app=samuel -o jsonpath='{.items[0].metadata.name}')
echo "pod=$POD image=$(kubectl -n sam-queries get pod $POD -o jsonpath='{.spec.containers[0].image}')"
# gthread env + replica host + worker count (expect ~10 = master + 9; class=gthread)
kubectl -n sam-queries exec "$POD" -- sh -c 'echo "CLASS=$GUNICORN_WORKER_CLASS WORKERS=$GUNICORN_WORKERS THREADS=$GUNICORN_THREADS"; echo "FS_SCAN_PG_HOST=$FS_SCAN_PG_HOST"'
kubectl -n sam-queries exec "$POD" -- sh -c 'ps -e -o cmd | grep -c "[g]unicorn"'
```

**Definitive `statement_timeout` + replica routing** (uses the app's *own* init-warmed
engine — a bare `FS_SCANS.load()` probe does NOT attach the listener, so it would show
`statement_timeout=0`; you MUST go through `create_app`):

```bash
kubectl -n sam-queries exec -i "$POD" -- python - <<'PY' 2>&1 | grep -vE '^(INFO|WARNING|\[20|pguser:)'
from webapp.run import create_app
from sqlalchemy import text
app=create_app()
with app.app_context():
    from webapp.disk_scans.session import get_engines
    print("FS_SCAN_STATEMENT_TIMEOUT_MS =", app.config.get('FS_SCAN_STATEMENT_TIMEOUT_MS'))
    e=get_engines(app); coll=sorted(e)[0]
    with e[coll].connect() as c:
        print("statement_timeout =", c.execute(text("SHOW statement_timeout")).scalar())   # expect '100s'
        print("pg_is_in_recovery =", c.execute(text("SELECT pg_is_in_recovery()")).scalar()) # expect True (replica)
PY
```

Baseline healthcheck: `./scripts/cirrus_healthcheck.sh` (expect 0 FAIL; benign WARNs =
rollout startup-probe race + unrelated `baotoken`).

---

## 2. Re-baseline the slow path (DB vs Python split)

This is the measurement that justified gthread (DB-bound → threads help). Re-run it to
see how much the peer optimization shaved off. Goes through the real service `_scoped`.

```bash
kubectl -n sam-queries exec -i "$POD" -- python - <<'PY' 2>&1 | grep -vE '^(INFO|WARNING|\[20|pguser:)'
import time
from sqlalchemy import event
from sqlalchemy.engine import Engine
db=[0.0]; qn=[0]
@event.listens_for(Engine,"before_cursor_execute")
def _b(c,cur,s,p,ctx,m): ctx._t=time.perf_counter()
@event.listens_for(Engine,"after_cursor_execute")
def _a(c,cur,s,p,ctx,m): db[0]+=time.perf_counter()-ctx._t; qn[0]+=1
from webapp.run import create_app
app=create_app()
with app.app_context():
    from webapp.extensions import db as fdb
    from sam import Project
    from webapp.disk_scans import service
    proj=Project.get_by_projcode(fdb.session,"NRAL0002")
    mod,prefixes,collections=service._scoped(fdb.session,proj,"Campaign_Store",None)
    q=mod.FsScanQueries(filesystems=collections)
    for label,call in (("file_size_histogram",lambda:q.file_size_histogram(path_prefixes=prefixes)),
                       ("access_history",lambda:q.access_history(path_prefixes=prefixes))):
        db[0]=0.0; qn[0]=0
        t0=time.perf_counter(); r=call(); wall=time.perf_counter()-t0
        print("%s: wall=%.1fs db=%.1fs(%.0f%%) python=%.1fs queries=%d fast_path=%s"
              %(label,wall,db[0],100*db[0]/wall,wall-db[0],qn[0],(r or {}).get("fast_path")))
PY
```

**Pre-optimization baseline (2026-06-19):** `file_size_histogram` wall 61s / db 52s (85%) ·
`access_history` wall 70s / db 53s (76%); 12 queries each; `fast_path=False`. If the new
numbers are much lower, the on-the-fly cost dropped — note it and scale load up.

---

## 3. Authenticated browser (Playwright) — for load generation

Drive load from **inside** the browser context (in-page `fetch`). **Do NOT extract the
session cookie** (HttpOnly; the user declined cookie exfiltration). All load rides the
browser's existing session; only status/timings come back.

Auth flow: `browser_navigate https://samuel.k8s.ucar.edu/` → click **Login** →
Microsoft OIDC (`login.microsoftonline.com`) → **hand off to the user for password + 2FA**
→ they land authenticated. Confirm with a `browser_snapshot` (should show the dashboard,
build footer in `contentinfo`).

The disk resource-details page (fs-scans tabs live here):
`/user/resource-details/<projcode>?resource=Campaign_Store` — expand the **Filesystem
Scans** card to lazy-load the 4 tabs (Large directories / User-group / Access history /
File sizes).

---

## 4. Load generation (Playwright `browser_run_code_unsafe` → `page.evaluate`)

**Pattern:** fire-and-forget concurrent `fetch`es (the browser MCP caps tool calls at
~30s, but scans run 60–100s+), keep refs on `window.__x` so they aren't GC'd, return
quickly, then read outcomes from **server logs** (§5). Measure fast-route latency in the
same call to prove gthread non-blocking.

**Cache awareness (critical):** results are cached scan-date-keyed, 8-day TTL
(`webapp/disk_scans/cache.py`). To generate *real* DB load you need **cold keys**:
- distinct **child scopes** (each a different `scope=`), or
- distinct **`owner_uid=`** values on the same scope (distinct cache key, still a full scan), or
- **resource-wide** (`/resource/Campaign_Store/directories`) = whole filesystem, heaviest.
There is **no single-flight** — N concurrent misses on the *same* key all compute (herd).

**Wave A — gthread non-blocking** (12 slow distinct-child + fast probe interleaved):
```js
async (page) => { return await page.evaluate(async () => {
  const base=location.origin, now=()=>performance.now();
  const get=async(p,l)=>{const s=now();try{const r=await fetch(base+p,{credentials:'include',headers:{'HX-Request':'true'}});const t=await r.text();return{l,status:r.status,ms:Math.round(now()-s),bytes:t.length};}catch(e){return{l,status:'ERR',ms:Math.round(now()-s)};}};
  const kids=['NSAP0003','P48500028','P48500047','NWSA0002','NWCA0002','NRIS0001','P48503002','NRAL0003'];
  const mk=(s,k)=>`/dashboards/user/disk-scans/NRAL0002/${k}?resource=Campaign_Store&scope=${s}&target_id=t`;
  const slow=[]; kids.forEach(c=>slow.push(get(mk(c,'file-sizes'),`slow:${c}`)));
  kids.slice(0,4).forEach(c=>slow.push(get(mk(c,'access-history'),`ah:${c}`)));
  window.__A=slow; await new Promise(r=>setTimeout(r,2000));
  const fast=[]; for(let i=0;i<6;i++){fast.push(await get(`/dashboards/user/disk-scans/NMMM0003/directories?resource=Campaign_Store&scope=NMMM0003&target_id=d`,`fast#${i}`)); await new Promise(r=>setTimeout(r,800));}
  const fm=fast.map(f=>f.ms).sort((a,b)=>a-b);
  return {dispatched_slow:slow.length, fast:fast.map(f=>`${f.l}:${f.status}:${f.ms}ms`), fast_p50:fm[3], fast_max:fm[5]};
});}
```
PASS: fast probes stay ~100–400ms (200) while 12 slow scans run.

**Wave B — brutal cold-key, trip `statement_timeout`** (resource-wide + owner-varied parent):
```js
async (page) => { return await page.evaluate(async () => {
  const base=location.origin, fire=p=>fetch(base+p,{credentials:'include',headers:{'HX-Request':'true'}}).then(r=>r.text()).catch(e=>String(e));
  const w=[];
  for(let i=0;i<4;i++) w.push(fire(`/dashboards/user/disk-scans/resource/Campaign_Store/directories?target_id=t&_=${i}`));
  for(let u=9000;u<9008;u++) w.push(fire(`/dashboards/user/disk-scans/NRAL0002/access-history?resource=Campaign_Store&scope=NRAL0002&owner_uid=${u}&target_id=t`));
  for(let u=9008;u<9016;u++) w.push(fire(`/dashboards/user/disk-scans/NRAL0002/file-sizes?resource=Campaign_Store&scope=NRAL0002&owner_uid=${u}&target_id=t`));
  window.__B=w; await new Promise(r=>setTimeout(r,1500)); return {dispatched:w.length};
});}
```
If peer optimization made these too fast to trip 100s, escalate: raise concurrency
(e.g. 16→32), add more resource-wide copies, or widen the `owner_uid` range.

---

## 5. Monitoring (run in BACKGROUND after firing a wave; ~3 min)

```bash
POD=$(kubectl -n sam-queries get pod -l app=samuel -o jsonpath='{.items[0].metadata.name}')
probe(){ kubectl -n sam-queries exec -i "$POD" -- python - <<'PY' 2>/dev/null
from sam.plugins import FS_SCANS
from sqlalchemy import text
m=FS_SCANS.load(); eng=m.get_engine(sorted(m.list_pg_schemas())[0])
with eng.connect() as c:
    r=c.execute(text("SELECT count(*) FILTER (WHERE state='active'), coalesce(round(max(extract(epoch from (now()-query_start))) FILTER (WHERE state='active')),0) FROM pg_stat_activity WHERE application_name LIKE 'sam-webapp%fs_scans%'")).fetchone()
    print(f"active={r[0]} oldest={int(r[1])}s")
PY
}
for i in $(seq 1 9); do printf "t+%03ds: " $((i*20-20)); probe|grep active=||echo "?"; [ $i -lt 9 ] && sleep 20; done
echo "--- route-level scan failures (statement_timeout surfaces here) ---"
kubectl -n sam-queries logs -l app=samuel --since=6m --tail=-1 2>&1 | grep -iE 'scan failed|canceling statement|QueryCanceled|statement timeout' | tail -15
echo "--- duration histogram (seconds, app-log) ---"
kubectl -n sam-queries logs -l app=samuel --since=6m --tail=-1 2>&1 | grep -E 'webapp.run — GET /dashboards/user/disk-scans' | grep -oE '\([0-9.]+ ms\)' | tr -dc '0-9.\n' | awk '{printf "%.0f\n",$1/1000}' | sort -n | uniq -c
echo "--- 5xx / worker-timeout / restarts ---"
kubectl -n sam-queries logs -l app=samuel --since=6m --tail=-1 2>&1 | grep -ciE '→ 50[0-9]|HTTP/1.1" 50[0-9]|WORKER TIMEOUT' | xargs echo "  5xx/timeout lines:"
kubectl -n sam-queries get pods -l app=samuel -o 'custom-columns=NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount'
```

---

## 6. Pass criteria (what "healthy under load" looks like)

- Worker count stable (master + 9); **zero pod/worker restarts** across the run.
- gthread non-blocking: fast routes stay sub-second while many slow scans run.
- `statement_timeout` fires on queries >100s → `QueryCanceled`/`OperationalError`
  caught by the route's `except Exception` → **HTTP 200 + error banner, ZERO 5xx**
  (`disk_scans/routes.py` `_render_*`; `error=str(exc)`).
- Active query age never exceeds ~100s; max request ~100s for single-dominant-query scans.
- Connections recover after cancellation (no cascade; `pool_pre_ping` covers it).

**Pre-optimization baseline result (2026-06-19):** 12 concurrent slow → all 200, fast
route 114–205ms throughout. Brutal wave → ~20 `statement_timeout` cancels, **0 5xx**,
max 100.4s, 0 restarts. Route degrades gracefully. If a fresh run shows 5xx, hangs, or
restarts → regression, investigate.

---

## 7. Pitfalls / lessons (don't repeat these)

- **Don't exfiltrate the session cookie** — drive load via in-browser `page.evaluate`. (User declined cookie extraction.)
- **µs vs ms:** gunicorn access-log durations end in `…µs` (micro). `196866µs` = 0.2s, NOT 196s. Use the app-log `webapp.run — … (NNN ms)` lines for human-readable durations.
- A bare in-pod `FS_SCANS.load()` engine has **no** `statement_timeout`/`application_name` (the listener is attached only inside `init_fs_scans`). Verify via `create_app` (§1).
- `statement_timeout` is **per-query**, not per-request: a request issuing several sub-100s queries can sum past 100s (saw 115s once), bounded then by the 120s gunicorn worker timeout. The >120s-under-gthread path was never reached.
- Browser MCP tool calls cap ~30s → fire-and-forget, monitor server-side.
- The cache has **no single-flight** → identical-key concurrency stampedes (proposed follow-up; see §6 of K8S_DEPLOYMENT_HARDENING.md). For load you generally WANT cold keys anyway.

---

## 8. Key facts

- Resource: **Campaign_Store** (disk). Disk-details page: `/user/resource-details/<projcode>?resource=Campaign_Store`.
- **Fast** scope (fast-path, sub-second): **NMMM0003** (`/gpfs/csfs1/mmm`).
- **Slow** on-the-fly scope (spans `ncar`+`ral` collections): **NRAL0002**.
- NRAL0002 child scopes (distinct cold keys): `NSAP0003 P48500028 P48500047 NWSA0002 NWCA0002 NRIS0001 P48503002 NRAL0003` (P48500028 = `/ral/hap`, biggest ~4PB).
- Fragment endpoints: `/dashboards/user/disk-scans/<projcode>/{directories,entities,access-history,file-sizes}?resource=Campaign_Store&scope=<scope>[&owner_uid=<uid>]&target_id=t`
- Resource-wide (needs `VIEW_ALL_FILESYSTEM_DATA`; Ben has it): `/dashboards/user/disk-scans/resource/Campaign_Store/directories`
- 13 collections: acom asp cesm cgd cisl collections eol hao mmm ncar ral univ uwyo.
