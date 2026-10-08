# Traefik ingress: move SAMuel off `nginx-external`

Status: PR 1 (dev on `traefik-external`) is the PR that carries this file. Prod waits
on the dev soak and the probe comparison below.

## Why

CIRRUS (Kevin, Nick, 2026-10-07) say ingress-nginx support on nwc1 has ended and
tenants should set `ingressClassName: traefik-external`; Traefik Middleware replaces
nginx annotations. "Switching the app to that is step one, then we can refine as
needed."

We want it for a second reason. The ingress soak probe (`ingress_probe.py`, a laptop
vantage; see "The probe" below) measures loss at the edge that never reaches the app:
about 2% of first SYNs to 128.117.41.126 lost (0.2% to the CNPG LoadBalancer
addresses on the same cluster and to a control host), in multi-minute clusters, and
TLS stalls where TCP accepts in ~25 ms and the handshake never finishes
(2026-10-07 13:42-13:57Z, 232 of 247 attempts; again 22:49-22:50Z the same evening,
41 of 45, after the kernel roll had finished). Jenett's `sock.connect()` hangs from
guadm (10-06 18:44Z, 20:00-20:15Z) match. Nothing in the pod logs: the stall is in
front of the Service.

## What was verified (read-only, 2026-10-07)

- **128.117.41.126 is genuine ingress-nginx today**, not Traefik in a compatibility
  mode: an unknown SNI gets `CN=Kubernetes Ingress Controller Fake Certificate`, an
  unknown host gets nginx's own 404 page, port 80 answers 308 to https. So
  `traefik-external` is a **separate controller** and almost certainly a **separate
  LoadBalancer IP** (two Services cannot share one IP:443). The tenant cannot see it:
  Capsule rejects `get ingressclass` and cluster-wide `get svc`; the only
  LoadBalancer Services visible are the CNPG ones in `pg-testing`.
- **DNS.** `samuel.k8s.ucar.edu` and `samuel-dev.k8s.ucar.edu` are 300 s A records in
  the CIRRUS zone (`ml-ddi` / `wy-ddi`); `sam.hpc.ucar.edu` is a static CNAME to
  `samuel.k8s.ucar.edu` and follows it; `gdex.ucar.edu` has the same shape. Moving an
  A record is CIRRUS's (or external-dns's), never ours.
- **The chart's only nginx-specific content** is three rate-limit annotations
  (`helm/templates/ingress.yaml`, `webapp.ingress.rateLimit` in `helm/values.yaml`),
  measured inert: every client arrives as 127.0.0.1 so they were one global bucket,
  and it never tripped at 180 req/s (`docs/plans/implemented/DEV_LOAD_CAMPAIGN.md`).
  The class is composed as `nginx-` + `webapp.ingress.visibility`; `values-local.yaml`
  sets `visibility: nginx` and renders the nonsense `nginx-nginx`. No helm test
  asserts the class or the annotations.
- **App-side edge dependencies.** `ProxyFix(x_for=PROXYFIX_X_FOR, x_proto=1,
  x_host=1, x_prefix=1)` in `src/webapp/run.py`; the OIDC callback is built from the
  forwarded host and proto (`helm/tests/test-oidc-render.sh`,
  `tests/unit/webapp/test_oidc_auth.py`). gunicorn `forwarded_allow_ips='*'`,
  `keepalive=5`, and logs `xff="…"`. HSTS and CSP are set by the app. Flask-Limiter
  keys anonymous traffic by `ip:`, today one shared bucket.
