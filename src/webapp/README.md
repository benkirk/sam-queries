# SAM Web UI

Flask-based web administration interface for the Systems Accounting Manager (SAM) database.

## Features

- **Database browser** (`/database`): read-only rows from every database the
  webapp connects to, for low-level diagnosis (`ADMIN_DATABASE`; see below)
- **Account registration**: anonymous `/register` form with email verification
  (gated by the `ACCOUNT_REGISTRATION_ENABLED` kill-switch, off in production;
  `docs/plans/implemented/ACCOUNT_REGISTRATION.md`)
- **Authentication**: Pluggable authentication system (stub, LDAP, OIDC)
- **Role-Based Access Control (RBAC)**: Permissions from POSIX group bundles + per-user overrides
- **Dashboard**: Statistics and monitoring for projects, users, and allocations
- **Expiration Monitoring**: Track upcoming and expired project allocations
- **REST API**: Comprehensive JSON API for users, projects, allocations, and expirations
- **Bootstrap 5 UI**: Modern, responsive interface (vendored assets, htmx-driven fragments)

## Quick Start

### 1. Install Dependencies

Dependencies come from the repo-level conda environment (there is no
webapp-local `requirements.txt`):

```bash
source etc/config_env.sh   # from the repo root — activates conda, loads .env
```

### 2. Configure Database

The web UI uses the same database configuration as the main SAM package. Ensure your `.env` file is configured:

```bash
# .env
SAM_DB_SERVER=your-db-server
SAM_DB_USERNAME=your-username
SAM_DB_PASSWORD=your-password
```

### 3. Configure Permissions

The webapp resolves a user's permissions from two sources, unioned:

1. **POSIX group membership** — `get_user_group_access()` reads
   `adhoc_system_account_entry`. Groups that have a bundle in
   `GROUP_PERMISSIONS` (currently `csg`, `nusd`, `hsg`) confer that
   bundle to anyone in the group.
2. **`USER_PERMISSION_OVERRIDES`** in `webapp/utils/rbac.py` — a
   per-username dict for one-off grants on top of group bundles.

To grant yourself elevated permissions in dev, add your username to
`USER_PERMISSION_OVERRIDES` (e.g. `'your_username': set(Permission)`
for full access). No DB writes, no fake role tables — same code path
as production.

### 4. Run Development Server

```bash
# From the repo root — preferred (rebuilds + live-reloads on change)
docker compose up webdev --watch
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
├── run.py                      # Application factory & dev server
├── config.py                   # Config class hierarchy (Dev/Prod/Testing)
├── extensions.py               # Flask extension instances
├── auth/                       # Authentication (AuthProvider ABC: stub, LDAP, OIDC)
├── api/                        # REST API v1 + access-control decorators
│   ├── access_control.py       # @require_project_access etc.
│   └── v1/                     # users, projects, charges, allocations, status,
│                               #   health, admin + legacy-compat endpoints
├── audit/                      # Implicit model-audit logging (SQLAlchemy events)
├── caching/                    # Caching facade + adapters (Redis/Flask/chart)
├── dashboards/                 # htmx dashboard blueprints
│   ├── admin/                  # Admin dashboard (projects, orgs, resources, …)
│   ├── allocations/            # Allocations dashboard (+ /projects/export xlsx via sam.export)
│   ├── status/                 # System-status dashboard
│   ├── user/                   # User dashboard
│   ├── charts.py               # Matplotlib SVG chart generators
│   └── project_members.py      # Project-member management
├── disk_scans/                 # Filesystem-scan views (hpc-usage-queries plugin)
├── jobs/                       # Job-history views (hpc-usage-queries plugin):
│                               #   5-tab card (project/machine/user modes),
│                               #   explorer + facet chips, bar→bucket drills,
│                               #   TTL cache (routes/service/cache)
├── limiter/                    # Rate-limiting facade (mirrors caching/)
├── utils/                      # rbac, htmx helpers, nav registry, csp, …
├── static/                     # Vendored Bootstrap 5 / htmx / FontAwesome + app JS/CSS
└── templates/                  # Jinja2 templates (dashboards/, admin/, auth/, …)
```

Marshmallow schemas live outside the webapp in `src/sam/schemas/` (API
serialization) and `src/sam/schemas/forms/` (htmx/API form validation).

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

