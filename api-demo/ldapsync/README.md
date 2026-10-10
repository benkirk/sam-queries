# ldapsync/: the identity-sync feed

`/api/protected/admin/ldapsync/...`, the contract `sam-ldap-syncd` reads to keep
LDAP in step with SAM. It answers at legacy SAM's paths and in legacy's shapes.

The key needs the legacy `ROLE_API_ADMIN` role on its `api_credentials` row. A
key from the webapp config cannot carry one. Use `SAM_LDAPSYNC_USER` /
`SAM_LDAPSYNC_PASS` when that is a different key from your default.

The example reads `status`, `institution`, `organization`, `groupTag` and one
`user/<unix_uid>`. The full `user` and `group` lists are large and are left out;
the `PUT` side belongs to the sync daemon.
