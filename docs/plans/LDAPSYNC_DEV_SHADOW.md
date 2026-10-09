# LDAP sync API: shadowing production on samuel-dev (sketch)

**Status:** sketch, 2026-10-09. Nothing scheduled; a fresh-context session matures this.
**Prerequisites:** PR #773 merged to `staging` (the code arrives on dev gated off), PR #774
rebased and undrafted (the patched daemon), the local soak's results in `LDAP_SYNC_API.md`
§ 10.7. **Read first:** `LDAP_SYNC_API.md` § 10, `LDAPSYNC_TESTBED_VERIFICATION.md`
(safety rules), `containers/ldap-pipeline/README.md`, the #774 comment on the lifecycle job.

## Goal

Run the real `sam-ldap-syncd` (patched build) against `samuel-dev` for days, with the
purge and restore levers on and lifecycle off, while legacy keeps serving production: the
same directory changes land in both, and a daily comparison shows only the known residual
classes. N clean days is the evidence for flipping `SAM_URL` on sam-app.

## Sequence

1. **Merge.** CI green; undraft #773; squash to `staging` with a title and body free of
   skip-ci tokens. Argo rolls `samuel-dev` (gated off); `watch-dev` confirms. Then #774:
   rebase on `staging`, add the "patched here" pointers in `SAM_LDAP_SYNCD_REFERENCE.md`
   (N5, 15, N22, N23), undraft. 773 first: both touch the testbed README.
2. **Baseline.** `make refresh-dev` → dev DB = prod at T0; record T0. The `admin`
   `api_credentials` row with `ROLE_API_ADMIN` comes with it (#690). Prove the handshake
   against dev: 401 with `Basic realm="Realm"`, then `ldapsync/status` 200 as `admin`.
   `RBAC_SOURCE=db` holds API keys to a token route's Permission; this blueprint passes
   `roles=` only, like XRAS — prove it on that first curl rather than assume.
3. **Levers, dev only** (`helm/values-dev.yaml`): `LDAPSYNC_PURGE_ENABLED: "1"`,
   `LDAPSYNC_RESTORE_ON_REACTIVATE: "1"`, `LDAPSYNC_LIFECYCLE_ENABLED: "0"` (the daemon
   cannot call it: `LifecycleUpdater.pm:296`, the #774 comment). Verify with the permit
   probe (an existing inactive user's permit must not say "Purge disabled").
4. **Repoint the testbed.**
   - `bin/guard.sh` allows writes only to `host.docker.internal` / `localhost`. samuel-dev
     is our own webapp, so the guard learns that one host as a write target — a reviewed
     change with its reason in the README, never a general relaxation.
   - HTTPS: `HTTPS_CA_DIR` must trust dev's certificate; `syncdTest` (`--test-connections`)
     against dev is the first proof. `test-sam` stays unusable (404s; the daemon retries forever).
   - The 24 MB `user` GET now crosses the dev ingress (1.8 s locally); watch the first one.
   - Collect and tear down the local soak (`out/HANDOFF.md` has the recipe), write § 10.7,
     then `.env.dev`: `SAM_URL=https://samuel-dev.k8s.ucar.edu`, stub off, `SAM_READ_PROD`
     empty, `SYNCD_TAG=<tag>-patched`. Reset the spool; start. Pass 1 should be small: dev
     is prod at T0 and the replica tracks prod.
5. **The daily shadow check.** Each morning, `syncdInit` against prod (one deliberate
   download) and against dev, then `bin/compare-dumps A/sam-data.json B/sam-data.json`.
   A clean day: only the § 10.5 residual classes and data age differ. Anything systematic
   is a port defect caught while legacy still serves prod. Plus the tick: the daemon's log
   locally, `scripts/cirrus_watch.sh --env dev` for the server side; counts only.
6. **Exit.** N clean days (pick N up front) → the prod `SAM_URL` flip on sam-app (George);
   rollback is the same variable.

## To settle with fresh context

- The guard's host rule and the TLS trust path (the two day-one stalls).
- Re-baseline cadence: dev drifts through its own testing; `make refresh-dev` weekly keeps
  the diff honest but resets the daemon's spool with it (collect first).
- Whether other dev users mind identity churn (purge is real deletes on dev).
- Where the daily comparison lands: a dated table in `LDAP_SYNC_API.md` § 10.7.
- Expect the 175 legacy-stamped pending users to finish on the first run that asks, once
  lifecycle is ever enabled anywhere; it is not part of this shadow.
