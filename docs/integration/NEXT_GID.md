# Legacy SAM GID Assignment and Integration

## Overview
During the migration to the new `@project_samuel` architecture, it was observed that the new project creation form allows manual input for a project's `unix_gid`. This approach is fragile and error-prone, as it risks collisions. 

An investigation into the legacy Java SAM codebase revealed that **SAM does not locally generate or calculate the next available GID**. Instead, it delegates GID assignment entirely to an external Project Database (PDB) / ID Service.

## Legacy Workflow

1. **Initial Project Creation:** Projects are created locally in SAM (e.g., via `DefaultAddProjectCommand`) with the `unix_gid` field left as `NULL`. The legacy UI (`projectDetails.xhtml`) does not expose a GID input field.
2. **Synchronization Trigger:** SAM uses a background or event-triggered synchronization process to push the new project to the external PDB.
3. **External Assignment:** The `PdbProjectAsGroupDetailSynchronizer` issues a REST `PUT` request to the PDB. The external PDB acts as the source of truth, safely determining the next available GID across the broader environment (e.g., LDAP) and assigning it to the group.
4. **Local Update:** The `PdbProjectAsGroupSynchronizer` receives the assigned GID in the REST response and updates the local SAM `project` record (using `project.setUnixGid(groupDetail.getGid())`).

## Key Legacy Classes
- `edu.ucar.cisl.sam.project.sync.service.pdb.PdbProjectAsGroupSynchronizer`
- `edu.ucar.cisl.sam.project.sync.service.pdb.PdbProjectAsGroupDetailSynchronizer`
- `edu.ucar.cisl.sam.project.sync.service.pdb.PdbGroupRetriever`
- `edu.ucar.cisl.sam.user.idservice.command.DefaultSyncIdServiceGroupCommand`

## Implications for `@project_samuel`

- **Fragility of Manual Input:** Allowing admins to manually enter a `unix_gid` in the new HTMX form risks collisions with existing system groups or other projects in the broader infrastructure.
- **No Local Sequence:** Unlike project codes (`next_projcode` in `sam/samuel/projects/projects.py`), there is no existing logic in the database schema (e.g., `MAX(unix_gid) + 1`) or the SAM application to reliably determine the next GID.
- **Recommendation:** `@project_samuel` should replicate the legacy automated behavior. It should either:
  1. Leave the GID blank upon creation and retrieve it asynchronously via an API integration with the identity service (PDB).
  2. Prompt the identity service synchronously for a new GID during the project creation flow.
