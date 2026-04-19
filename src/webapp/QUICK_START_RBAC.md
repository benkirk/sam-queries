# Quick Start Guide: Testing RBAC

This guide shows how to test the authentication and RBAC system locally.

## How permissions resolve

A user's permission set is the union of:

1. **POSIX group bundles** — for each group the user belongs to
   (`get_user_group_access()`), if that group name has a key in
   `GROUP_PERMISSIONS` (`webapp/utils/rbac.py`), the bundle's
   permissions are added. Currently defined bundles: `csg`, `nusd`,
   `hsg`.
2. **Per-user overrides** — `USER_PERMISSION_OVERRIDES` in
   `webapp/utils/rbac.py` grants specific `Permission` enum members to
   individual usernames on top of their group bundles.

There is **no** dependency on the SAM `role_user` / `role` tables and
no dev-only bypass mapping — dev, test, and production all use the
same two-layer model.

## Step 1: Grant yourself permissions

Edit `src/webapp/utils/rbac.py` and add your SAM username to
`USER_PERMISSION_OVERRIDES`:

```python
USER_PERMISSION_OVERRIDES = {
    'your_sam_username': set(Permission),  # full admin equivalent
    # 'view_only_user': {Permission.VIEW_PROJECTS, Permission.VIEW_ALLOCATIONS},
}
```

## Step 2: Start the server

```bash
docker compose up webdev --watch
```

The application is available at `http://localhost:5050`.

## Step 3: Login

The dev login page shows a "Quick Login" panel of test usernames
(driven by `DEV_QUICK_LOGIN_USERS` in `webapp/config.py:DevelopmentConfig`).
Click any username — stub auth accepts any password — then verify the
expected tabs appear in the navbar.

## Step 4: Verify your permissions

Visit `http://localhost:5050/auth/profile` to see your username, the
`roles` set (POSIX-group names with `GROUP_PERMISSIONS` bundles), and
the resolved permission set.

## Step 5: Test individual permissions

To test a narrower permission set without disturbing real group
membership, point `USER_PERMISSION_OVERRIDES` at a specific subset:

```python
USER_PERMISSION_OVERRIDES = {
    'your_username': {
        Permission.VIEW_PROJECTS,
        Permission.VIEW_ALLOCATIONS,
        Permission.EXPORT_DATA,
    },
}
```

Restart the server (or rely on `--watch` to pick up the change) and
re-login to verify only the matching tabs and action buttons appear.

## Reference: bundle contents

The current real-group bundles in `GROUP_PERMISSIONS` are documented
inline in `webapp/utils/rbac.py`. They are still provisional — confirm
the actual `csg` / `nusd` / `hsg` permission contents with the team
before relying on them in production.

## Troubleshooting

- **"No roles assigned" on profile page** — your username has no POSIX
  group with a `GROUP_PERMISSIONS` bundle and no
  `USER_PERMISSION_OVERRIDES` entry. Add one or check group membership
  in `adhoc_system_account_entry`.
- **Edit buttons missing** — the role/permission gating macros in
  `templates/dashboards/fragments/action_buttons.html` are hiding them.
  Check what permissions the action requires (route decorator or
  `@require_project_permission`) and confirm your user has them.
- **"Invalid username or password"** — username doesn't exist in SAM
  or the account is locked/inactive.
