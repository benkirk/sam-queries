# External contacts: contract PIs and monitors out of `users`

**Status: built 2026-11-05** on the `external-contacts` branch (one sprint PR to staging).
Ben accepted the legacy SAM risk below instead of a coordinated legacy change. The DDL
(`scripts/sql/create_external_contact.sql`, `alter_contract_external_contacts.sql`, each with
a `_pg.sql` twin) is applied by hand before the code rolls. The XRAS lever
`XRAS_AUTO_CONTRACTS` is on in samuel-dev and off in production until the dev proof.

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
(`docs/plans/implemented/XRAS_CONTRACT_BLOCKERS.md`), and creating that row needed a SAM-user PI.

## Measurements

From `docs/plans/implemented/CONTRACT_IMPORTING_PLAN.md` § F2, which first proposed this:

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

## As built

```
external_contact
  external_contact_id PK, name NOT NULL, email NULL (unique, stored lower-cased),
  organization NULL, user_id NULL (unique), creation_time, modified_time
contract
  principal_investigator_user_id  NOT NULL -> NULL
  + pi_contact_id, monitor_contact_id   (indexed, no FK constraint)
```

- **A SAM user goes on the user FK, exactly as before**; a person who is not one goes on the
  contact column. So legacy's panels, the lifecycle keep-alive (`sam/queries/user_lifecycle.py`)
  and purge (`sam/manage/purge.py`) are unchanged for SAM users. Every contract has a PI by
  one column or the other (`Contract.create`). There is no mass backfill: contacts are made
  on demand.
- `ExternalContact` (`sam/projects/contracts.py`): `find_or_create` matches on email; a name
  alone matches only an email-less contact. `link_user` fills the empty user FKs of a
  contact's contracts. `Contract.pi_person` / `monitor_person` are the one read, and the user
  wins.
- `sam/manage/contracts.py` places every person:
  - `person_columns`: a typed contact whose email a SAM user holds goes on the user FK.
  - `plan_contract_from_award` / `apply_contract_plan`: people resolve against the request's
    roster first, then any SAM user, then become contacts. The plan refuses an incomplete
    award, a number SAM already matches, an award with no PI, and an award whose PI's
    surname differs from the request's.
- Forms: the create form takes a user or a contact PI, and both forms take a contact monitor.
  The award lookup prefills the contact fields when NSF names someone with no SAM user.

### XRAS: a post that creates its contract

- **The sweep** (`xras_sweep`) fetches the NSF award of each pending grant SAM has no
  contract for into the Redis award cache, 25 fetches a run. It also creates the contract
  (`contract_targets`, `_create_contracts`) for every NSF grant an approved, unapplied
  request cites: the wire grants of each request still owed its push, and the contract
  blockers the Remediations index recorded. A light hour re-checks each unblocked request
  into the index. Then it runs `ExternalContact.link_matching_users`.
- **The post** (`plan_contracts`, New and Update) reads that cache only (`cached_award`), so
  XRAS's POST never waits on NSF. A cached award becomes a planned contract instead of a
  422, written in `execute` inside the post's transaction.
- **The identity race.** On 2026-11-05 a contract was made only as the last step of a post
  that otherwise applied, so an incoming user who is also the grant PI could not become a
  contact. Ben, 2026-11-07: make it early, and close the race at the post. A post that
  applies links each unlinked PI or monitor contact that one roster user is, by the
  contact's own name or email (`plan_contact_links`, planned in assembly so the preflight
  shows it). The allocation PI is never assumed to be the grant's: they can differ, and
  then nothing links.
- **The Remediations strip** offers one-click Create for every NSF target. The ticket task
  comments "may now post" on a request whose missing contract now exists.
- **The lever** `XRAS_AUTO_CONTRACTS` covers the post and the sweep, on in both envs.
- `reports` gives only `fundingAgencyId`, so the sweep and Re-check name NSF
  (`with_named_agencies`, `xras_api/vocabulary.py`) outside `sam/xras`, which never imports
  the outbound client.

## Legacy risk, accepted (Ben, 2026-11-05)

Measured from legacy's source and 30 days of sam-tomcat access logs:

| Legacy surface | Behavior once an active contract has no PI user | Use, 30 days to 2026-11-05 |
|---|---|---|
| `/app/contract/edit` (index and search) | 500 for everyone: `DefaultContractDTOTransformer.java:25` dereferences the PI | 2 sessions (Oct 15, which created 2289 GCP-2555988; Oct 21) |
| Project dashboard contracts panel | the contract is silently absent (inner join, `HibernateContractRepository.findProjectContracts`) | 1,129 dashboard hits |
| AMIE contract API | 500 on a matching grant | 0; AMIE is out of scope |
| `userlifecycle/activeusers`, `collabexpiryupdates` | whole response 500 (`DefaultUserLifecycleManager.java:93,97`) | 0; ldapsync answers on SAMuel since 2026-11-02 |
| Boot, Hibernate, Flyway | unaffected: no hbm2ddl, Flyway not run at boot, no views or triggers on the columns | |

Legacy cannot write a NULL PI (its save requires both a user PI and a user monitor) and never
writes the new columns, so data integrity holds. `scripts/tomcat_watch.sh` WARNs on any
`/app/contract/` hit: that person belongs on SAMuel's Contracts page.

### Legacy source notes

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
locally). Ben accepted it rather than change legacy: nothing calls those endpoints on legacy
since the ldapsync repoint.

The lifecycle code also shows what "contract PI" means to legacy: an association
that keeps a user in use until the contract ends. A contract with no PI user
holds nobody, which is the intent.

## Rollout

1. Point whoever still uses legacy's `/app/contract/edit` at SAMuel's Contracts page.
2. Apply the DDL on sam_dev (Postgres) before the staging push, and on prod MySQL before the
   promotion rolls: the ORM selects the new columns on every contract load.
3. Prove it on samuel-dev with XRAS-dev: an NSF award whose PI is not a SAM user.
4. Arm `XRAS_AUTO_CONTRACTS` in production (`helm/values.yaml`, webapp and tasks together).
5. Later, when legacy is gone: drop the purely external `users` rows that exist only as PIs
   or monitors (`docs/plans/SCHEMA_RETIREMENT.md`).

## Open

- `organization` stays free text: program officers map to no `institution`.
- No merge path for duplicate contacts yet; email is the key, so one changed email makes a
  second contact.
