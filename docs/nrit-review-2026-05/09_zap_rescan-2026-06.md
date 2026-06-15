# ZAP re-scan — 2026-06-15 (post-hardening verification)

**Purpose:** verify that the browser-defense findings from the original NRIT ZAP
scan (`zap-basic-report.html`, base commit `b166d9b`, 2026-05) were actually
closed by the hardening that has since landed in `main` — CSP enforce-by-default
(#303), the vendored-asset / SRI registry and security-header + CSRF work (#296),
and RBAC hardening (#308). This closes the loop opened in
`docs/plans/implemented/PRODUCTION_IMPROVEMENTS.md` ("re-run David's authenticated
ZAP script to confirm header/CSRF deltas").

## How this was run

Dockerized, no native ZAP install:

```bash
docker pull ghcr.io/zaproxy/zaproxy:stable   # one-time
./scripts/zap_probe_docker.sh                # passive baseline (default)
```

`scripts/zap_probe_docker.sh` spins up a throwaway `webdev` container with dev
auto-login (`DISABLE_AUTH=1 DEV_AUTO_LOGIN_USER=benkirk`, runtime-only) on port
5051, confirms `/admin/` serves 200 (so an unauthenticated spider reaches the
**authenticated** app), runs `zap-baseline.py -c gen.conf` against it, writes a
timestamped report, and tears the container down. See the script header for
`--full` (active scan, local-only) and `-t URL` (external target) modes.

- **Scanner:** `zap-baseline.py` (spider + AJAX spider + passive rules) — same
  *kind* of scan as the original NRIT pass.
- **Coverage:** 118 URLs, authenticated as an ADMIN user (broader than the
  original).
- **Result line:** `FAIL-NEW: 0  WARN-NEW: 12  PASS: 55`.

> Reports (`zap-baseline-rescan-*.html/.json`, `zap.yaml`) are git-ignored; this
> note is the committed record. Re-run any time to refresh.

## Diff vs the 2026-05 baseline

### Resolved ✅

| Original finding | Risk | Now |
|---|---|---|
| Content Security Policy (CSP) Header Not Set | Medium | **PASS** |
| Missing Anti-clickjacking Header | Medium | **PASS** (CSP `frame-ancestors`) |
| Sub Resource Integrity Attribute Missing | Medium | **PASS** (vendored assets + SRI) |
| X-Content-Type-Options Header Missing | Low | **PASS** |
| Cross-Domain JavaScript Source File Inclusion | Low | **PASS** (CDNs retired) |
| Cross-Origin-Embedder-Policy Missing | Low | gone |
| Cross-Origin-Opener-Policy Missing | Low | gone |
| Big Redirect Detected | Low | gone |
| Absence of Anti-CSRF Tokens | Medium* | **PASS** (*flagged in the manual run; CSRF now enforced) |

All three original **Medium** findings are closed, plus five of the seven Lows.

### Still open ⚠️ (genuine, low-risk, easy)

| Finding | Risk | Note |
|---|---|---|
| Cross-Origin-Resource-Policy Header Missing | Low | not emitted by `utils/security_headers.py` |
| Permissions-Policy Header Not Set | Low | not emitted by `utils/security_headers.py` |

Both are defense-in-depth headers. Fix is a two-line addition to the
`after_request` block in `src/webapp/utils/security_headers.py` (e.g.
`Cross-Origin-Resource-Policy: same-origin` and a minimal `Permissions-Policy`),
mirroring how `X-Content-Type-Options` / `Referrer-Policy` are already set there.

### New, introduced by the hardening itself

| Finding | Risk | Note |
|---|---|---|
| CSP: `style-src 'unsafe-inline'` | Medium | A byproduct of *having* a CSP now. `utils/csp.py` allows `style-src 'self' 'unsafe-inline'`; ZAP flags the `unsafe-inline`. Known tradeoff (inline styles still in templates). Tightening would require hashing/nonces or extracting inline styles — track separately, not a regression. |

### Noise / dev-only (no action)

- **Server Leaks Version Information ("Server" header)** — the Werkzeug dev
  server leaks its version; prod serves via gunicorn. Dev artifact only.
- **Information Disclosure – Sensitive Information in URL** — the admin
  *edit* forms submit via GET, so field values land in the query string (and
  thus referrer/logs). Mostly the spider exercising forms, but worth a glance:
  consider POST for the admin edit forms if any carry sensitive values.
- Suspicious Comments, Timestamp Disclosure, Dangerous JS Functions (in the
  vendored `htmx` lib), Modern Web Application, Session Management, cacheability
  — standard passive-scan noise.

## Caveats

- **HSTS could not be verified here.** ZAP only evaluates Strict-Transport-Security
  over HTTPS; the local scan is HTTP, so rule 10035 auto-passes. `security_headers.py`
  gates HSTS on `SESSION_COOKIE_SECURE` (prod-only). Verify HSTS on the live
  HTTPS site.
- **This was a local scan.** It proves the code path emits the right headers; it
  does not prove the prod deployment does. The **live authenticated** scan of
  `samuel.k8s.ucar.edu` still needs the native `run-zap-manual-explore.sh`
  (Docker on macOS can't share the VPN or do the 2FA login) — recommended as a
  post-deploy confirmation.

## Bottom line

The post-NRIT hardening verifiably closed every Medium browser-defense finding
and most of the Lows. Two trivial defense-in-depth headers (CORP, Permissions-
Policy) and one CSP-tightening item (`style-src 'unsafe-inline'`) remain as
optional follow-ups.
