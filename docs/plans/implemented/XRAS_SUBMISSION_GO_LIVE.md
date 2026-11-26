# XRAS submission go-live

**Status: implemented; live in production 2026-11-19** (`sha-cb397b6`, promoted in #956).
Phases 1 to 4 merged 2026-11-18 as #953 (`0a6e10f1`), promoted in #952; the limits one-off ran
on `sam_dev` and on production the same day. Phase 6 was two steps: #954 (filing from My
Requests, `/request` for signed-in users) and #957 (`/request` public).
Builds on `implemented/XRAS_SUBMISSION_SPRINT2.md` (#880) and `XRAS_SUBMISSION_SPRINT3.md` (#886);
the design is `XRAS_SUBMISSION.md`.

## What this branch changes

| Phase | Change |
|---|---|
| 1 | `/request` is gated on its own. It is always mounted; `XRAS_SUBMIT_ANONYMOUS_ENABLED` serves it and the new `ALLOCATION_REQUEST_LOGIN_REQUIRED` (default on) keeps it to signed-in users. `ACCOUNT_REGISTRATION_ENABLED` and `ACCOUNT_REGISTRATION_LOGIN_REQUIRED` now cover `/register` alone |
| 2 | No form asks for the project code letters. A posted code is ignored. A New with no mnemonic match files with none; Remediations shows series `<facility>---` and the Mnemonic blocker from the day it is filed, and the existing override assigns the code. The visitor's review expects all three of the placeholder PI's own person messages |
| 3 | A filed visitor request says so: the outcome block gives the request number, the status page reads "Request received", and the `allocation_request_received` mail carries the facts and a status link good for `REQUEST_STATUS_TTL_DAYS` (365) |
| 4 | Limits follow CISL's approval rules: the ceiling is the absolute maximum, the default sits in the initial range, a justified resource has no default, and a ceiling of 0 withholds a resource from a type's New requests (a project keeps what its line already holds). Values: `XRAS_SUBMISSION.md` § 4.5 |

## Phase 5: dev proof

After the dev roll, run the one-off limits script on `sam_dev`, then in a browser:

1. `/request` signed out, end to end, with an institution SAM has no code for: no picker, Submit
   enabled, the outcome block's number, Done, the "Request received" page.
2. Admin > Notifications shows the receipt recorded (suppressed on dev: mail is off).
3. Allocations > XRAS > Remediations shows the request with `U---####` and the Mnemonic blocker;
   set the override and the series resolves.
4. The signed-in New has no picker either.
5. `/register` on dev is unaffected by `ALLOCATION_REQUEST_LOGIN_REQUIRED`.
6. A Classroom card and form with no Campaign Store; Casper GPU with no pre-filled amount; a
   Small request accepted at 4,000,000 Derecho and refused above it.

## Phase 6: the production flip

`helm/values.yaml`, in two PRs:

| Step | Values |
|---|---|
| Preview | `XRAS_SUBMIT_ENABLED: "1"`, `XRAS_SUBMIT_ACTION_TYPES: "all"`, `XRAS_SUBMIT_ANONYMOUS_ENABLED: "1"` in the webapp env **and** `tasks.env`. `/request` stays signed-in only |
| Public | `ALLOCATION_REQUEST_LOGIN_REQUIRED: "0"` |

`ACCOUNT_REGISTRATION_ENABLED` stays `"0"`: `/register` is not part of this. The pins in
`helm/tests/test-dev-render.sh` and `tests/unit/gates/test_xras_admin_client.py`
(`TestTheVisitorLever`, `TestHelmSubmitLever`) are rewritten in the same PR.

The preview step alone makes all four request types live for every signed-in lead and admin on
My Requests; there is no lever for the visitor's New alone.

### Checks before the flip

1. **Limits applied.** Done 2026-11-18: the one-off script ran on production (verification
   row 3 | 8 | 4).
2. **Tables present on production:** `xras_submission`, `allocation_type_guide`,
   `allocation_type_resource_limit`, the account-request `purpose` column.
3. **Production XRAS:** the public opportunities are open and offer the expected resources; the
   EUA attribute sets `XRAS_SUBMISSION.md` § 3.4 says to delete at the pivot; XRAS itself does
   not mail the requester on submit (or the receipt is a second mail).
4. **Client IPs.** Read `xff=` in a production pod log. While every request arrives as one
   address the per-IP tiers are one shared bucket, and Turnstile, the per-address tier and
   `RATELIMIT_REGISTER_GLOBAL` carry the public form. Verified 2026-11-26 on `traefik-external`:
   `xff=` is 1 hop with real client IPs, and a forged X-Forwarded-For is dropped
   (`TRAEFIK_INGRESS.md` § Measured on Traefik).
5. **Mail:** Turnstile keys present; no stale override of `account_verify`; one verify mail and
   one receipt to an outside address during the preview step.
6. **Preview pass** on production as staff before the public step.

### Writes that begin on production

- At a visitor's filing: an `account_request` row (held), an XRAS placeholder person, the XRAS
  request, an `xras_submission` row, the verify mail and the receipt mail.
- After approval: the NUSD ticket, the placeholder merge in the tasks pod (it deletes an XRAS
  person), and the roster invitations at activation.

## Left for later

- **A cumulative ceiling.** SAM checks each request against the ceiling, not the project's
  running total across supplements; the approver applies the total.
- **A renewal above a ceiling.** A Renewal asks for the whole amount again, so a project
  already above its type's ceiling cannot renew at its current size on the form (on the
  2026-11-18 snapshot: 16 Small projects over 10 TB of Campaign Store, and a handful over
  the Derecho, Casper and Casper GPU ceilings). The operator's request editor is not bound.
- **A Large supplement's cap.** It is checked against the type's ceiling (500 million Derecho
  core-hours); the approval rules cap it near a Small's initial amount.
- **Extension length.** One year at most, or to the grant's end for Small; SAM checks only that
  the new end follows the current one.
- **Eligibility prose.** The approval rules admit nonprofits and related research
  institutions; the cards say U.S. universities.
- **`register_typeahead`'s `guard` parameter** has no caller now.