- **Traefik, from its docs.** The Kubernetes Ingress provider honors
  `spec.ingressClassName`. `cert-manager.io/cluster-issuer` is read by cert-manager's
  ingress-shim, not the controller, and the Certificate spec (hosts, secretName) does
  not change, so no re-issuance. Traefik writes its own `X-Forwarded-{Proto,Host,For,Port}`
  (client-supplied copies are dropped unless the entrypoint trusts the source), so
  ProxyFix `x_for=1` stays right and starts meaning something. The Ingress
  `status.loadBalancer` is published by default (`publishedService`), so
  `kubectl get ingress -o wide` is how we learn the VIP. **HTTP to HTTPS redirect is
  not a chart default** (nginx gave us the 308). No request-body cap (nginx's default
  was 1 MiB; the app's `MAX_CONTENT_LENGTH` of 16 MiB becomes the only one). Middleware
  attach as `traefik.ingress.kubernetes.io/router.middlewares: <ns>-<name>@kubernetescrd`.

## Design

### Chart: one class string; the nginx annotations ride on it

`helm/values.yaml`, replacing `visibility` and the `rateLimit` comment block:

```yaml
  ingress:
    # IngressClass name as CIRRUS gives it. nwc1 retired ingress-nginx
    # (nginx-external) for Traefik (traefik-external), 2026-10.
    className: nginx-external            # PR 1; PR 2 flips to traefik-external
    # nginx-only edge limit, rendered only while className starts with "nginx";
    # inert in practice (docs/plans/implemented/DEV_LOAD_CAMPAIGN.md). Flask-Limiter
    # is the limiter. Removed with the nginx class.
    rateLimit: {rps: 100, connections: 200, burstMultiplier: 5}
```

- `helm/templates/ingress.yaml`: `ingressClassName: {{ required "…" .Values.webapp.ingress.className }}`;
  the three annotations inside `{{- if hasPrefix "nginx" $class }}`. Keep
  `cert-manager.io/cluster-issuer`.
- `values-local.yaml`: `className: nginx` (fixes `nginx-nginx`).
  `values-dev.yaml`: `ingress: {className: traefik-external}`, PR 1 only.
- **No Traefik Middleware now.** The edge limit never did anything and the app limits
  itself; a per-IP edge limiter is a deliberate later change, made with the measured
  `xff` distribution in hand. Capsule may not allow `middlewares.traefik.io` at all
  (`kubectl -n sam-queries-dev auth can-i create middlewares.traefik.io` answers that
  when the day comes). One comment in `values.yaml` points at the annotation form.

### Rollout: dev as the Traefik canary, probe both VIPs, then prod

Questions for CIRRUS first; the answers set the risk profile:

1. Does `traefik-external` have its own LoadBalancer IP? Who moves the `k8s.ucar.edu`
   A record: external-dns off the Ingress status, or a request?
2. Is the HTTP to HTTPS redirect on the `web` entrypoint? (nginx answered 308 on :80.)
3. Does it hand the real client IP in `X-Forwarded-For`? (nginx handed 127.0.0.1.)
4. How long does `nginx-external` keep running? That is the rollback horizon.

**PR 1 (staging, dev only):** the chart change, the dev override, tests, docs. After
Argo syncs `sam-query-dev`, in order:

- `kubectl -n sam-queries-dev get ingress samuel-dev -o wide`: CLASS, and **ADDRESS is
  the new VIP** (empty: ask Nick). `dig samuel-dev.k8s.ucar.edu`: same IP means no DNS
  work; a different one means dev is dark on the old VIP (nginx's default backend
  404s) until the record moves. Test meanwhile with
  `curl --resolve samuel-dev.k8s.ucar.edu:443:<vip>`.
- Cert: the `incommon-cert-samuel-dev` Secret and Certificate resourceVersion
  unchanged; served SANs match (`scripts/cirrus_healthcheck.sh --env dev`).
- `curl -sI --http2 https://samuel-dev.k8s.ucar.edu/api/v1/health/live` answers 200
  over HTTP/2. On :80 Traefik served the app in plaintext (checked 2026-10-08; nginx
  answered 308), and the tenant may not create a `Middleware` (`auth can-i` says no),
  so the chart binds the router to `websecure` (`webapp.ingress.traefik.entrypoints`)
  and :80 answers 404. A real redirect is CIRRUS's: on the `web` entrypoint,
  cluster-wide. The session cookie is `Secure`, so a plaintext login fails loudly.
- An OIDC round trip in a browser (proves the forwarded host and proto).
- gunicorn's `xff=` field in the pod log: expect real client IPs. One hop means
  `proxyFixForwardedHops: 1` stays; two means bump to 2. Measure, do not guess.
- `scripts/cirrus_watch.sh --env dev`, `scripts/cirrus_weblog_audit.sh`, and a short
  `profile-dev` load run, to see whether DEV_LOAD_CAMPAIGN's connect stalls (finding F)
  are gone.
- **The probe:** a second `ingress_probe.py` instance on the new VIP
  (`--vip <ip> --host samuel-dev.k8s.ucar.edu --out …/laptop-traefik`, no code
  change), 48 h or more beside the nginx run. That A/B, the SYN-loss and TLS-stall
  rate per controller, is the evidence CIRRUS asked for and the go/no-go for prod.

**PR 2 (prod):** flip the default to `traefik-external`, drop the dev override, flip
the test assertions. If the VIP differs, do not flip and hope: add a transitional
`templates/ingress-legacy.yaml` (gated by `webapp.ingress.legacyClassName`, same
hosts, same `secretName`, name `samuel-legacy`) so nginx keeps serving the old VIP
while Traefik serves the new one; then CIRRUS moves `samuel.k8s.ucar.edu` (300 s TTL;
`sam.hpc.ucar.edu` follows), a day of `watch-prod`, then **PR 3** removes the legacy
Ingress, the `rateLimit` values and the nginx annotation branch. Same VIP: PR 2 is a
one-line flip and PR 3 is cleanup. Ben owns deploy timing; no image rebuild anywhere.

### Tests and scripts

- `helm/tests/test-dev-render.sh` (section 4, after the hosts check; and the prod
  block): assert `ingressClassName` per env (PR 1: dev traefik, prod nginx; PR 2: both
  traefik, plus `expect_reject --set webapp.ingress.className=nginx-external`); no
  `nginx.ingress.kubernetes.io/` annotation when the class is traefik; local renders
  `ingressClassName: nginx`, never `nginx-nginx`.
- `scripts/cirrus_weblog_audit.sh` (R1/R2 header; the `EDGE_RPS` read): read
  `.spec.ingressClassName` first; non-nginx passes as "edge rate limit: none at the
  ingress (class X; Flask-Limiter is the limiter)". Rewrite R2: "every client is
  127.0.0.1" becomes controller-conditional, and the section-2 symptom flips to a pass
  when distinct `xff` IPs appear.
- `scripts/cirrus_healthcheck.sh`: the comment "the cert nginx actually presents"
  becomes "the ingress controller".

### Docs

- `docs/README-k8s.md` (the `nginx-external` mentions and "Accessing the App"):
  `traefik-external`; how to read the VIP from `get ingress -o wide`; :80 behavior is
  controller-level; `proxyFixForwardedHops` re-measured.
- `helm/README.md` dependency line; `.claude/skills/watch-dev/SKILL.md`; the values
  comments.
- Records (`K8S_DEV_ENVIRONMENT.md`, `CNAME_PLAN.md`, `RATE_LIMITING.md`,
  `DEV_LOAD_CAMPAIGN.md` under `docs/plans/implemented/`): one dated line each.
- The deck (`docs/presentations/samuel/_5-deployment.qmd`, `_D-security.qmd`
  client-IP slide): after prod moves and the IP finding is confirmed.

### Risks to name in the PRs

- Plain HTTP on :80 (highest; above).
- A VIP change is a dark window until DNS moves: the bridge Ingress for prod.
- Real client IPs: Flask-Limiter goes from one shared anonymous bucket to per-IP,
  looser in aggregate and tighter behind campus or VPN NAT. Review the anon/authed
  tiers after a week. Audit logs and the Turnstile check start seeing real addresses.
- Body cap becomes the app's 16 MiB instead of nginx's 1 MiB.
- Timeouts: Traefik read 60 s, write none, idle 180 s; gunicorn `timeout=120` stays
  the cap; keepalive 5 s under the idle timeout, as today.
- Rollback is reverting the values line, for as long as `nginx-external` exists.

## The probe

`~/.local/state/sam-watch/ingress_probe.py` (stdlib, Python 3.6-safe, not in the
repo yet) makes a fresh TCP+TLS connection to the VIP every 2 s with no request, an
HTTP GET of `/api/v1/health/live` every 20 s (UA `sam-ingress-probe/1`), connect-only
probes of the CNPG addresses 128.117.217.105 / .107 every 5 s, and the same TCP+TLS
against a control host (`sam.ucar.edu`); with `--kube-context nwc1` it logs node and
pod transitions beside the samples. Slow is a connect of 0.9 s or more (a lost SYN
that a retransmit recovered); laptop sleeps are excluded; episodes are merged within
60 s and compared with the other targets in the same window. Reading rule: every
target failing together, or `tcp_unreach`, is the laptop or VPN; control also slow is
the laptop's path; ingress only is the LB-to-controller leg. `--report --since 35m`
prints the per-target table and the bad episodes with source ports and the nearest
cluster event. Whether it moves into `scripts/` is decided after this rollout.

## Verification

`helm template` for prod, dev and local; `bash helm/tests/test-dev-render.sh` and
`test-oidc-render.sh`; on dev after the sync, the checklist above (ingress address,
cert, HTTP/2, :80 redirect, OIDC, xff hops, healthcheck, a quiet `watch-dev` tick);
the second probe instance reporting for 48 h or more; the PRs mapped in the watch's
expected-fixes list.
