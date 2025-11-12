# SAM Web UI

Flask-based web administration interface for the Service Allocation Management (SAM) database.

## Features

- **Flask-Admin Interface**: Full CRUD operations for all SAM database tables
- **Authentication**: Pluggable authentication system (stub, LDAP, SAML)
- **Role-Based Access Control (RBAC)**: Fine-grained permissions based on user roles
- **Dashboard**: Statistics and monitoring for projects, users, and allocations
- **Expiration Monitoring**: Track upcoming and expired project allocations
- **REST API**: Programmatic access to SAM data (in progress)
- **Bootstrap 4 UI**: Modern, responsive interface

## Quick Start

### 1. Install Dependencies

```bash
cd python/webui
pip install -r requirements.txt
```

### 2. Configure Database

The web UI uses the same database configuration as the main SAM package. Ensure your `.env` file is configured:

```bash
# .env
SAM_DB_SERVER=your-db-server
SAM_DB_USERNAME=your-username
SAM_DB_PASSWORD=your-password
```

### 3. Configure Development Roles

**For Read-Only Database (Development):**

Edit `python/webui/run.py` and add your username to the `DEV_ROLE_MAPPING`:

```python
app.config['DEV_ROLE_MAPPING'] = {
    'your_username': ['admin'],          # Full access
    # 'other_user': ['facility_manager'], # Limited access
    # 'test_user': ['user'],              # Read-only
}
```

Available roles: `admin`, `facility_manager`, `project_lead`, `user`, `analyst`

See `config_example.py` for detailed role/permission mappings.

**For Production Database (Future):**

When you have write access, create roles in the database:

```sql
-- Create test roles
INSERT INTO role (name, description) VALUES
  ('admin', 'System Administrator'),
  ('facility_manager', 'Facility Manager'),
  ('project_lead', 'Project Lead'),
  ('user', 'Regular User');

-- Assign admin role to your user
INSERT INTO role_user (role_id, user_id)
SELECT r.role_id, u.user_id
FROM role r, users u
WHERE r.name = 'admin' AND u.username = 'your_username';
```

Then uncomment the database role code in `auth/models.py` (line 93) and remove `DEV_ROLE_MAPPING`.

### 4. Run Development Server

```bash
python run.py
```

The application will be available at: `http://localhost:5050`

### 5. Login

**Development Mode (Stub Authentication):**
- Username: Any existing SAM username
- Password: Any non-empty password

The stub authenticator accepts any password for existing, active, non-locked users in the SAM database.

## Project Structure

```
python/webui/
├── README.md                   # This file
├── DESIGN.md                   # Detailed design documentation
├── requirements.txt            # Python dependencies
├── run.py                      # Application factory & dev server
├── extensions.py               # Flask extension instances
├── auth/                       # Authentication module
│   ├── models.py               # AuthUser (Flask-Login wrapper)
│   └── providers.py            # Auth providers (stub, LDAP, SAML)
├── blueprints/                 # Flask blueprints
│   ├── auth_bp.py              # Login/logout routes
│   └── admin/                  # Flask-Admin views
│       ├── views.py            # Custom admin index
│       ├── custom_model_views.py   # RBAC-enabled model views
│       ├── expiration_views.py     # Expiration dashboard
│       └── ...
├── utils/                      # Utilities
│   └── rbac.py                 # RBAC permissions and decorators
├── api/                        # REST API (in progress)
│   └── v1/
│       ├── users.py            # User API endpoints
│       └── projects.py         # Project API endpoints
└── templates/                  # Jinja2 templates
    ├── auth/
    │   ├── login.html
    │   └── profile.html
    └── admin/
        └── ...
```

## Authentication & Authorization

### Roles

The system supports the following roles:

- **admin**: Full system access
- **facility_manager**: Can manage projects, allocations, and resources
- **project_lead**: Can view projects and allocations
- **user**: Read-only access to projects and allocations
- **analyst**: Read-only access to everything with export capabilities

### Permissions

Permissions are defined in `webui/utils/rbac.py`:

- User management: `VIEW_USERS`, `EDIT_USERS`, `CREATE_USERS`, `DELETE_USERS`
- Project management: `VIEW_PROJECTS`, `EDIT_PROJECTS`, etc.
- Allocation management: `VIEW_ALLOCATIONS`, `EDIT_ALLOCATIONS`, etc.
- Reports: `VIEW_REPORTS`, `EXPORT_DATA`
- System: `MANAGE_ROLES`, `SYSTEM_ADMIN`

### Switching Authentication Providers

To switch from stub auth to enterprise auth, update `run.py`:

```python
# Development (stub)
app.config['AUTH_PROVIDER'] = 'stub'

# Production (LDAP)
app.config['AUTH_PROVIDER'] = 'ldap'
app.config['LDAP_URL'] = 'ldap://ldap.example.org'
app.config['LDAP_BASE_DN'] = 'ou=users,dc=example,dc=org'
```

**Note:** LDAP and SAML providers require implementation. See `DESIGN.md` for details.

## Main Features

### Dashboard

The main dashboard shows:
- Active user count
- Active project count
- Active resource count
- Upcoming expirations (next 30 days)
- Recently expired projects (last 90 days)

Access: `http://localhost:5050/admin/`

### User Management

View, search, and manage SAM users.

Access: `http://localhost:5050/admin/users/`

