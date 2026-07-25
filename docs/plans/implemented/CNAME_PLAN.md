# Add `sam.hpc.ucar.edu` as the user-facing hostname for SAM

## Context

SAM is deployed to CIRRUS k8s and currently served only at the
platform-provisioned name **`samuel.k8s.ucar.edu`** (CIRRUS restricts
ingress names to the `.k8s.ucar.edu` subdomain). We control the
`hpc.ucar.edu` zone and want **`sam.hpc.ucar.edu`** to be the single,
friendly, *advertised* name — and to remain in the browser address bar
when users navigate (`sam.hpc.ucar.edu/user`, etc.).

**Decisions (confirmed with user):**
- Both hostnames serve the same deployment **in parallel**; only
  `sam.hpc.ucar.edu` is communicated to users. `samuel.k8s.ucar.edu`
  stays as the platform/automation/health-check alias.
- Target visibility is **external/public** for both (the user is moving
  `samuel.k8s.ucar.edu` to public shortly; `sam.hpc.ucar.edu` follows).

**Why a DNS record alone is not enough.** A DNS CNAME is invisible to
the browser — it keeps the typed name (`sam.hpc.ucar.edu`) in the URL
bar and sends it as both the TLS **SNI** and the HTTP **`Host`** header.
The current ingress only knows `samuel.k8s.ucar.edu` and only holds a
cert for that name, so a raw CNAME yields a **cert mismatch + ingress
404**. The work is to teach the ingress the new host, get it a cert, and
register the new OIDC callback. **No application code changes** — auth is
already hostname-agnostic via `ProxyFix` (`src/webapp/run.py:67`).

## DNS approach

Use a **CNAME**: `sam.hpc.ucar.edu` → `samuel.k8s.ucar.edu` (in the
`hpc.ucar.edu` zone we control). Consequence: public resolution follows
the chain to `samuel.k8s.ucar.edu`, so that name must be publicly
resolvable/reachable too — acceptable since we only *advertise*
`sam.hpc`. (Alternative — an A record pointing `sam.hpc` straight at the
external ingress LB — would let `samuel.k8s` stay private, but needs a
stable LB target from NRIT and fights the platform's `.k8s.ucar.edu`
automation; not recommended unless a hard requirement emerges.)

## Findings from the 2026-07-22 investigation (supersede parts of this doc)

1. **Ingress admission: CONFIRMED OK.** `kubectl apply --dry-run=server` of an
   Ingress with `host: sam.hpc.ucar.edu` on `nginx-external` in `sam-queries`
   is **accepted** (a control run with the existing host was rejected for a
   *different* reason — duplicate host/path — proving the webhook chain ran).
   The CIRRUS team confirmed: *"No CIRRUS concerns, we do this with
   gdex.ucar.edu."* So `values.yaml`'s "must end in .k8s.ucar.edu" is a
   convention, not enforced policy.
2. **`hpc.ucar.edu` is issuable: CONFIRMED.** Sysadmins: *"the same certinext
   acme that the rest of ucar uses works fine for .hpc."*
3. **The ACME mechanism is domain delegation, not a solvable challenge** —
   this corrects Prerequisite 1 as originally written. The live Order shows
   `initialState: valid` with an unexercised `sectigo-email-01` challenge; no
   HTTP-01/DNS-01 ever ran. Issuance therefore never depended on DNS existing.
4. **`gdex` is a working precedent on our own ingress IP** (`128.117.41.126`):
   `gdex.ucar.edu` CNAMEs to `gdex.k8s.ucar.edu` and is served by one multi-SAN
   cert — `SAN: gdex.k8s.ucar.edu, api.gdex.ucar.edu, gdex.ucar.edu`. Exactly
   the shape implemented here.
5. **`visibility` is already `external`** — the "was 'internal'" note below is
   stale.
6. **Watch item:** on 2026-07-22 the issuer's ACME account returned
   `401 ... The account is currently suspended` for *every* order, including
   one for the already-delegated `samuel.k8s.ucar.edu`. Account-wide and
   unrelated to the new hostname. `incommon-cert-samuel` renews 2026-08-05 and
   expires 2026-10-10, so this is a production concern in its own right if it
   recurs.

## Prerequisites (external — verify/request before deploying)

1. ~~**CIRRUS / NRIT platform check**~~ — **RESOLVED**, see findings 1-4 above.
2. **Entra app registration** (file UCAR IT ticket — see
   `docs/AUTHENTICATION.md:361`): add to the existing app
   - reply URL `https://sam.hpc.ucar.edu/auth/oidc/callback`
   - post-logout redirect URI `https://sam.hpc.ucar.edu/`
   Keep the existing `samuel.k8s.ucar.edu` URLs registered too (parallel).
3. **DNS record** in `hpc.ucar.edu`: CNAME `sam.hpc.ucar.edu` →
   `samuel.k8s.ucar.edu`.

## Implementation changes (in-repo)

### 1. Ingress — serve both hosts with one multi-SAN cert
`helm/templates/ingress.yaml` — generalize the single hardcoded host
into a list so the chart renders a `rules:` entry per host and lists all
hosts in one `tls:` block (single multi-SAN secret; cert-manager reissues
to cover both names).

