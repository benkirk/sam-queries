# Ingress stopgap: nginx-external while traefik-external is down

Status: interim. Dev first; prod only after the dev proof. Revert both once CIRRUS
restores the Traefik VIP.

## The incident

From 2026-11-21 03:30:45 MST the nwc1 `traefik-external` VIP 128.117.41.125 refuses
TCP 80 and 443 from outside the cluster (laptop on VPN, casper), so `sam.hpc.ucar.edu`,
`samuel.k8s.ucar.edu` and `samuel-dev.k8s.ucar.edu` are unreachable. Connect timeouts to
it began about 2026-11-19 16:10 MST, when real client IPs first reached the pods. Inside
the cluster the same VIP answers and serves every SAMuel host with the right certificate,
so Traefik and our Ingresses are healthy and the fault is the external load-balancer path.
The `nginx-external` VIP 128.117.41.126 and the CNPG LoadBalancer IPs answer from outside.

## The change

`webapp.ingress.className: nginx-external`, first in `helm/values-dev.yaml`, then in
`helm/values.yaml` for prod. On an nginx class the chart renders
`nginx.ingress.kubernetes.io/proxy-body-size: "16m"` (nginx defaults to 1m; the app's
`MAX_CONTENT_LENGTH` is 16 MiB) and drops the Traefik entrypoint annotation. The
`incommon` certificate secrets are unchanged. DNS is not ours: CIRRUS's external-dns
follows the Ingress status, which moved both names in under six minutes in 2026-10.

Costs: nginx-external is retired (CIRRUS, 2026-10) and may be removed without notice;
every client reaches the app as 127.0.0.1 again; plain http answers nginx's 308 to https.

## Dev proof

After Argo syncs `sam-query-dev`:

1. `kubectl -n sam-queries-dev get ingress samuel-dev -o wide`: CLASS `nginx-external`,
   ADDRESS 128.117.41.126.
2. Before DNS moves: `curl --resolve samuel-dev.k8s.ucar.edu:443:128.117.41.126
   https://samuel-dev.k8s.ucar.edu/api/v1/health/ready` answers 200 with the dev cert.
3. `dig +short samuel-dev.k8s.ucar.edu` reaches 128.117.41.126 (300 s TTL).
4. By name: `/ready` 200, an OIDC sign-in completes, a POST over 1 MiB is not refused 413.
5. The HPC dev-lane collectors post again (`scripts/cirrus_watch.sh --env dev`).

## Prod and revert

Prod: the line moves to `helm/values.yaml` (dev inherits it, so `values-dev.yaml` no
longer carries one), merged to staging and promoted, then steps 1-5 for `samuel` with
`samuel.k8s.ucar.edu` (`sam.hpc.ucar.edu` is a CNAME to it).
Revert: set `helm/values.yaml` back to `traefik-external` once `nc -z 128.117.41.125 443`
succeeds from outside; DNS follows back, with up to one TTL on the old VIP.
