# Development Configuration Example for SAM Web UI
#
# This file shows example configurations for development with a read-only database.
# Copy the sections you need into run.py's create_app() function.

# ============================================================================
# DEVELOPMENT ROLE MAPPING (Read-Only Database)
# ============================================================================
# Since you have a read-only database connection, you can't insert roles
# into the role/role_user tables. Instead, hard-code role assignments here.
#
# When you're ready to use database roles:
# 1. Comment out or remove DEV_ROLE_MAPPING
# 2. Uncomment the database role code in auth/models.py (line 93)
# 3. Insert roles into your database

DEV_ROLE_MAPPING = {
    # Format: 'username': ['role1', 'role2', ...]

    # Available roles:
    # - 'admin': Full system access (all permissions)
    # - 'facility_manager': Manage projects, allocations, resources
    # - 'project_lead': View projects and allocations
    # - 'user': Basic read-only access
    # - 'analyst': Read-only with export capabilities

    # Examples:
    'admin_user': ['admin'],
    'facility_manager_user': ['facility_manager'],
    'project_lead_user': ['project_lead'],
    'regular_user': ['user'],
    'analyst_user': ['analyst'],

    # Multiple roles:
    'power_user': ['facility_manager', 'analyst'],

    # Add your actual SAM usernames here:
    # 'your_username': ['admin'],
}

# ============================================================================
# PERMISSION REFERENCE
# ============================================================================
# Each role has these permissions (defined in utils/rbac.py):
#
# admin:
#   - All permissions (full access)
#
# facility_manager:
#   - VIEW_USERS, VIEW_PROJECTS, EDIT_PROJECTS, CREATE_PROJECTS
#   - VIEW_ALLOCATIONS, EDIT_ALLOCATIONS, CREATE_ALLOCATIONS
#   - VIEW_RESOURCES, EDIT_RESOURCES
#   - VIEW_REPORTS, VIEW_CHARGE_SUMMARIES, EXPORT_DATA
#   - VIEW_SYSTEM_STATS
#
# project_lead:
#   - VIEW_USERS, VIEW_PROJECTS, VIEW_PROJECT_MEMBERS
#   - VIEW_ALLOCATIONS, VIEW_REPORTS, VIEW_CHARGE_SUMMARIES
#
# user:
#   - VIEW_PROJECTS, VIEW_ALLOCATIONS, VIEW_CHARGE_SUMMARIES
#
# analyst:
#   - VIEW_USERS, VIEW_PROJECTS, VIEW_PROJECT_MEMBERS
#   - VIEW_ALLOCATIONS, VIEW_RESOURCES
#   - VIEW_REPORTS, VIEW_CHARGE_SUMMARIES, EXPORT_DATA
#   - VIEW_SYSTEM_STATS

# ============================================================================
# TESTING DIFFERENT ROLES
# ============================================================================
# 1. Add your username to DEV_ROLE_MAPPING with desired role
# 2. Restart the Flask app
# 3. Login with your username (any password works with stub auth)
# 4. Check /auth/profile to see your assigned roles and permissions
# 5. Try accessing different admin views to test RBAC

# ============================================================================
# TRANSITION TO DATABASE ROLES (Future)
# ============================================================================
# When ready to use database-backed roles:
#
# 1. Create roles in database:
#    INSERT INTO role (name, description) VALUES
#      ('admin', 'System Administrator'),
#      ('facility_manager', 'Facility Manager'),
#      ('project_lead', 'Project Lead'),
#      ('user', 'Regular User');
#
# 2. Assign roles to users:
#    INSERT INTO role_user (role_id, user_id)
#    SELECT r.role_id, u.user_id
#    FROM role r, users u
#    WHERE r.name = 'admin' AND u.username = 'your_username';
#
# 3. In auth/models.py, uncomment line 93:
#    self._roles = {ra.role.name for ra in self.sam_user.role_assignments}
#
# 4. In run.py, set DEV_ROLE_MAPPING = {} or remove it
#
# 5. Restart the Flask app - roles will now come from database
