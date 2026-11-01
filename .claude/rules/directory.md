---
paths:
  - "src/sam/integration/directory/**/*"
  - "src/sam/queries/directory.py"
  - "src/sam/manage/directory.py"
  - "src/sam/manage/directory_panel.py"
  - "src/webapp/dashboards/admin/directory_routes.py"
  - "tests/unit/directory/**/*"
  - "tests/factories/directory.py"
---

# The directory client

Loaded with `sam.integration.directory`. Plan and step list: `docs/plans/implemented/DIRECTORY_CLIENT.md`;
what the directory holds and how it maps to SAM: `docs/plans/LDAPSYNC_DIRECTORY_AFFILIATIONS.md`
Appendix C.

- **Shape.** `DirectoryReader` (ABC, `base.py`) answers typed records, `None`, or raises
  `DirectoryUnavailable`; `NullDirectory` when `DIRECTORY_URL` is empty; one `UCARDirectory`
  adapter (`ucar.py`) owns every attribute name, DN shape and the academic-status label map.
  No consumer reads an attribute name. `registry.py` and `ucar.py` are imported by path
  (`ldap3` stays out of the `sam` import graph; gate `test_notify_import_graph.py`).
- **Config** is read per call through `sam.integration._config`: `DIRECTORY_PROVIDER`,
  `DIRECTORY_URL` (empty = off), `DIRECTORY_CHECK_ON_SYNC`, and one write lever per kind,
  `DIRECTORY_APPLY_<KIND>` (`KINDS` in `base.py`), absent = off. Gate
  `tests/unit/gates/test_helm_directory_levers.py`.
- ⚠️ **The sync hook is fail-open and runs after the PUT's commit**
  (`webapp/api/ldapsync/sync.py::_directory_check`). The blueprint turns any escaping
  exception into a 500 and the daemon drops a rejected record until its next full reload, so
  the hook catches everything and logs one warning. `sam.queries.directory.run_check` never
  writes. A failed read sets the `outage` latch (60 s per worker) so a 1,300-PUT reset burst
  does not wait out a timeout each.
- ⚠️ **Dates.** `sam.dates.from_generalized_time`: a `-0600`/`-0700` stamp is Mountain wall
  time as written, never converted. Workday writes every position date as midnight `-0600`
  year-round; a strict conversion moves the winter ones to 23:00 the day before (580 of
  2,551 on 2026-10-15). Only `Z` stamps convert. Position ends compare by day.
- **Apply** (`sam.manage.directory`) consumes the compare categories, so the report and the
  write cannot drift; each kind's rules are the table in `DIRECTORY_CLIENT.md` section 5.
  Identity columns (`User.SYNC_FIELDS` excludes them), twins, undated-inactive entries,
  reopens and SAM-only history are withheld whatever the lever says. `dry_run` touches nothing.
  Emails mirror the directory through the sync's own `sync_emails` (SAM-only addresses deleted),
  except that a record with no email withholds the deletes.
- **Levers gate the hook only.** An operator's Sync (the user panel, `sam-admin user
  --directory-apply`) runs under `DirectoryConfig.for_operator()`: the person-side kinds
  (`PERSON_KINDS`) on whatever `DIRECTORY_APPLY_<KIND>` says; the withholds above still hold.
  Organization and institution Apply stay lever-gated.
- **Panels** (`sam/manage/directory_panel.py`, Flask-free so the CLI `--directory` views share
  it; routes in `dashboards/admin/directory_routes.py`) are operator-initiated reads gated on
  `VIEW_DIRECTORY` (NUSD, CSG); every Apply is `MANAGE_DIRECTORY`, withheld from the default roles: a card
  view never dials the directory. The GET is `reconcile_*(dry_run=True)`, so "would apply" /
  "withheld" comes from the same rules as the write. Groups (the admin project card, the adhoc
  group card) are report-only for different reasons, and the panel note says which: a project
  group flows SAM to directory through the sync daemon (its SAM side is the sync's own
  `project_group` row); an adhoc group is authored in Google Groups and reaches SAM through
  the directory, a path that is not live yet. Panel ids carry the entity id because the user
  and project cards also open inside a modal. The Organizations card is cached per user: a
  config flip shows after `sam-admin cache --refresh`.
- **CLI and the nightly report.** `sam-search user|project <k> --directory` render the same
  views as the panels (`cli/core/directory.py` flattens them; the envelope key is absent when
  unconfigured, `available: false` when down); `sam-admin user <u> --directory-apply [--dry-run]`
  is `reconcile_from_directory` in `management_transaction`, JSON refused for the live write.
  `sam-admin directory report [--kind K] [--limit N]` and the `directory_report` task (03:00
  Mountain, `needs=('sam',)`) run `sam/queries/directory_sweep.py`: `reader.records(kind)` pages
  a whole subtree, SAM's side is one query per table (the sync's own group lists), counts by
  category with capped keys. ⚠️ The task is named in prod's `SAM_TASKS_DISABLED` (fail-open
  list) and the CronJob forwards the four `DIRECTORY_*` read keys by name; never a
  `DIRECTORY_APPLY_<KIND>` (`helm/tests/test-cronjob-render.sh`).
- **Logs** carry usernames and row ids, like the rest of the ldapsync logger; personal names,
  email addresses and phone numbers are counted in a `Discrepancy`, never valued.
- **Tests** never open a socket: `tests/conftest.py::_no_outbound_ldap` guards
  `ldap3`'s strategy `open`; use `FakeDirectory` (`tests/factories/directory.py`) and
  monkeypatch `sam.integration.directory.registry.reader_from_environment`. The `directory`
  marker is derived from `tests/unit/directory/`.