Permissions required: `VIEW_USERS` (view), `EDIT_USERS` (edit), `CREATE_USERS` (create)

### Project Management

View, search, and manage SAM projects.

Access: `http://localhost:5050/admin/projects/`

Permissions required: `VIEW_PROJECTS` (view), `EDIT_PROJECTS` (edit)

### Allocation Management

View and manage resource allocations.

Access: `http://localhost:5050/admin/allocations/`

Permissions required: `VIEW_ALLOCATIONS` (view), `EDIT_ALLOCATIONS` (edit)

### Expiration Monitoring

Comprehensive dashboard for monitoring project expirations:
- **Upcoming**: Projects expiring in next 7/30/60 days
- **Expired**: Projects expired in last 90 days
- **Abandoned Users**: Users whose only projects have expired

Features:
- Filter by facility and resource
- CSV export
- Direct links to projects and users

Access: `http://localhost:5050/admin/expirations/`

## REST API (In Progress)

### User Endpoints

```bash
# List users
GET /api/v1/users?page=1&per_page=50&search=smith

# Get user details
GET /api/v1/users/johndoe

# Get user's projects
GET /api/v1/users/johndoe/projects
```

### Project Endpoints

```bash
# List projects
GET /api/v1/projects?page=1&per_page=50

# Get project details
GET /api/v1/projects/ABC123

# Get project members
GET /api/v1/projects/ABC123/members

# Get project allocations
GET /api/v1/projects/ABC123/allocations

# Get expiring projects
GET /api/v1/projects/expiring?days=30
```

All API endpoints require authentication and appropriate permissions.

## Development

### Adding a New Permission

1. Add to `Permission` enum in `webui/utils/rbac.py`:
   ```python
   VIEW_SOMETHING = "view_something"
   ```

2. Add to role mappings in `ROLE_PERMISSIONS`:
   ```python
   "admin": [Permission.VIEW_SOMETHING, ...],
   ```

3. Use in views:
   ```python
   @require_permission(Permission.VIEW_SOMETHING)
   def view_something():
       ...
   ```

### Adding a New Flask-Admin View

In `webui/blueprints/admin/__init__.py`:

```python
from sam.models import MyModel
from .custom_model_views import SAMModelView

admin.add_view(SAMModelView(MyModel, db.session,
                            name='My Models',
                            endpoint='my_models',
                            category='My Category'))
```

### Adding RBAC to a View

```python
class MyModelAdmin(SAMModelView):
    def is_accessible(self):
        if not current_user.is_authenticated:
            return False
        from webui.utils.rbac import has_permission, Permission
        return has_permission(current_user, Permission.VIEW_SOMETHING)

    @property
    def can_edit(self):
        from webui.utils.rbac import has_permission, Permission
        return has_permission(current_user, Permission.EDIT_SOMETHING)
```

## Production Deployment

### Configuration

1. **Set SECRET_KEY**:
   ```python
   app.config['SECRET_KEY'] = os.environ['SECRET_KEY']  # From environment
   ```

2. **Enable secure cookies**:
   ```python
   app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only
   app.config['SESSION_COOKIE_HTTPONLY'] = True
   ```

3. **Configure authentication**:
   ```python
   app.config['AUTH_PROVIDER'] = 'ldap'  # or 'saml'
   ```

### Running with Gunicorn

```bash
# Basic
gunicorn -w 4 -b 0.0.0.0:5000 'webui.run:create_app()'

# With gevent workers (better for I/O)
gunicorn -w 4 -k gevent -b 0.0.0.0:5000 'webui.run:create_app()'

# With config file
gunicorn -c gunicorn_config.py 'webui.run:create_app()'
```

### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name sam.example.org;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /path/to/webui/static;
    }
}
```

## Testing

### Test Authentication

```bash
# Login as different users to test RBAC
curl -X POST http://localhost:5050/auth/login \
  -d "username=admin_user&password=test"
```

### Test API Endpoints

```bash
# Get users (requires authentication)
curl -H "Cookie: session=..." \
  http://localhost:5050/api/v1/users

# Get project details
curl -H "Cookie: session=..." \
  http://localhost:5050/api/v1/projects/ABC123
```

## Troubleshooting

### "Please log in to access this page"

This means authentication is required. Navigate to `/auth/login` to log in.

### "Forbidden - insufficient permissions"

Your user account doesn't have the required permission for this action. Check:
1. User has appropriate role assigned in `role_user` table
2. Role has appropriate permission in `ROLE_PERMISSIONS` dict

### Database connection errors

Check:
1. `.env` file has correct database credentials
2. Database server is accessible
3. `SAM_DB_*` environment variables are set

### "Role not found"

Ensure roles exist in the `role` table:
```sql
SELECT * FROM role;
```

Create missing roles:
```sql
INSERT INTO role (name, description) VALUES ('admin', 'Administrator');
```

## Further Documentation

- **DESIGN.md**: Detailed architecture and design decisions
- **requirements.txt**: Python package dependencies
- **Flask-Admin docs**: https://flask-admin.readthedocs.io/
- **Flask-Login docs**: https://flask-login.readthedocs.io/

## Support

For questions or issues:
1. Check the documentation in `DESIGN.md`
2. Review the code comments
3. Contact the SAM development team

## License

Copyright (c) 2025 SAM Project
