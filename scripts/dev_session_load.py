#!/usr/bin/env python3
# dev_session_load.py -- concurrent HTTP load driver for samuel-dev's
# authenticated (@login_required) surface, replaying a saved OIDC session.
#
# Reads a Playwright storage_state.json (cookies from one real Entra login --
# capture it with dev_capture_session.py) and replays it at concurrency against
# a ranked list of session-only dashboard / rollup routes. It measures only the
# client side: latency percentiles, throughput, status codes. Server-side
# attribution (cpu=/sam=/rm=) comes from the pod logs via `cirrus_watch.sh
# --env dev`, correlated by the X-Request-ID the driver records per request.
#
# Guardrails: refuses any base host that is not samuel-dev unless --allow-nondev
# is given (never hammer prod), and never requests the login / OIDC paths. The
# session cookie is a credential -- keep the storage-state file out of the repo.
#
# Usage:
#   dev_session_load.py --storage-state FILE [--target NAME ... | --all]
#       [--clients N] [--duration S | --count N] [--base URL]
#       [--projcode P] [--username U] [--resource R] [--vary]
#       [--csv FILE] [--list] [--allow-nondev]
#
# Exit codes: 0 ok / 1 some requests failed / 2 precondition failed.

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

try:
    import requests
except ImportError:
    sys.exit("requests not found; run inside the conda env (source etc/config_env.sh)")

DEFAULT_BASE = "https://samuel-dev.k8s.ucar.edu"

# name -> path template. GET-only, session-only routes (the collector API key
# cannot reach these). {projcode}/{username}/{resource}/{date} fill from flags.
# The rd_* group is the uncached resource-details tree/plugin surface: every hit
# pays the full subtree walk (no route cache), so single-request latency is the
# interactive cost. Disk group: --projcode <disk-tree> --resource Campaign_Store.
TARGETS = {
    "allocations_projects": "/allocations/projects",
    "charges_summary":      "/api/v1/projects/{projcode}/charges/summary",
    "project_allocations":  "/api/v1/projects/{projcode}/allocations",
    "resource_details":     "/user/resource-details/{projcode}?resource={resource}",
    "rd_usage_chart":       "/user/resource-details/usage-chart/{projcode}?resource={resource}",
    "rd_user_pie":          "/user/resource-details/user-pie/{projcode}?resource={resource}",
    "rd_disk_chart":        "/user/resource-details/disk-usage-chart/{projcode}?resource={resource}",
    "rd_user_subtree":      "/user/resource-details/user-subtree/{projcode}?resource={resource}&username={username}",
    "rd_day_subtree":       "/user/resource-details/day-subtree/{projcode}?resource={resource}&date={date}",
    # jobs card + a chart: the jobhistory plugin, embedded lazily on the HPC
    # resource-details page. days=365 over a wide tree is the heavy one.
    "jobs_card":            "/dashboards/user/jobs/{projcode}/card?machine={machine}&cid=jobs-hist&tablist_id=jobsCardTabs&scope={projcode}&days={days}",
    "jobs_by_user":         "/dashboards/user/jobs/{projcode}/by-user?machine={machine}&target_id=t&scope={projcode}&days={days}",
    "user_tree":            "/user/tree/{projcode}",
    "user_accounts":        "/user/accounts",
    "admin_projects":       "/admin/projects",
}
DEFAULT_TARGETS = ["allocations_projects", "charges_summary",
                   "project_allocations", "resource_details"]


def load_cookies(session, path, host):
    """Set cookies from a Playwright storage_state.json onto *session*."""
    with open(path) as fh:
        state = json.load(fh)
    cookies = state.get("cookies", [])
    if not cookies:
        sys.exit(f"{path} carries no cookies -- recapture the session")
    n = 0
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".")
        if dom and dom not in host and host not in dom:
            continue
        session.cookies.set(c["name"], c["value"],
                            domain=c.get("domain", host), path=c.get("path", "/"))
        n += 1
    if not n:
        sys.exit(f"no cookies in {path} match host {host}")
    return n


def percentile(sorted_ms, q):
    if not sorted_ms:
        return 0.0
    i = min(len(sorted_ms) - 1, int(round(q / 100.0 * (len(sorted_ms) - 1))))
    return sorted_ms[i]


def worker(session, url, deadline, max_count, counter, out):
    while time.monotonic() < deadline and (max_count is None or counter[0] < max_count):
        counter[0] += 1
        t0 = time.perf_counter()
        try:
            r = session.get(url, timeout=60, allow_redirects=False)
            dt = (time.perf_counter() - t0) * 1000.0
            out.append((r.status_code, dt, r.headers.get("X-Request-ID", "-")))
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            out.append((-1, dt, type(e).__name__))