For detailed schema documentation, see [CLAUDE.md](../../CLAUDE.md#serialization-schemas-samschemas).

## Authentication & Authorization

### Group bundles (not DB roles)

There is no role table behind webapp authorization. A user's permission set
is the union of:

1. The `GROUP_PERMISSIONS` bundles for POSIX groups they belong to
   (e.g. `csg`, `nusd`, `ssg`) — see `webapp/utils/rbac.py`.
2. Any per-user grant in `USER_PERMISSION_OVERRIDES` (same file), including
   facility-scoped grants via `USER_FACILITY_PERMISSIONS`.

### Permissions

Permissions are defined in the `Permission` enum in `webapp/utils/rbac.py`
(user/project/allocation/resource management, reports, system admin, …).
Route-level enforcement uses `@require_permission(...)` /
`@require_permission_any_facility(...)` from the same module, plus the
project-scoped decorators in `webapp/api/access_control.py`.

### Switching Authentication Providers

Provider selection is environment-driven (`AUTH_PROVIDER` = `stub`, `ldap`,
or `oidc`; see `webapp/auth/providers.py` and `config.py`). Production runs
OIDC; development defaults to the stub provider with Quick Login buttons for
RBAC testing.

## Main Features

### Database browser (`/database`)

Read-only rows from every database the webapp holds an engine for: SAM,
`system_status`, job_history per machine, fs_scans per collection. Tables,
columns and foreign keys come from reflection, so nothing is registered per
model. Admin → Database, `http://localhost:5050/database/`.

- **Table view**: structured filters (`=`, `<`, `like`, `in`, `is null`, ...),
  sort, column picker, row estimates; an exact count only on click. Every view
  is a plain URL, so it can be bookmarked or pasted into a ticket.
- **Row view**: every column, foreign-key links to the referenced row,
  "referenced by" links to each child table filtered on this row, and
  "Open in SAM" for users, projects and groups.
- **Schema tab**: types, indexes, foreign keys both ways, ORM drift.
- **Safety**: each query runs in a READ ONLY transaction with a statement
  timeout (`DB_BROWSER_STATEMENT_TIMEOUT_MS`, default 5000); secret columns are
  redacted (never selected, filtered or sorted).
- **Access**: `ADMIN_DATABASE` (csg bundle); `DB_BROWSER_ENABLED=0` unmounts it.
  Design record: `docs/plans/DB_BROWSER.md`.

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

### Notifications

Email delivery, shared with the CLI via `sam.notify` (design and relay
measurements: `docs/plans/implemented/NOTIFICATION_FRAMEWORK.md`).

- **XRAS activation handoff** — the pending-activation card's Notify button
  previews the message and its recipients, then sends. Two steps, because the
  send is irreversible. Nothing delivered → the manual-fallback dialog and
  **no** activation event, so the card cannot claim a handoff nobody received.
- **Delivery log** — every attempt, for every channel:
  `Admin → Configuration → Notifications` (counts, `VIEW_SYSTEM_CONFIG`) with
  `Details »` → `/admin/htmx/notifications` (rows, `SYSTEM_ADMIN`, because
  every row names a real person's email).

⚠️ **Fail-closed.** `NOTIFY_ENABLED` defaults to `0`; without it every attempt
is recorded `suppressed` and nothing is delivered. Set
`NOTIFY_REDIRECT_TO=you@example.edu` to exercise the real send path safely —
messages are re-addressed, recorded `redirected` rather than `sent`, and both
the card and the preview say so loudly.

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

### Import Policy

Imports belong at module top by default. A function-local import is the
exception and must carry a one-line reason comment — an import cycle, the
SAM ORM init-chain load order (see `webapp/api/access_control.py`), or an
optional dependency. Hoist locals opportunistically only in files you are
already modifying, and verify with pytest plus an app boot.

### Adding a New Permission

1. Add to `Permission` enum in `webapp/utils/rbac.py`:
   ```python
   VIEW_SOMETHING = "view_something"
   ```

2. Add to the group-bundle mappings in `GROUP_PERMISSIONS`:
   ```python
   "csg": [Permission.VIEW_SOMETHING, ...],
   ```

3. Use in views:
   ```python
   @require_permission(Permission.VIEW_SOMETHING)
   def view_something():
       ...
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
1. The user belongs to a POSIX group with a `GROUP_PERMISSIONS` bundle
2. Or has a per-user grant in `USER_PERMISSION_OVERRIDES` (`webapp/utils/rbac.py`)

### Database connection errors

Check:
1. `.env` file has correct database credentials
2. Database server is accessible
3. `SAM_DB_*` environment variables are set

## Further Documentation

- **../../CLAUDE.md**: Project conventions (form validation, route protection,
  display formatting, testing)
- **../cli/README.md**: CLI architecture (shared conventions with the webapp)
- **Flask-Login docs**: https://flask-login.readthedocs.io/

## Support

For questions or issues:
1. Check `../../CLAUDE.md` and the code comments
2. Contact the SAM development team

## License

Copyright (c) 2025 SAM Project
