# SAM Web UI

Flask-based web administration interface for the Systems Accounting Manager (SAM) database.

## Features

- **Flask-Admin Interface**: Full CRUD operations for all SAM database tables
- **Authentication**: Pluggable authentication system (stub, LDAP, SAML)
- **Role-Based Access Control (RBAC)**: Fine-grained permissions based on user roles
- **Dashboard**: Statistics and monitoring for projects, users, and allocations
- **Expiration Monitoring**: Track upcoming and expired project allocations
- **REST API**: Comprehensive JSON API for users, projects, allocations, and expirations
- **Bootstrap 4 UI**: Modern, responsive interface

## Quick Start

### 1. Install Dependencies

```bash
cd src/webapp
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

Edit `src/webapp/run.py` and add your username to the `DEV_ROLE_MAPPING`:

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
src/webapp/
├── README.md                   # This file
├── DESIGN.md                   # Detailed design documentation
├── requirements.txt            # Python dependencies
├── run.py                      # Application factory & dev server
├── extensions.py               # Flask extension instances
├── auth/                       # Authentication module
│   ├── models.py               # AuthUser (Flask-Login wrapper)
│   ├── providers.py            # Auth providers (stub, LDAP, SAML)
│   └── blueprint.py            # Login/logout routes
├── admin/                      # Flask-Admin views
│   ├── views.py                # Custom admin index
│   ├── custom_model_views.py   # RBAC-enabled model views
│   ├── expiration_views.py     # Expiration dashboard
│   └── ...
├── dashboards/                 # Dashboard blueprints
│   ├── user/                   # User dashboard
│   │   └── blueprint.py        # User dashboard routes
│   └── status/                 # Status dashboard (future)
├── utils/                      # Utilities
│   └── rbac.py                 # RBAC permissions and decorators
├── api/                        # REST API v1
│   └── v1/
│       ├── __init__.py         # API package initialization
│       ├── users.py            # User endpoints (list, details, projects)
│       ├── projects.py         # Project endpoints (list, details, members,
│       │                       #   allocations, expiring, recently_expired)
│       └── charges.py          # Charge/balance endpoints
├── schemas/                    # Marshmallow-SQLAlchemy schemas
│   ├── __init__.py             # Base schema + exports
│   ├── user.py                 # User schemas (3 tiers)
│   ├── project.py              # Project schemas (3 tiers)
│   ├── resource.py             # Resource schemas
│   ├── allocation.py           # Allocation/balance schemas ⭐ KEY
│   └── charges.py              # Charge summary schemas
└── templates/                  # Jinja2 templates
    ├── auth/
    │   ├── login.html
    │   └── profile.html
    └── admin/
        └── ...
```

## API Serialization with Marshmallow

The REST API uses **marshmallow-sqlalchemy** for declarative serialization, providing:
- Type-safe JSON serialization
- Automatic datetime formatting
- Nested relationship handling
- Calculated fields (e.g., allocation balances)

### Schema Organization

Schemas follow a **three-tier strategy** for optimal performance:

1. **Full Schemas** - All fields + relationships (e.g., `UserSchema`)
2. **List Schemas** - Lightweight for collections (e.g., `UserListSchema`)
3. **Summary Schemas** - Minimal for references (e.g., `UserSummarySchema`)

### Quick Example

```python
from sam.schemas import UserSchema, ProjectListSchema

# Serialize single object
user_data = UserSchema().dump(user)

# Serialize collection
projects_data = ProjectListSchema(many=True).dump(projects)
```

### Key Schemas

**AllocationWithUsageSchema** ⭐ - Calculates real-time allocation balances:
- `used`: Total charges from summary tables
- `remaining`: allocated - used
- `percent_used`: usage percentage
- `charges_by_type`: Breakdown by comp/dav/disk/archive
- `adjustments`: Manual charge adjustments

**Example API Response:**
```json
{
  "allocation_id": 12345,
  "allocated": 1000000.0,
  "used": 456789.12,
  "remaining": 543210.88,
  "percent_used": 45.68,
  "start_date": "2024-01-01T00:00:00",
  "end_date": "2024-12-31T23:59:59",
  "charges_by_type": {
    "comp": 345678.90,
    "dav": 111110.22,
    "disk": 0.0,
    "archive": 0.0
  },
  "adjustments": [],
  "resource": {
    "resource_id": 42,
    "name": "Derecho"
  }
}
```

