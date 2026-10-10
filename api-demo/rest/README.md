# rest/: the core v1 REST API

`/api/v1/...`, JSON in and out. Each GET takes an API key or a browser session.

| Script | Endpoints | Permission a production key needs |
|---|---|---|
| `health` | `/health/live`, `/health/ready` | none (no key at all) |
| `users` | `/users/`, `/users/search`, `/users/<username>`, `/users/<username>/projects` | `VIEW_USERS`, `VIEW_PROJECTS` |
| `projects` | `/projects/`, `/projects/<projcode>`, `.../members`, `.../allocations`, `/projects/expiring`, `/projects/recently_expired` | `VIEW_PROJECTS`, `VIEW_PROJECT_MEMBERS`, `VIEW_ALLOCATIONS` |
| `allocations` | `/allocations/<allocation_id>` | `VIEW_ALLOCATIONS` |
| `charges` | `/projects/<projcode>/charges`, `.../charges/summary` | `VIEW_ALLOCATIONS` |
| `status` | `/status/{derecho,casper,jupyterhub}/latest`, `/status/outages`, `/status/reservations` | none beyond a valid key |
| `cache_refresh` | `POST /admin/cache/refresh[?category=...]` (a write) | `SYSTEM_ADMIN` |

A key is admitted by permission alone, never by project membership. The
project and user the examples look up are discovered from the list calls;
set `SAM_DEMO_PROJCODE` / `SAM_DEMO_USERNAME` to choose your own.
