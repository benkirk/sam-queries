# External contacts: contract PIs and monitors out of `users`

Status: **proposal**, not scheduled. Needs a coordinated change with legacy SAM,
which still runs against the shared database. No SAM-side schema change lands
out of step with it.

## The problem

`contract.principal_investigator_user_id` is `NOT NULL` with an FK to `users`, and
`contract.contract_monitor_user_id` is a nullable FK to `users`. Every contract
contact therefore has to be a row in UCAR's identity table, with the UPID,
`unix_uid`, POSIX groups, IDMS sync and RBAC that come with it, whether or not
they will ever use a computer here.

Two cases make this hurt:

- **The lead is not the PI.** A project lead is often a collaborator who
  references someone else's award. The contract PI then has to exist in `users`
  even though they need no HPC access. The alternative, putting the lead in the
  PI field, corrupts the data `sam/integration/awards/audit.py` compares
  against NSF.
- **The monitor is the funding agency's program officer.** They never need SAM
  access, yet each one is a `users` row.

There is no sanctioned way to add such a person to `users` short of an ad hoc
backdoor, and that is not an option. The same requirement blocks XRAS: a
request whose award has no `contract` row stalls
(`XRAS_CONTRACT_BLOCKERS.md`), and creating that row today needs a SAM-user PI.

## Measurements

From `implemented/CONTRACT_IMPORTING_PLAN.md` § F2, which first proposed this:

- 1,608 users are a contract PI or monitor; 881 of them are inactive.
- 414 are purely external: no `account_user` row, never a project lead or admin.
- 314 of the 387 distinct monitors (81%) are purely external.

Re-measured 2026-09-25 on a local snapshot (aggregates only):

| | |
|---|---|
| contracts with a linked project whose lead is not the contract PI | 783 |
| of those, contracts whose PI has never been on an account | 163 (21 active) |
| users who are a contract PI and nothing else (no account, never lead/admin) | 107 |
| distinct monitors never on an account | 325 of 388 |
| active contracts with no monitor | 7 |

"Lead is not the PI" is common, not rare. The friction case is the narrower
"PI has no SAM presence at all" row.

## Proposed shape

```
external_contact
  external_contact_id   PK
  name                  NOT NULL
  email                 NULL, unique when present (case-insensitive)
  organization          NULL, free text ("NSF AGS", "University of X")
  user_id               NULL, FK users, unique; set only when the person IS a SAM user
  creation_time / modified_time

contract
  + pi_contact_id       NULL, FK external_contact
  + monitor_contact_id  NULL, FK external_contact
```

- **Role lives on the contract, not the contact.** F2 listed a `role` column,
  but "PI" and "monitor" describe a relationship to one contract. The column
  that references the contact already says which.
- **`user_id` is an optional link.** When the PI is also a SAM user (a lead, a
  collaborator), the contact points at them and the UI keeps the entity link to
  their user card. A program officer has no link and no identity baggage.
- **Email is the natural key.** NSF supplies it, and `resolve_person()`
  (`sam/integration/awards/people.py`) already matches on email first. Creating
  a contact is harmless in a way creating a `User` never was, so the award
  prefill can find-or-create a contact instead of today's
  "suggest, don't impose" hint.
- **The old user FKs stay** during coexistence. Legacy reads them.

### Considered and rejected

- **Free-text columns on `contract`** (`pi_name`, `pi_email`, `monitor_name`,
  `monitor_email`). Simpler, but a program officer appears on about six
  contracts on average and each copy drifts independently when an email
  changes. No way to ask "every contract for this officer" except a text match.
- **A placeholder "unknown PI" user.** A synthetic identity row; exactly what
  this proposal removes.
- **Putting the lead in the PI field.** Wrong data, and the NSF award audit
  flags every one.

## Where SAM changes

Most display code already tolerates a missing PI: `contract_user_link` renders
nothing for `None`, and the search-results, edit-form and JSON-schema paths
guard it. The PI filter in `Contract.search_by_pattern` joins the user only when filtering
by PI.

To change:

- `sam/projects/contracts.py`: new relationships; `create()`/`update()` take
  contacts; PI no longer required.
- `sam/schemas/forms/orgs.py`: `principal_investigator_user_id` is
  `required=True`; becomes a contact picker.
- `webapp/dashboards/admin/contracts_routes.py`: FK checks and the award
  prefill hint.
- `sam/integration/awards/people.py`: `resolve_person()` returns an
  `ExternalContact` (find or create), with `user_id` set when an email matches
  a SAM user. This is the seam F2 kept clean for this change.
- `sam/integration/awards/audit.py`: compares contact name/email with the award.
- `sam/queries/contract_audit.py`: add a "missing PI" finding.
- `tests/integration/test_schema_validation.py`: follows the DDL.

## Legacy SAM (`legacy_sam/`) dependencies

Java paths are under `legacy_sam/src/main/java/edu/ucar/cisl/sam/`.

| Location | Behavior with a NULL PI |
|---|---|
| `legacy_sam/src/main/resources/hibernate/Contract.hbm.xml` (`not-null="true"`) | Hibernate refuses to save; legacy cannot edit such a contract. Reads are fine. |
| `presentation/gui/primefaces/ContractBean.java:140,152` | `getPrincipalInvestigator().getUsername()` NPE on legacy create/update. |
| `amie/query/DefaultAMIEContractQuery.java:55` | NPE. Confirm whether AMIE is still live. |
| `service/userlifecycle/DefaultUserLifecycleManager.java:93,97` | `.equals()` on a nullable id: NPE when evaluating the monitor of a PI-less contract. |
| JSF views (`contractDetails.xhtml`, results templates) | Blank name; EL tolerates null. Cosmetic. |

`DefaultUserLifecycleManager.java:93` is **already a live bug**: evaluating the
PI of an active contract with no monitor throws today (7 such contracts
locally). Null-guarding lines 93 and 97 is worth doing regardless, and it is
the prerequisite for everything below.

The lifecycle code also shows what "contract PI" means to legacy: an association
that keeps a user in use until the contract ends. A contract with no PI user
holds nobody, which is the intent.

## Rollout

1. **Additive, legacy-safe.** Create `external_contact` and the two nullable
   contract columns. Backfill one contact per distinct PI/monitor user (about
   1,600), each with `user_id` set. Hibernate does not map the new objects, so
   legacy is unaffected.
2. **New SAM reads and writes contacts.** Display, audit and search use the
   contact, falling back to the linked user. New SAM still writes the user FKs
   while legacy reads them.
3. **Monitor goes contact-only** once legacy null-guards
   `DefaultUserLifecycleManager.java:93,97`.
4. **PI goes contact-only** once legacy relaxes `Contract.hbm.xml`, fixes
   `ContractBean.java:140,152` and decides AMIE, and the DBA makes
   `principal_investigator_user_id` nullable. Until then a PI with no SAM user
   still blocks contract creation; steps 1 and 2 fix display, not that.
5. **Retirement.** When legacy stops reading the old columns, drop them and
   retire the purely external `users` rows (see `SCHEMA_RETIREMENT.md`).

## Open questions

- Does anyone still create or edit contracts in the legacy UI, or does legacy
  only read them? If only reads, step 4 is the null guards alone.
- Is AMIE (`DefaultAMIEContractQuery`) still exercised?
- `organization`: free text, or an FK to `institution` for PIs? Program officers
  do not map to institutions.
- A merge path for duplicate contacts when an email changes?
- During steps 2 and 3, does new SAM keep the user FKs in sync, or does legacy
  only read?