def run_target(name, url, args):
    session = requests.Session()
    host = urlsplit(args.base).netloc
    load_cookies(session, args.storage_state, host)
    results = []
    counter = [0]
    deadline = time.monotonic() + (args.duration if args.count is None else 1e9)
    max_count = None if args.count is None else args.count
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        futs = [pool.submit(worker, session, url, deadline, max_count, counter, results)
                for _ in range(args.clients)]
        for f in as_completed(futs):
            f.result()
    wall = time.monotonic() - t0
    return summarize(name, url, results, wall)


def summarize(name, url, results, wall):
    lat = sorted(dt for _s, dt, _x in results)
    codes = {}
    errs = []
    for s, dt, x in results:
        key = s if s > 0 else x
        codes[key] = codes.get(key, 0) + 1
        if s < 0 or s >= 500:
            if len(errs) < 5:
                errs.append((s, round(dt), x))
    n = len(results)
    ok = sum(v for k, v in codes.items() if isinstance(k, int) and 200 <= k < 400)
    print(f"\n== {name}  {url}")
    print(f"   {n} req in {wall:.1f}s = {n / wall:.1f} req/s  |  ok={ok}")
    print(f"   p50 {percentile(lat, 50):.0f} / p95 {percentile(lat, 95):.0f} / "
          f"p99 {percentile(lat, 99):.0f} ms")
    print(f"   status: " + ", ".join(f"{k}:{v}" for k, v in sorted(codes.items(), key=str)))
    for s, dt, x in errs:
        print(f"     ! {s} {dt}ms {x}")
    return {"target": name, "url": url, "n": n, "wall": wall, "ok": ok,
            "codes": codes, "results": results}


def main():
    p = argparse.ArgumentParser(description="Concurrent load driver for samuel-dev's authenticated surface.")
    p.add_argument("--storage-state", default=os.getenv("SAM_E2E_STORAGE_STATE"),
                   help="Playwright storage_state.json (or $SAM_E2E_STORAGE_STATE)")
    p.add_argument("--base", default=os.getenv("SAM_API_BASE", DEFAULT_BASE))
    p.add_argument("--target", action="append", choices=list(TARGETS), help="repeatable")
    p.add_argument("--all", action="store_true", help="every target")
    p.add_argument("--clients", type=int, default=8)
    p.add_argument("--duration", type=float, default=30.0, help="seconds (ignored with --count)")
    p.add_argument("--count", type=int, help="total requests per target instead of --duration")
    p.add_argument("--projcode", default="SCSG0001")
    p.add_argument("--username", default="benkirk")
    p.add_argument("--resource", default="Derecho")
    p.add_argument("--date", default=None,
                   help="YYYY-MM-DD for rd_day_subtree (default: 3 days ago)")
    p.add_argument("--machine", default="derecho", help="jobs_* machine (lowercase)")
    p.add_argument("--days", type=int, default=365, help="jobs_* window in days")
    p.add_argument("--vary", action="store_true", help="append a cache-buster to defeat per-user HTML cache")
    p.add_argument("--csv", help="write raw per-request rows here")
    p.add_argument("--list", action="store_true", help="print the target table and exit")
    p.add_argument("--allow-nondev", action="store_true", help="permit a non-samuel-dev base")
    args = p.parse_args()

    if args.list:
        for k, v in TARGETS.items():
            print(f"{k:22} {v}")
        return 0

    args.base = args.base.rstrip("/")
    host = urlsplit(args.base).netloc
    if "samuel-dev" not in host and not args.allow_nondev:
        sys.exit(f"refusing base {args.base!r} (not samuel-dev); pass --allow-nondev to override")
    if not args.storage_state or not os.path.exists(args.storage_state):
        sys.exit("--storage-state FILE is required (capture it with dev_capture_session.py)")

    from datetime import date, timedelta
    day = args.date or (date.today() - timedelta(days=3)).isoformat()

    names = list(TARGETS) if args.all else (args.target or DEFAULT_TARGETS)
    runs = []
    for name in names:
        path = TARGETS[name].format(projcode=args.projcode, username=args.username,
                                    resource=args.resource, date=day,
                                    machine=args.machine, days=args.days)
        if path.startswith("/auth"):
            sys.exit("refusing to drive an auth path")
        url = args.base + path
        if args.vary:
            url += ("&" if "?" in url else "?") + f"_v={int(time.time()*1000)}"
        runs.append(run_target(name, url, args))

    if args.csv:
        with open(args.csv, "w") as fh:
            fh.write("target,status,latency_ms,x_request_id\n")
            for r in runs:
                for s, dt, x in r["results"]:
                    fh.write(f"{r['target']},{s},{dt:.1f},{x}\n")
        print(f"\nwrote {args.csv}")

    failed = sum(r["n"] - r["ok"] for r in runs)
    print(f"\ntotal non-2xx/3xx: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