For detailed schema documentation, see [CLAUDE.md](../../CLAUDE.md#marshmallow-sqlalchemy-schemas).

## Authentication & Authorization

### Roles

The system supports the following roles:

- **admin**: Full system access
- **facility_manager**: Can manage projects, allocations, and resources
- **project_lead**: Can view projects and allocations
- **user**: Read-only access to projects and allocations
- **analyst**: Read-only access to everything with export capabilities

### Permissions

Permissions are defined in `webapp/utils/rbac.py`:

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

### Database Admin Interface

The Flask-Admin database interface provides direct access to database models:
- Active user count
- Active project count
- Active resource count
- Upcoming expirations (next 30 days)
- Recently expired projects (last 90 days)

Access: `http://localhost:5050/database/`

### User Management

View, search, and manage SAM users.

Access: `http://localhost:5050/database/users/`

Permissions required: `VIEW_USERS` (view), `EDIT_USERS` (edit), `CREATE_USERS` (create)

### Project Management

View, search, and manage SAM projects.

Access: `http://localhost:5050/database/projects/`

Permissions required: `VIEW_PROJECTS` (view), `EDIT_PROJECTS` (edit)

### Allocation Management

View and manage resource allocations.

Access: `http://localhost:5050/database/allocations/`

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

Access: `http://localhost:5050/admin/` (Admin Dashboard)

## REST API

All API endpoints require authentication (session cookie) and appropriate RBAC permissions. Responses are in JSON format serialized using [Marshmallow schemas](#api-serialization-with-marshmallow).

### Authentication

Login to obtain a session cookie:

```bash
curl -c cookies.txt -X POST http://localhost:5050/auth/login \
  -d "username=your_username&password=your_password"

# Use the session cookie in subsequent requests
curl -b cookies.txt http://localhost:5050/api/v1/users/
```

### User Endpoints

**List Users**
```bash
GET /api/v1/users?page=1&per_page=50&search=smith&active=true
```
- **Query Parameters:**
  - `page` (int): Page number (default: 1)
  - `per_page` (int): Items per page (default: 50, max: 100)
  - `search` (str): Search term for username/name
  - `active` (bool): Filter by active status (true/false)
  - `locked` (bool): Filter by locked status (true/false)
- **Permission:** `VIEW_USERS`
- **Response:** `{ users: [...], page: 1, per_page: 50, total: 42 }`

**Get User Details**
```bash
GET /api/v1/users/johndoe
```
- **Permission:** `VIEW_USERS`
- **Response:** User object with institutions, organizations, roles, timestamps

**Get User's Projects**
```bash
GET /api/v1/users/johndoe/projects
```
- **Permission:** `VIEW_PROJECTS`
- **Response:** Lists projects where user is lead, admin, or member

### Project Endpoints

**List Projects**
```bash
GET /api/v1/projects?page=1&per_page=50&search=climate&active=true
```
- **Query Parameters:**
  - `page` (int): Page number (default: 1)
  - `per_page` (int): Items per page (default: 50, max: 100)
  - `search` (str): Search term for projcode/title
  - `active` (bool): Filter by active status
  - `facility` (str): Filter by facility name
- **Permission:** `VIEW_PROJECTS`
- **Response:** `{ projects: [...], page: 1, per_page: 50, total: 156 }`

**Get Project Details**
```bash
GET /api/v1/projects/ABC123
```
- **Permission:** `VIEW_PROJECTS`
- **Response:** Project object with abstract, lead, admin, timestamps

**Get Project Members**
```bash
GET /api/v1/projects/ABC123/members
```
- **Permission:** `VIEW_PROJECT_MEMBERS`
- **Response:** Project lead, admin, and all active members

**Get Project Allocations**
```bash
GET /api/v1/projects/ABC123/allocations?resource=Casper
```
- **Query Parameters:**
  - `resource` (str): Filter by resource name
- **Permission:** `VIEW_ALLOCATIONS`
- **Response:** All allocations for the project with resource details

**Get Expiring Projects**
```bash
# Default: 30 days, all facilities
GET /api/v1/projects/expiring

# Custom parameters
GET /api/v1/projects/expiring?days=90&facility_names=UNIV&resource=Casper

# Backwards compatible single facility
GET /api/v1/projects/expiring?days=60&facility=UNIV
```
- **Query Parameters:**
  - `days` (int): Days in future to check (default: 30)
  - `facility_names` (list): Filter by facility names (can specify multiple)
  - `facility` (str): Single facility filter (backwards compatible)
  - `resource` (str): Filter by resource name
- **Permission:** `VIEW_ALLOCATIONS`
- **Response:**
  ```json
  {
    "expiring_projects": [
      {
        "projcode": "ABC123",
        "title": "Project Title",
        "lead_username": "jdoe",
        "lead_name": "John Doe",
        "admin_username": "asmith",
        "active": true,
        "resource_name": "Casper",
        "days_remaining": 25,
        "allocation_end_date": "2025-12-06T23:59:59",
        "allocation_start_date": "2024-06-05T00:00:00"
      }
    ],
    "days": 30,
    "facility_names": ["UNIV"],
    "resource_name": "Casper",
    "total": 150
  }
  ```

**Get Recently Expired Projects**
```bash
# Default: 90-365 days ago, all facilities
GET /api/v1/projects/recently_expired

# Custom date range
GET /api/v1/projects/recently_expired?min_days=90&max_days=180

# With filters
GET /api/v1/projects/recently_expired?min_days=0&max_days=30&facility_names=UNIV&resource=Casper
```
- **Query Parameters:**
  - `min_days` (int): Minimum days since expiration (default: 90)
  - `max_days` (int): Maximum days since expiration (default: 365)
  - `facility_names` (list): Filter by facility names
  - `facility` (str): Single facility filter (backwards compatible)
  - `resource` (str): Filter by resource name
- **Permission:** `VIEW_ALLOCATIONS`
- **Response:** Similar to expiring, but with `days_since_expiration` instead of `days_remaining`

### Response Formats

All endpoints return JSON with consistent error handling:

**Success:**
```json
{
  "data": [...],
  "total": 42,
  ...additional metadata...
}
```

**Not Found:**
```json
{
  "error": "Project not found"
}
```
Status: 404

**Unauthorized:**
```json
{
  "error": "Unauthorized - authentication required"
}
```
Status: 401

**Forbidden:**
```json
{
  "error": "Forbidden - insufficient permissions"
}
```
Status: 403

## Development

### Adding a New Permission

1. Add to `Permission` enum in `webapp/utils/rbac.py`:
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

In `webapp/admin/__init__.py`:

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
        from webapp.utils.rbac import has_permission, Permission
        return has_permission(current_user, Permission.VIEW_SOMETHING)

    @property
    def can_edit(self):
        from webapp.utils.rbac import has_permission, Permission
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
gunicorn -w 4 -b 0.0.0.0:5000 'webapp.run:create_app()'

# With gevent workers (better for I/O)
gunicorn -w 4 -k gevent -b 0.0.0.0:5000 'webapp.run:create_app()'

# With config file
gunicorn -c gunicorn_config.py 'webapp.run:create_app()'
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
        alias /path/to/webapp/static;
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
# Login and save session cookie
curl -c cookies.txt -X POST http://localhost:5050/auth/login \
  -d "username=your_username&password=test"

# List users with pagination
curl -b cookies.txt http://localhost:5050/api/v1/users?page=1&per_page=20

# Get specific user details
curl -b cookies.txt http://localhost:5050/api/v1/users/johndoe

# List projects
curl -b cookies.txt http://localhost:5050/api/v1/projects?search=climate

# Get project details
curl -b cookies.txt http://localhost:5050/api/v1/projects/ABC123

# Get project members
curl -b cookies.txt http://localhost:5050/api/v1/projects/ABC123/members

# Get expiring projects (next 90 days for UNIV facility)
curl -b cookies.txt "http://localhost:5050/api/v1/projects/expiring?days=90&facility_names=UNIV"

# Get recently expired projects (90-180 days ago, filtered by resource)
curl -b cookies.txt "http://localhost:5050/api/v1/projects/recently_expired?min_days=90&max_days=180&resource=Casper"
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
