---
name: watch-tomcat
description: >-
  Watching legacy SAM (the Java/Tomcat app on sam-tomcat behind sam.ucar.edu)
  while its surfaces move to SAMuel. Load before running
  scripts/tomcat_watch.sh to read its health lines, the caller and cutover
  lines that track legacy's API traffic falling, the daily trend, and an
  --at window lined up with a prod or ingress incident.
---

# Watch tomcat

The legacy counterpart of `watch-prod`, and as thin: the reading rules for a
tick (report only what changed, escalate a trend not an outlier, known-open is
not re-flagged) are `watch-prod` §1-§3. This skill carries what is specific to
legacy. Its first job is the **cutover**: legacy's API traffic, family by
family, should only fall as ports land and callers repoint. AMIE is out of
scope and ignored by path (`/api/protected/amie/`): it is not ported and will be
rebuilt on a new protocol if it is ever needed.

## 1. Run a tick

```bash
scripts/tomcat_watch.sh                       # one tick, the last 35 min
scripts/tomcat_watch.sh --at 2026-10-10T03:26Z --span 10m   # a past window, centered
scripts/tomcat_watch.sh --trend 14            # the per-family daily table
scripts/tomcat_watch.sh --backfill            # seed the history from 30 days of logs, once
scripts/tomcat_watch.sh --daily 20261009      # one day's cutover line
```

Prerequisites: the VPN, an ssh master to the host, and (for the `http:` line)
`SAM_LEGACY_PROBE_USER/PASS` in the environment or the repo `.env` (the admin
key). The target is `--host` or `TOMCAT_HOST`, default `sam-tomcat.ucar.edu`.
Any ssh alias works: with `Host sam-tomcat` plus `HostName sam-tomcat.ucar.edu`,
a `ControlPath` built on `%h` expands to the HostName, so the alias shares the
existing master socket. ssh runs with `BatchMode`, so an expired master reads as
`OFFLINE:` (exit 0) and names the fix: `ssh <host> true` once, with Duo.

One ssh call per tick sends `scripts/lib/tomcat_log_summary.sh` to the host,
which aggregates there and returns tab-separated records;
`scripts/lib/tomcat_report.py` turns them into lines. Exit 0 quiet / 1 warn / 2 fail.

## 2. The lines

All times are UTC, taken from each request's own bracketed time and offset, so
they line up with `cirrus_watch.sh` and the ingress probe reports.

| Line | Reads | Flags |
|---|---|---|
| `http:` | one authenticated `GET /api/protected/admin/ldapsync/groupTag` (220 B, ~0.2 s on 2026-10-10), user agent `sam-tomcat-watch/1` | FAIL 5xx or no answer; WARN non-200 or > 4 s |
| `host:` | uptime, JVM pid and start, `/tomcat` use, newest `catalina.out.<stamp>` | WARN `tomcat restarted` (JVM start moved > 5 min); WARN `/tomcat` ≥ 85 %; FAIL no java |
| `web:` | API calls by status class, p50/p95 (bucketed, 2xx/3xx only), JSF `app`, `saml`, `other`, and `scan` (non-`protected` `/api/` paths: scanners) | FAIL a 5xx to any caller but `self`/`watch` |
| `callers:` | authenticated calls per map label, then the same Mountain wall-clock window a week ago in parentheses | see below |
| `slow:` | up to 3 API calls over 4 s, with UTC time and label | info |
| `errors:` | `sam.log` ERROR and `catalina.out` SEVERE in the window, top classes with first/last time | WARN > 5 in the window; WARN a class not seen before |
| `probe:` | the per-minute `GET /` health check (user agent `libwww-perl`; someone else's) | WARN silent > 5 min |
| `cutover` | on the first tick of a Mountain day: yesterday per status group and the top families, against the same weekday a week earlier | info: the trend is the point |

`callers:` flags: **NEW** for an unmapped client that got past authentication
(prints client, user agent and families: label it); **MISSING** when a label had
≥ 5 calls in last week's window and none now, 07:00-19:00 Mountain; **refused**
when a mapped caller gets 401/403 (key revoked or rotated); **FAIL** for any
authenticated `/api/xras/` call (XRAS cut over 2026-08-24); WARN for a `retired`
family answering. A 401 from an unmapped client is a scanner and is never NEW.

Volume is weekday-shaped, which is why every comparison is the same weekday.
Baselines (2026-10-02 to 10-09): `ported` ~950-1,130 calls a day (ssg
fairShareTree ~500, wallClockExemption ~300, queue ~200, ldapsync 5-70),
`porting` (HEUV) ~30-60 on weekends, ~140-170 on weekdays. `sam.log` logged 27
ERRORs on Saturday 10-10 (NPEs behind `ApiController`), so the window threshold
is the signal, not a daily count.

## 3. Families, the caller map, the history

The family table (prefix → `ported` / `porting` / `cutover` / `retired` /
`other`) is code: `FAMILIES` in `scripts/lib/tomcat_report.py`. When a port
lands or a family is retired, edit its line there in the same PR.

Everything else is local, under `${XDG_STATE_HOME:-~/.local/state}/sam-watch/`,
never committed: `tomcat-callers` (the map), `tomcat-daily.tsv` (one row per
day, family and label: `date family label calls 5xx refused`; it outlives the
host's 30 days of logs), `state-tomcat` (JVM start, seen error classes) and
`known-open-tomcat` (printed back like the others).

Map lines are `label family-prefix client ua-prefix`, whitespace-separated,
`#` comments, first match wins; the client matches exactly unless it carries a
glob. Two labels are special: `self` (Ben's own parity and shadow runs) and
`watch` (this tick's probe) are shown but never gate and never count toward the
cutover totals. A missing map is not an error: every authenticated client prints
as NEW, which is how the map is seeded. Label service hosts only; a residential
address is a person, not a caller.

## 4. Correlating with our own timelines

- `--at <UTC> --span 10m` reads any window in the last 30 days, centered on the
  time given, without writing state. Use it for an incident logged in
  `known-open`: a legacy that stayed quiet through an ingress episode puts the
  fault on the nwc1 side.
- The local ingress probe already uses `sam.ucar.edu` as its **control** host. A
  control failure in a probe report plus a silent `probe:` line or a restart on
  this tick is legacy, not the network.
- `cutover` and `--trend` are what to quote when a caller repoints: the family's
  row should fall to the `self` noise floor within a day.

## 5. Traps

- **Never open the two files under the system Tomcat log directory**
  (`tomcat-sam_access.log`, `tomcat-sam_catalina.log`): they are named pipes into
  syslog and a read steals their entries. The reader touches only
  `/tomcat/tomcat-sam/logs`; a test fails if a script names the pipe directory.
- Today's log is plain (`access.log-YYYYMMDD`); earlier days are `.gz` and kept
  30 days. Older history comes from `tomcat-daily.tsv` or a snapshot.
- Log times are Mountain; the reader converts with each line's own offset.
- `GET ldapsync/status` always answers 500 on legacy (a broken query); it shows
  up only from `self`, which never gates.
- `sam.log` mails every ERROR to sweg-notify already; the watch counts, it does
  not need to page.
- A request is logged about 10 s late (syslog), so the tick's own `http:` hit
  appears in the next tick's window.

## 6. Timer

No second timer. Per `watch-prod` §7, the prod timer's prompt gains one line,
`scripts/tomcat_watch.sh`, after the dev tick, reported as one line when quiet.
