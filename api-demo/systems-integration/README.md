# systems-integration/: the provisioning and scheduler feeds

The APIs that LDAP, provisioning and PBS tooling poll. Their JSON keeps the
camelCase shapes of the legacy Java SAM service they replaced, so existing
consumers switched hosts without code changes.

| Script | Endpoint (`/api/v1/...`) | What it carries |
|---|---|---|
| `directory_access` | `directory_access/[<branch>]` | unix accounts and groups per access branch |
| `project_access` | `project_access/[<branch>]` | project groups, status and expiry per branch |
| `fstree_access` | `fstree_access/[<resource>]`, `.../projects/<projcode>`, `.../users/<username>` | the fair-share tree with allocation, usage and balance |
| `queue` | `queue/[<resource>]` | batch queues and their wallclock limits |
| `wallclock_exemption` | `wallclock_exemption/[<resource>]` | users exempt from a queue's limit |
| `disk_quota` | `disk_quota` (one list, every resource) | project storage quotas and paths |

Every one caches its response for 5 minutes and has a `POST .../refresh` that
drops the cache; each script ends with it. Resource names with a space are
URL-encoded (`Derecho%20GPU`). A production key needs the
`api_systems_integration` role. The full field reference is SAM's
`docs/apis/SYSTEMS_INTEGRATION_APIs.md`.