`helm/values.yaml` (the `webapp.tls` block, ~line 66) — keep
`fqdn: samuel.k8s.ucar.edu` as the primary and add the extra host, e.g.:
```yaml
webapp:
  tls:
    fqdn: samuel.k8s.ucar.edu        # platform primary (keep)
    extraHosts:                       # NEW — advertised alias(es)
      - sam.hpc.ucar.edu
    secretName: incommon-cert-samuel  # one multi-SAN secret covers both
  ingress:
    visibility: external              # was 'internal' — public target
```
Template both `tls[].hosts` and the `rules` list over
`[.fqdn] + .extraHosts`. `values-local.yaml` keeps its single `localhost`
host (empty `extraHosts`), so local dev is unaffected.

### 2. OIDC — make the redirect per-host (critical for parallel hosts)
`helm/values.yaml:143` — **remove** the static
`OIDC_REDIRECT_URI: "https://samuel.k8s.ucar.edu/auth/oidc/callback"`
(or set it empty). With it hardcoded, a user who starts login on
`sam.hpc` is bounced to the `samuel.k8s` callback — a *different origin*
where the PKCE/state session cookie doesn't exist → callback fails.
`src/webapp/auth/blueprint.py:130` already falls back to
`url_for('auth.oidc_callback', _external=True)`, which `ProxyFix`
(`x_host`/`x_proto`) resolves to **whichever host the user is on**. Both
callback URLs are registered in Entra (prereq 2), so login works on
either name. Logout already uses `url_for(..., _external=True)` and needs
no change.

### 3. Health check — probe both hosts
`scripts/cirrus_healthcheck.sh:38` hardcodes
`INGRESS_HOST="samuel.k8s.ucar.edu"`. Add an optional `--ingress-host`
override (mirroring the existing `--namespace/--release/--context`
flags) and/or loop the Section 7 edge-header probe (HSTS, CSP, CORP,
Permissions-Policy, etc., ~lines 643-668) over both hosts so the new
name's TLS + headers are verified post-deploy.

### 4. (Optional) Canonical-host redirect
To make "users only ever see `sam.hpc`" strictly true, 301-redirect any
request whose `Host` is `samuel.k8s.ucar.edu` to the same path on
`sam.hpc.ucar.edu`. Cleanest as an ingress-level
`nginx.ingress.kubernetes.io/...` rule on a `samuel.k8s`-only ingress; an
app-level `before_request` is the fallback. Defer unless desired — it is
not required for correctness.

### 5. Docs
Update host references in `docs/README-k8s.md`, `docs/AUTHENTICATION.md`,
and `.env.example` to note `sam.hpc.ucar.edu` is the advertised name and
`samuel.k8s.ucar.edu` the platform alias. (Test/util references in
`tests/unit/test_oidc_auth.py` and `utils/parity/*` can stay on the k8s
name — they exercise config plumbing, not branding.)

## What needs NO change
Security headers (`security_headers.py`) and CSP (`csp.py`) are
`'self'`-only/host-agnostic; HSTS applies per-host automatically. CSRF
(ProxyFix-aware), session cookies (host-scoped — correctly *not* shared
across the two names), and open-redirect protection
(`_is_safe_redirect`) are all unaffected.

## Verification

1. **DNS:** `dig +short sam.hpc.ucar.edu` resolves through the CNAME to
   the ingress IP.
2. **TLS:** `curl -vI https://sam.hpc.ucar.edu/` → cert SAN includes
   `sam.hpc.ucar.edu`, no mismatch; HTTP 200/302.
3. **Headers:** run `scripts/cirrus_healthcheck.sh --ingress-host
   sam.hpc.ucar.edu` — HSTS/CSP/CORP/Permissions-Policy all PASS on the
   new host (parity with `samuel.k8s.ucar.edu`).
4. **OIDC end-to-end:** in a browser, visit `https://sam.hpc.ucar.edu`,
   log in via Entra, confirm the URL bar stays on `sam.hpc.ucar.edu`
   through the callback and that an authenticated session lands on the
   dashboard. Repeat starting from `samuel.k8s.ucar.edu` to confirm
   parallel operation. Log out and confirm the IdP end-session redirect
   returns to the originating host.
5. **Tests:** `source etc/config_env.sh && pytest tests/unit/test_oidc_auth.py`
   — adjust the redirect-URI test (`test_oidc_login_uses_configured_redirect_uri`)
   if removing the static `OIDC_REDIRECT_URI` changes the prod default;
   ensure the dynamic-fallback branch is covered.

## Risks / open questions
- **Platform support for non-`.k8s.ucar.edu` ingress host + cert
  issuance** is the gating unknown (prereq 1). If CIRRUS cannot serve it,
  fall back to the A-record-to-external-LB approach (needs a stable LB
  target from NRIT).
- Going public (external visibility) broadens exposure beyond the recent
  ZAP-hardening baseline; coordinate with the in-progress
  `samuel.k8s.ucar.edu` public cutover rather than treating this as a
  separate exposure event.
