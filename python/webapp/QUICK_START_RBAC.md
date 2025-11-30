# Quick Start Guide: Testing RBAC with Read-Only Database

This guide shows how to test the authentication and RBAC system with a read-only database connection.

## Step 1: Configure Dev Roles

Edit `python/webapp/run.py` around line 27-37 and add your SAM username:

```python
app.config['DEV_ROLE_MAPPING'] = {
    'your_sam_username': ['admin'],  # Replace with your actual username
}
```

## Step 2: Start the Server

```bash
cd python/webapp
python run.py
```

You should see:
```
* Running on http://127.0.0.1:5050
```

## Step 3: Login

1. Navigate to `http://localhost:5050`
2. You'll be redirected to the login page
3. Enter:
   - **Username**: Your SAM username (from step 1)
   - **Password**: Any non-empty text (e.g., "test")
4. Click "Login"

## Step 4: Verify Your Roles

After login, go to `http://localhost:5050/auth/profile`

You should see:
- Your username
- Your assigned roles (e.g., "admin")
- All permissions granted to that role

## Step 5: Test RBAC

### As Admin (full access):
- Navigate to `/admin` - Should see full dashboard
- Click "Users" - Can view, edit, create, delete users
- Click "Projects" - Can view, edit, create, delete projects
- Click "Allocations" - Can view and edit allocations

### Testing Other Roles:

Change your role in `run.py`:

```python
app.config['DEV_ROLE_MAPPING'] = {
    'your_username': ['facility_manager'],  # Try different roles
}
```

Restart the server and login again.

#### As facility_manager:
- Can view and edit projects
- Can view and edit allocations
- Can view users (but not edit them)
- Can export data

#### As project_lead:
- Can view projects and allocations
- Cannot edit anything
- Cannot access user management

#### As user:
- Very limited read-only access
- Can view projects they're on
- Can view allocations

## Step 6: Test Multiple Users

You can configure multiple users with different roles:

```python
app.config['DEV_ROLE_MAPPING'] = {
    'admin_user': ['admin'],
    'manager_user': ['facility_manager'],
    'lead_user': ['project_lead'],
    'regular_user': ['user'],
}
```

Login with each username to test different permission levels.

## Role → Permission Reference

| What You Can Do | admin | facility_manager | project_lead | user |
|----------------|-------|------------------|--------------|------|
| View Users | ✓ | ✓ | ✓ | ✗ |
| Edit Users | ✓ | ✗ | ✗ | ✗ |
| View Projects | ✓ | ✓ | ✓ | ✓ |
| Edit Projects | ✓ | ✓ | ✗ | ✗ |
| View Allocations | ✓ | ✓ | ✓ | ✓ |
| Edit Allocations | ✓ | ✓ | ✗ | ✗ |
| View Resources | ✓ | ✓ | ✗ | ✗ |
| Edit Resources | ✓ | ✓ | ✗ | ✗ |
| Export Data | ✓ | ✓ | ✗ | ✗ |
| System Stats | ✓ | ✓ | ✗ | ✗ |

## Testing Checklist

- [ ] Can login with any SAM username
- [ ] Profile page shows correct roles
- [ ] Admin can access all views
- [ ] Non-admin has limited access
- [ ] Edit buttons hidden for users without permissions
- [ ] Unauthorized access redirects or shows error
- [ ] Logout works correctly

## Troubleshooting

### "No roles assigned" on profile page
- Check that username in `DEV_ROLE_MAPPING` exactly matches your SAM username
- Restart the Flask server after changing configuration

### "Please log in to access this page"
- Session expired, login again
- Or authentication is working correctly!

### Can see views but can't edit/create/delete
- Your role doesn't have those permissions
- Check role permissions in `config_example.py`

### "Invalid username or password"
- Username doesn't exist in SAM database
- User account is locked or inactive

## Next Steps

Once RBAC testing is complete:

1. **For Production**: Transition to database roles
   - Create `role` and `role_user` tables
   - Uncomment database role code in `auth/models.py`
   - Remove or clear `DEV_ROLE_MAPPING`

2. **For Enterprise Auth**: Switch from stub to LDAP/SAML
   - Implement `LDAPAuthProvider` or `SAMLAuthProvider`
   - Update `AUTH_PROVIDER` config
   - Map LDAP/SAML groups to SAM roles

3. **Customize Permissions**: Edit `utils/rbac.py`
   - Add new permissions
   - Adjust role mappings
   - Create custom permission checks
