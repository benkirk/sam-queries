# `sam.hpc.ucar.edu` — cert + per-host OIDC rollout

## Status

| Unit | State |
|---|---|
| Unit 1 — multi-host ingress + healthcheck (PR #367) | **Deployed** |
| Cert reissue (multi-SAN `incommon-cert-samuel`) | **Done** — see below |
| Unit 2 — per-host OIDC callback | **Deployed to CIRRUS + browser-smoked** on `cname_final` (`sha-831f083`); pending merge |
| Step 4 — logout post-logout URI | **Tested** — needs one Entra swap, see below |
| Step 5 — advertise the name | Blocked on the merge + the Entra swap |

All commands run against namespace `sam-queries`.

---

## Completed — the cert

The InCommon/Sectigo ACME account was suspended account-wide, which blocked the
reissue. CIRRUS resolved it; cert-manager's own failed-issuance backoff then
retried and succeeded with no manual intervention — Steps 1–2 of the original
runbook never needed to be executed by hand.

Verified 2026-07-29:

- `Certificate incommon-cert-samuel`: `Ready=True`, `revision=2`,
  `dnsNames=["samuel.k8s.ucar.edu","sam.hpc.ucar.edu"]`.
- Newest Order `state=valid`.
- **Wire truth:** both hosts serve a cert issued by
  `InCommon Intermediate CA - DVG2C` whose SANs are
  `sam.hpc.ucar.edu, samuel.k8s.ucar.edu`.
- `notAfter=2027-02-13`, `renewalTime=2026-12-09`.
- `scripts/cirrus_healthcheck.sh`: §6 (served-cert coverage) and §7 (edge
  security headers) **green on both hosts**.

> **The old "independent production concern" is RESOLVED.** The worry was that
> the suspension would also block the routine `samuel.k8s.ucar.edu` renewal due
> 2026-08-05. That renewal has happened; the next one is 2026-12-09. No NRIT /
> InCommon CM ticket is outstanding.

Re-verify at any time with:

```bash
kubectl -n sam-queries get certificate incommon-cert-samuel \
  -o jsonpath='dnsNames:{.spec.dnsNames}{"\n"}revision:{.status.revision}{"\n"}{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'

for h in samuel.k8s.ucar.edu sam.hpc.ucar.edu; do
  echo "=== $h ==="
  echo | openssl s_client -connect ${h}:443 -servername ${h} 2>/dev/null \
    | openssl x509 -noout -subject -issuer -enddate -ext subjectAltName
done

scripts/cirrus_healthcheck.sh -v
```

§6 keys on the **served** SANs decoded from the Secret's `tls.crt`, not the
requested `spec.dnsNames`, so a green §6 is real proof the browser sees the
right cert.

---

## Unit 2 — per-host OIDC callback

**The bug it fixes.** `helm/values.yaml` pinned
`OIDC_REDIRECT_URI=https://samuel.k8s.ucar.edu/auth/oidc/callback`, so a login
started on `sam.hpc` was handed to Entra with a return address on the other
host. Authlib scopes the PKCE verifier / state cookie to the origin the login
started on, so the callback landed somewhere that cookie was invisible and died
with `MismatchingStateError`. Anonymous pages were unaffected.

**The change.** Delete the pin. `webapp/auth/blueprint.py` already treats it as
an override — `config.get('OIDC_REDIRECT_URI') or url_for('auth.oidc_callback',
_external=True)` — and `ProxyFix(x_host=1, x_proto=1)` (`webapp/run.py`)
resolves that from `X-Forwarded-*`. No application code changed. Nothing else
pins the external URL: there is no `SERVER_NAME`, `PREFERRED_URL_SCHEME`,
`APPLICATION_ROOT`, or CORS allowlist anywhere in the repo.

**Guards added,** because the failure mode is invisible to HTTP-level checks:

- `helm/tests/test-oidc-render.sh` — asserts `OIDC_REDIRECT_URI` and any
  `auth/oidc/callback` literal do **not** render from `values.yaml`.
- `tests/unit/test_oidc_auth.py::test_oidc_login_falls_back_to_request_host` —
  the deployed branch (no config) derives the callback.
- `…::test_oidc_login_callback_follows_forwarded_host` — parametrized over both
  hosts, driving real `X-Forwarded-Host` through ProxyFix.
- `…::test_oidc_login_uses_configured_redirect_uri` kept: the override branch
  is still supported for a single-host deployment.

`helm/values-local.yaml` still sets `OIDC_REDIRECT_URI: ""`. That is now a
no-op, deliberately left in place inside its "override production OIDC envs
back to stub" defense-in-depth block.

**Pre-req, verified — no Entra change needed for login.** Replaying the live
authorize URL with `redirect_uri` swapped to `sam.hpc` returns AADSTS**50058**
(“needs sign-in”), identical in shape to the `samuel.k8s` response — *not*
AADSTS**50011** (reply-URL mismatch). Entra validates reply URLs before
rendering the sign-in page, so `https://sam.hpc.ucar.edu/auth/oidc/callback`
is already registered.

### Deploying it

Two valid routes — the chart reaches CIRRUS either way:

- **Any branch, on demand:** `workflow_dispatch` on
  `build-images-cirrus-deploy.yaml` once the branch is pushed. This is the
  fastest way to smoke Unit 2 against real Entra before merging.
- **Automatic:** push to `main` triggers the same workflow. Either path builds
  the webapp image, force-pushes the `cirrus` branch with the image pinned, and
  ArgoCD reconciles.

> ⚠ **The staging PR is not ceremonial.** `ci-staging.yaml` is the only place
> `helm/tests/test-oidc-render.sh` runs, and it triggers on `pull_request` into
> **`staging`** only — never on PRs into `main`. Merging straight to `main`
> would never execute the render assertions above.

### Verifying it

```bash
# each host must now name ITSELF as the callback
for h in samuel.k8s.ucar.edu sam.hpc.ucar.edu; do
  echo "=== $h ==="
  curl -s -o /dev/null -D - "https://$h/auth/oidc/login" \
    | grep -i '^location:' | tr '&' '\n' | grep -i redirect_uri
done
```

Then, **in a browser from each host in turn**, a full OIDC login round-trip:
the URL bar must stay on the originating host through the callback and land
signed in. The unit tests mock Authlib, so only this proves the cookie
actually survives the round-trip.

**Result on `sha-831f083` (2026-07-29):**

- `redirect_uri` before → both hosts emitted `https://samuel.k8s.ucar.edu/…`;
  after → each host emits its own. `samuel.k8s` unchanged, so no regression.
- Interactive login from **`sam.hpc`**: Entra accepted the reply URL (sign-in
  page, not AADSTS50011), PKCE present (`code_challenge_method=S256`), callback
  landed on `sam.hpc`, **no `MismatchingStateError`**, identity resolved with
  admin RBAC, 0 console errors.
- Interactive login from **`samuel.k8s`**: succeeded via Entra SSO, stayed on
  `samuel.k8s`.
- The landing page after login is RBAC-dependent (admins → `/admin/projects`,
  unprivileged → their user dashboard), so assert on the **host**, not the path.
- `scripts/cirrus_healthcheck.sh`: 35 PASS / 1 WARN / 0 FAIL (the WARN is the
  pre-existing "no helm release object" — ArgoCD reconciles the chart).

**Rollback** is restoring one line in `values.yaml`. `samuel.k8s` login is
unaffected throughout — the derivation produces the identical URL for that host.

---

## Step 4 — logout — TESTED, one Entra change outstanding

Entra allows only one post-logout redirect URI; it is currently
`https://samuel.k8s.ucar.edu/`. **No code change is needed** — logout already
derives `post_logout_redirect_uri` per-host as
`url_for('status_dashboard.index', _external=True)` → `https://<host>/status/`
(`src/webapp/auth/blueprint.py`, blueprint `url_prefix='/status'`).

**Measured 2026-07-29** on the deployed branch, both legs in one browser
session:

| Logout from | Registered origin? | Emitted URI | Result |
|---|---|---|---|
| `samuel.k8s.ucar.edu` | yes (root `/`) | `https://samuel.k8s.ucar.edu/status/` | returned cleanly to `/status/derecho` |
| `sam.hpc.ucar.edu` | no | `https://sam.hpc.ucar.edu/status/` | stranded on MS "You signed out of your account" |

> **Entra matches the post-logout URI by ORIGIN, not exact string.** The
> `samuel.k8s` leg proves it: a registration of root `/` accepted an emission
> of `/status/`. An earlier reading of Microsoft's "must match exactly" docs
> predicted the opposite and was wrong — do not re-derive this from the docs,
> the experiment above is the evidence.

**Therefore: accept the swap exactly as Entra offered it.** Registering
**`https://sam.hpc.ucar.edu/`** (root) is sufficient; there is no need to ask
for the longer `/status/` form. Per Ben's accepted tradeoff, clean logout on
`sam.hpc` with `samuel.k8s` degraded to the MS signed-out page is fine — and
that degradation is now a known, measured consequence rather than a surprise.

Login is unaffected either way: reply URLs are a separate Entra list and both
hosts are already registered there (proven by a successful interactive login on
each host).

---

## Step 5 — advertise

Only once Unit 2 and Step 4 are both green: communicate `sam.hpc.ucar.edu` as
SAM's user-facing hostname. `samuel.k8s.ucar.edu` stays as the platform /
automation / healthcheck alias — parity tooling, `scripts/apis/*`, and
`src/cli/cmds/admin.py` remain on it deliberately, with no 301 redirect.

---

## Note for future aliases

Adding a host is three things, not two: `webapp.tls.extraHosts` in
`values.yaml`, `INGRESS_HOSTS` in `scripts/lib/cirrus_common.sh`, **and** an
Entra reply-URL registration for its `/auth/oidc/callback`. The callback URL
itself needs no config — it is derived. `src/webapp/utils/config_inspect.py`
now reports `oidc_redirect_uri: None` in production; that is expected.
