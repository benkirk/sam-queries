# xras/: the endpoints XRAS calls

`/api/xras/v1/...`, the API NSF's XRAS allocation system calls in SAM to look up
people and requests and to post allocation actions.

The key needs the legacy `ROLE_XRAS` role on its `api_credentials` row. A key
from the webapp config cannot carry one. Use `SAM_XRAS_USER` / `SAM_XRAS_PASS`
when that is a different key from your default.

The example reads `people/<username>` and `requests/user/<username>` only. The
`POST` routes create real allocations and are left out.
