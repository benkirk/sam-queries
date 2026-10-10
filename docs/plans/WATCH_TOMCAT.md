# watch-tomcat: a read-only tick for legacy SAM on sam-tomcat

**Status:** built 2026-10-10 on branch `watch-tomcat` (one PR to staging). Planned the same day
(Ben + Claude) as "watch-legacy"; renamed before the PR. Open items at the end.

## Context

Legacy SAM (Java/Spring, Tomcat 9 on `sam-tomcat.ucar.edu`, behind the `sam.ucar.edu` VIP) still
serves callers while surfaces move to SAMuel: the ssg/sysacct/dasg feeds (ported, callers not
repointed), ldapsync (ported #772/#785, the daemon still on legacy, dev shadow running), HEUV (port on
#786), and the JSF GUI + SAML login. XRAS cut over 2026-08-24, so any legacy XRAS call is a regression.
Ben wants three things: is legacy **healthy**, can its events be **lined up with our own timelines**,
and above all **its API traffic, family by family, falling** as ports land and callers repoint.
AMIE is out of scope: ignored by path; if it is ever needed it will be rebuilt on a new protocol.

Decisions (Ben, 2026-10-10): on demand plus one line in the existing prod timer prompt (no second
timer); the caller map and the daily history are local only; one authenticated outside-in probe.

## As built

| Path | Role |
|---|---|
| `scripts/tomcat_watch.sh` | the tick, `--at/--span`, `--daily`, `--backfill`, `--trend`, `--host` |
| `scripts/lib/tomcat_log_summary.sh` | the remote reader, sent with `ssh HOST bash -s -- ARGS < reader` |
| `scripts/lib/samapp_summary.sh` | the sam-app reader (the syncd client), a second ssh call |
| `scripts/lib/tomcat_report.py` | stdlib Python: records to lines, the `FAMILIES` status table, the history |
| `tests/unit/gates/test_watch_tomcat_reader.py` | fixture-tree tests for all three |
| `.claude/skills/watch-tomcat/SKILL.md` | how to read it; thresholds; the map format; traps |

Local, under `${XDG_STATE_HOME:-~/.local/state}/sam-watch/`: `state-tomcat`, `known-open-tomcat`,
`tomcat-callers` (the map), `tomcat-daily.tsv` (history that outlives the host's 30 days of logs).

## What the live check of the host changed in the plan (2026-10-10)

1. **ssh target.** `ssh -o BatchMode=yes sam-tomcat` is denied; the open ControlMaster socket is keyed
   on `%h` = `sam-tomcat.ucar.edu`. Default `TOMCAT_HOST=sam-tomcat.ucar.edu`, overridable; an alias with
   `HostName sam-tomcat.ucar.edu` shares the socket. An expired master reads as OFFLINE with the fix.
2. **AMIE by path** (`api/protected/amie/`), never by IP: sam-app 128.117.177.140 also runs ldapsync.
3. **Callers are authenticated `api/protected/*` calls only.** Most distinct `/api/*` paths are
   scanners, and AWS addresses reach `api/protected/admin/ssg` with 401. Other `/api/` paths count
   as `scan`; a 401 from an unmapped client is never NEW; a 401/403 to a mapped caller is a WARN.
4. **XRAS by path** (`/api/xras/`): a `grep xras` is swamped by AMIE `task/AMIE/XRAS-*` lines. 0 hits
   in the 30 retained days.
5. **UTC from the bracketed request time and its offset**, not the syslog prefix (about 10 s later). The
   reader does the epoch math in plain awk, so it runs under BWK awk (macOS, tests) and gawk (host).
6. **Error baseline**: 27 `sam.log` ERRORs on Saturday 10-10, against "0-8/day" assumed earlier.
7. **The `GET /` prober** is pinned by user agent (`libwww-perl`); other root hits are web traffic.
   Its path is `/` today (`//` in June); both match.

## Traffic at build time (authenticated, `self` excluded)

| family | status | per day, 10-02..10-09 |
|---|---|---|
| `ssg/fairShareTree` | ported | 471-559 |
| `ssg/wallClockExemption` | ported | 283-317 |
| `ssg/queue` | ported | 187-208 |
| `ldapsync/*` | ported | 5-71 (the sam-app daemon) |
| `sysacct/directoryaccess` | ported | 1-7 |
| `heuv/{user,report,search}` | porting | 31-58 weekends, 142-172 weekdays |
| `xras/*` | cutover | 0 |

The `ported` row (~1,000-1,100 a day, nearly all the ssg pollers on 128.117.211.140/.141/.150 and
others) is the biggest repoint target: SAMuel already serves every one of those routes.

## Correlation

`--at <UTC> --span 10m` reads any retained window without writing state. For LB-path incident #7
(2026-10-10 03:25-03:28Z) legacy showed nothing: the ssg poller and the `GET /` prober ran
normally, so the episode stayed on the nwc1 side. The local ingress probe already uses `sam.ucar.edu`
as its control host; the skill says how to read the two together.

## sam-app, the syncd client (added the same day)

`ldapsync/sam-app` (128.117.177.140) is `sam-ldap-syncd` in George's rootless podman zoo on
`sam-app.ucar.edu`. As `benkirk` (group `swes`, no 2FA hop) the tick can see `ps` (slapd, `syncd -vvvvv
--redirect`, `ldif_log_processor.sh`; the whole zoo restarted 2026-10-09 21:41Z after George's
`sam-ldap-syncd` 5f216b2 "cron config" and `sam-app` 718fc28), the deployed HEAD of every repo in `~swes`,
and the syncd's dated logs (`log-YYYY-MM-DD-syncd.e`, `2026-10-10T07:11:32-06  msg`). It cannot see
`podman ps`/`logs`, `systemctl --user`, the swes crontab or slapd's log (those need `swes`, 2FA).
amie-sam-mediator and acct-sam-etl are not running by design. `scripts/lib/samapp_summary.sh` reads only
the first set; the tick prints `app:`, `zoo:` (on a HEAD change) and `syncd:`. Verified live: the
reader's PUT count for 10-10 (33) matches `grep -c 'status:'` on that day's log.

**A note for George (no contents read):** several `.env` files under `~swes/*/prod/` are group- or
world-readable (644/664), readable by any login on the host; the secrets proper under
`/data/tomcat-sam/secrets/` are 600. The watch never opens either; the modes are worth tightening.

## Open items

- `SAM_LEGACY_PROBE_USER/PASS` (the admin key) is set in Ben's `.env` (the tick also reads the parent
  directory's `.env`, as a worktree has none). `GET /api/protected/admin/ldapsync/groupTag`: 200, 220 B,
  ~0.2 s; its hits are labeled `watch` by the user agent.
- Ben reviews the proposed `tomcat-callers` map (seeded from the repo's known callers and the live
  logs; `self` = 128.117.65.3).
- After merge: Ben approves replacing the prod timer's prompt with one that adds
  `scripts/tomcat_watch.sh` after the dev tick.
- When #786 merges, `heuv/` moves from `porting` to `ported` in `FAMILIES`.
