# `sam.hpc.ucar.edu` — cert-fix apply runbook

## Context

Unit 1 of the `sam.hpc.ucar.edu` rollout (multi-host ingress + multi-host
healthcheck, PR #367) is **deployed on CIRRUS-k8s**. The ingress serves both
`samuel.k8s.ucar.edu` and `sam.hpc.ucar.edu` and requests a single multi-SAN
cert (`incommon-cert-samuel`). But the InCommon/Sectigo **ACME account is
suspended account-wide**, so the reissue cannot complete:

- `Certificate incommon-cert-samuel`: `Ready=False` (`SecretMismatch`),
  `Issuing=Failed`, `revision=1`, `failedIssuanceAttempts` climbing.
- The Order errors with
  `401 urn:ietf:params:acme:error:unauthorized: The account is currently suspended`.
- **Wire truth today:** `samuel.k8s.ucar.edu` serves its real InCommon cert
  (single SAN, `notAfter Oct 10 2026`) and is **unaffected**; `sam.hpc.ucar.edu`
  serves the fake `Kubernetes Ingress Controller Fake Certificate`.

This is the expected, contained interim state. `samuel.k8s.ucar.edu` keeps
working throughout. This doc is the fast-path to finish the job **once Ben
confirms the ACME account is unsuspended.**

All commands run against namespace `sam-queries`. We cannot read ClusterIssuers
or Capsule Tenants (Forbidden for our OIDC identity) — the suspension itself is
an NRIT / InCommon CM concern, not something we fix from here.

---

## Step 0 — trigger

**Do nothing until Ben says the ACME account is unsuspended.** Waiting does not
help: cert-manager's failed-issuance backoff (1h,2h,4h,8h,16h, cap 32h) means
the next automatic retry could be up to ~32h out, and it will just fail again
while suspended.

---

## Step 1 — force an immediate retry (skip the backoff)

Delete the failed CertificateRequest (this also clears the stale errored Order);
the ingress-shim / cert-manager immediately creates a fresh request:

```bash
kubectl -n sam-queries delete certificaterequest incommon-cert-samuel-2
```

Equivalent alternative if `cmctl` is available:

```bash
cmctl -n sam-queries renew incommon-cert-samuel
```

> The CertificateRequest name may have incremented past `-2` on a later
> revision. Confirm the current one first:
> `kubectl -n sam-queries get certificaterequest`.

---

## Step 2 — verify the reissue succeeded

```bash
# Certificate: Ready=True, revision>=2, BOTH dnsNames
kubectl -n sam-queries get certificate incommon-cert-samuel \
  -o jsonpath='dnsNames:{.spec.dnsNames}{"\n"}revision:{.status.revision}{"\n"}{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'

# Newest Order: state=valid
kubectl -n sam-queries get order | tail -3

# Ground truth on the wire — BOTH SANs present, chain verifies:
for h in samuel.k8s.ucar.edu sam.hpc.ucar.edu; do
  echo "=== $h ==="
  echo | openssl s_client -connect ${h}:443 -servername ${h} 2>/dev/null \
    | openssl x509 -noout -subject -issuer -enddate -ext subjectAltName
done
```

Success looks like: Certificate `Ready=True`, `revision` incremented, both
dnsNames; newest Order `state=valid`; **both** hosts present a cert whose SANs
include `samuel.k8s.ucar.edu` **and** `sam.hpc.ucar.edu`, issued by InCommon,
chain verifies (no more fake cert on `sam.hpc`).

Then run the full healthcheck — §6 (served-cert coverage) must now PASS on both
hosts:

```bash
scripts/cirrus_healthcheck.sh -v
```

The §6 check keys on the **served** SANs decoded from the Secret's `tls.crt`
(not the requested `spec.dnsNames`), so a green §6 is real proof the browser
sees the right cert.

**If it still fails while the account is reportedly unsuspended:** capture the
newest Order's `.status.reason`
(`kubectl -n sam-queries get order -o wide` then describe it) and hand back to
NRIT / InCommon CM — the block is upstream of us.

---

## Step 3 — Unit 2: per-host OIDC (MUST be reconstructed first)

Until this ships, **login from `sam.hpc.ucar.edu` fails** with Authlib
`MismatchingStateError`: the static `OIDC_REDIRECT_URI` sends the callback to
`samuel.k8s.ucar.edu`, a different origin where the `sam.hpc` PKCE/state cookie
does not exist. Anonymous pages work; `samuel.k8s` is unaffected.

⚠ **The Unit 2 branch `sam_hpc_oidc_perhost` no longer exists** (orphaned in a
rebase, or never pushed). It has to be re-created. The change is small:

- **Remove** the static `OIDC_REDIRECT_URI` env var from `helm/values.yaml`.
  `src/webapp/auth/blueprint.py:130` already falls back to
  `url_for('auth.oidc_callback', _external=True)`, which `ProxyFix(x_host=1)`
  (`src/webapp/run.py`) resolves to whichever host the user is on. Confirmed no
  `SERVER_NAME` / `PREFERRED_URL_SCHEME` / CORS-origin pins the external URL.
- **Tests:**
  - `helm/tests/test-oidc-render.sh` — flip the `OIDC_REDIRECT_URI` /
    `samuel.k8s.ucar.edu/auth/oidc/callback` assertions from `assert_contains`
    to `assert_not_contains` (callback is derived per-host).
  - `tests/unit/test_oidc_auth.py` — keep
    `test_oidc_login_uses_configured_redirect_uri` (covers the config-present
    branch); **add** `test_oidc_login_falls_back_to_request_host` asserting
    `authorize_redirect` is called with `http://<request host>/auth/oidc/callback`
    when `OIDC_REDIRECT_URI` is absent.
- **Pre-req before deploying Unit 2:** cert is real (Steps 1–2 done) AND Entra
  reply-URLs confirmed. Both login reply URLs are registered
  (`https://sam.hpc.ucar.edu/auth/oidc/callback` + the existing samuel.k8s one).

Deploy Unit 2 via the CIRRUS `workflow_dispatch` once merged to `main`, then do
a browser OIDC round-trip **from each host** — the URL bar must stay on the
originating host through the callback.

---

## Step 4 — logout (test-first, before touching Entra)

Entra allows only one post-logout redirect URI; it is currently the samuel.k8s
value. Logout emits `post_logout_redirect_uri` derived per-host as
`url_for('status_dashboard.index', _external=True)` →
`https://<host>/status/` (`src/webapp/auth/blueprint.py:~209`, blueprint
`url_prefix='/status'`).

1. Log in **and** log out **from `sam.hpc.ucar.edu`**.
2. If it redirects back cleanly → no Entra change needed (leading hypothesis:
   Entra validates the post-logout URI by registered-reply-URL **origin**, and
   `https://sam.hpc.ucar.edu/auth/oidc/callback` is already registered). Do not
   assert this — the test is the proof.
3. If it lands on MS's signed-out page instead → have Andrew Tamagni register
   **`https://sam.hpc.ucar.edu/status/`** (exact emitted string, host-swapped —
   NOT root `/`). Per Ben's accepted tradeoff: clean logout for `sam.hpc`,
   degraded from `samuel.k8s`, is fine.

---

## Step 5 — advertise

Only after Steps 2–4 are all green: communicate `sam.hpc.ucar.edu` as SAM's
user-facing hostname. `samuel.k8s.ucar.edu` stays as the platform / automation /
healthcheck alias (parity tooling, `scripts/apis/*`, `src/cli/cmds/admin.py`
remain on it deliberately — no 301 redirect).

---

## Independent production concern (do not lose track of)

`incommon-cert-samuel` has `renewalTime=2026-08-05` and `notAfter=2026-10-10`.
The account suspension is a live production risk **regardless of `sam.hpc`** — if
it is not resolved before early August, the normal renewal of the
`samuel.k8s.ucar.edu` cert also fails. This warrants a separate NRIT / InCommon
CM ticket tracking the suspension itself.
