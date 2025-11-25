# SAM Web UI Design Documentation

## Overview

The SAM Web UI is a Flask-based web application for managing the Service Allocation Management (SAM) database. It provides:

- **Flask-Admin Interface**: CRUD operations for all SAM database tables
- **Authentication**: Pluggable authentication (currently stub for development)
- **Role-Based Access Control (RBAC)**: Fine-grained permissions based on user roles
- **Dashboard**: Statistics and monitoring views
- **API Endpoints**: RESTful API for programmatic access (future)

## Architecture

### Technology Stack

- **Flask**: Web framework
- **Flask-Admin**: Admin interface with Bootstrap 4 theme
- **Flask-Login**: Session management
- **Flask-SQLAlchemy**: Database ORM (wraps existing SAM models)
- **Bootstrap 4**: UI framework
- **MySQL**: Database backend (existing SAM database)

### Directory Structure

```
python/webui/
├── __init__.py                 # Package init
├── run.py                      # Application factory and dev server
├── extensions.py               # Flask extension instances
├── auth/                       # Authentication module
│   ├── __init__.py
│   ├── models.py               # AuthUser (Flask-Login wrapper)
│   ├── providers.py            # Auth providers (stub, LDAP, SAML)
│   └── blueprint.py            # Login/logout routes
├── admin/                      # Flask-Admin views
│   ├── __init__.py             # Admin initialization
│   ├── views.py                # Custom admin index view
│   ├── default_model_views.py  # Base SAMModelView class
│   ├── custom_model_views.py   # Customized model views
│   ├── expiration_views.py     # Expiration dashboard
│   └── add_default_models.py   # Auto-register all models
├── dashboards/                 # Dashboard blueprints
│   ├── user/                   # User dashboard
│   │   └── blueprint.py        # User dashboard routes
│   └── status/                 # Status dashboard (future)
├── utils/                      # Utilities
│   ├── __init__.py
│   └── rbac.py                 # RBAC permissions and decorators
└── templates/                  # Jinja2 templates
    ├── auth/
    │   ├── login.html          # Login page
    │   └── profile.html        # User profile
    └── admin/
        ├── expirations_dashboard.html
        └── index.html # Dashboard
```

## Authentication System

### Current Implementation: Stub Authentication

The stub authentication provider is designed for **development and RBAC testing only**.

**How it works:**
1. User enters username and any password
2. System looks up user in SAM `users` table
3. If user exists and is active (not locked), authentication succeeds
4. User is wrapped in `AuthUser` object for Flask-Login

**Code:**
```python
# webui/auth/providers.py
class StubAuthProvider(AuthProvider):
    def authenticate(self, username: str, password: str) -> Optional[User]:
        if not password:
            return None
        user = find_user_by_username(self.db_session, username)
        if user and user.active and not user.locked:
            return user
        return None
```

### Future Enterprise Authentication

The authentication system is designed to be pluggable. Future implementations:

**LDAP Authentication:**
```python
provider = get_auth_provider('ldap',
                             db_session=db.session,
                             ldap_url='ldap://ldap.example.org',
                             base_dn='ou=users,dc=example,dc=org')
```

**SAML SSO:**
```python
provider = get_auth_provider('saml',
                             db_session=db.session,
                             entity_id='https://sam.example.org',
                             sso_url='https://sso.example.org/login')
```

### Flask-Login Integration

The `AuthUser` class wraps SAM's `User` model:

```python
# webui/auth/models.py
class AuthUser(UserMixin):
    def __init__(self, sam_user: User):
        self.sam_user = sam_user

    def get_id(self):
        return str(self.sam_user.user_id)

    @property
    def roles(self):
        return {ra.role.name for ra in self.sam_user.role_assignments}
```

## Role-Based Access Control (RBAC)

### Permission System

Permissions are defined as an enum in `webui/utils/rbac.py`:

```python
class Permission(Enum):
    # User management
    VIEW_USERS = "view_users"
    EDIT_USERS = "edit_users"
    CREATE_USERS = "create_users"
    DELETE_USERS = "delete_users"

    # Project management
    VIEW_PROJECTS = "view_projects"
    EDIT_PROJECTS = "edit_projects"
    # ... etc
```

### Role Definitions

Roles are mapped to permissions in `ROLE_PERMISSIONS`:

```python
ROLE_PERMISSIONS = {
    "admin": [p for p in Permission],  # All permissions

    "facility_manager": [
        Permission.VIEW_USERS,
        Permission.VIEW_PROJECTS,
        Permission.EDIT_PROJECTS,
        # ...
    ],

    "project_lead": [
        Permission.VIEW_PROJECTS,
        Permission.VIEW_ALLOCATIONS,
        # ...
    ],

    "user": [
        Permission.VIEW_PROJECTS,
        Permission.VIEW_ALLOCATIONS,
    ],
}
```

### Protecting Flask-Admin Views

All views inherit from `SAMModelView` which requires authentication by default:

```python
class SAMModelView(ModelView):
    def is_accessible(self):
        return current_user.is_authenticated

    def inaccessible_callback(self, name, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.url))
        return redirect(url_for('admin.index'))
```

**Adding permission checks to views:**

```python
class UserAdmin(SAMModelView):
    def is_accessible(self):
        if not current_user.is_authenticated:
            return False
        from webui.utils.rbac import has_permission, Permission
        return has_permission(current_user, Permission.VIEW_USERS)

    @property
    def can_edit(self):
        from webui.utils.rbac import has_permission, Permission
        return has_permission(current_user, Permission.EDIT_USERS)

    @property
    def can_create(self):
        from webui.utils.rbac import has_permission, Permission
        return has_permission(current_user, Permission.CREATE_USERS)

    @property
    def can_delete(self):
        from webui.utils.rbac import has_permission, Permission
        return has_permission(current_user, Permission.DELETE_USERS)
```

### Protecting Custom Routes

Use decorators for custom Flask routes:

```python
from webui.utils.rbac import require_permission, require_role, Permission

@app.route('/admin/special')
@login_required
@require_permission(Permission.SYSTEM_ADMIN)
def special_admin_view():
    return render_template('special.html')

@app.route('/reports')
@login_required
@require_role('admin', 'facility_manager', 'analyst')
def reports():
    return render_template('reports.html')
```

### Using RBAC in Templates

RBAC utilities are available in all templates via context processor:

```jinja2
{% if has_permission(Permission.EDIT_USERS) %}
    <a href="{{ url_for('users.edit_view', id=user.id) }}">Edit User</a>
{% endif %}

{% if has_role('admin') %}
    <a href="{{ url_for('admin.settings') }}">System Settings</a>
{% endif %}

<!-- Show user's current permissions -->
<ul>
    {% for perm in user_permissions %}
        <li>{{ perm.value }}</li>
    {% endfor %}
</ul>
```

## Dashboard Design

The dashboard (`MyAdminIndexView`) shows role-specific content:

```python
@expose('/')
def index(self):
    # Get user stats for all authenticated users
    user_count = session.query(User).filter(User.active == True).count()

    # Role-specific content can be added:
    if has_permission(current_user, Permission.SYSTEM_ADMIN):
        # Show admin-specific widgets
        pass
    elif has_permission(current_user, Permission.VIEW_PROJECTS):
        # Show project lead widgets
        pass

    return self.render('admin/index.html', ...)
```

## API Endpoints (Future)

### Planned Structure

```
python/webui/
└── api/
    ├── __init__.py
    └── v1/
        ├── __init__.py
        ├── users.py        # /api/v1/users
        ├── projects.py     # /api/v1/projects
        └── allocations.py  # /api/v1/allocations
```

### Example API Blueprint

```python
# webui/api/v1/users.py
from flask import Blueprint, jsonify, request
from flask_login import login_required
from webui.utils.rbac import require_permission, Permission
from webui.extensions import db

bp = Blueprint('api_users', __name__)

@bp.route('/', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_USERS)
def list_users():
    """GET /api/v1/users - List users"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    from sam.queries import get_active_users
    users = get_active_users(db.session, limit=per_page)

    return jsonify({
        'users': [{'username': u.username, 'email': u.primary_email}
                  for u in users],
        'page': page,
        'per_page': per_page
    })

@bp.route('/<username>', methods=['GET'])
@login_required
@require_permission(Permission.VIEW_USERS)
def get_user(username):
    """GET /api/v1/users/<username>"""
    from sam.queries import find_user_by_username
    user = find_user_by_username(db.session, username)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    return jsonify({
        'username': user.username,
        'email': user.primary_email,
        'active': user.active,
        'locked': user.locked
    })
```

**Register in `run.py`:**
```python
from webui.api.v1 import users as api_users
app.register_blueprint(api_users.bp, url_prefix='/api/v1/users')
```

## Flask Extensions to Add

Recommended extensions for future enhancement:

1. **Flask-Caching** - Cache expensive queries
   ```python
   from flask_caching import Cache
   cache = Cache(config={'CACHE_TYPE': 'redis'})
   ```

2. **Flask-Limiter** - Rate limiting for API
   ```python
   from flask_limiter import Limiter
   limiter = Limiter(key_func=get_remote_address)
   ```

3. **Flask-CORS** - CORS for API if separate frontend
   ```python
   from flask_cors import CORS
   CORS(app, resources={r"/api/*": {"origins": "*"}})
   ```

4. **Marshmallow** - Object serialization for API
   ```python
   from marshmallow_sqlalchemy import SQLAlchemyAutoSchema

   class UserSchema(SQLAlchemyAutoSchema):
       class Meta:
           model = User
   ```

## Testing RBAC

### Setup Test Users with Roles

```sql
-- Create test roles
INSERT INTO role (name, description) VALUES
  ('admin', 'System Administrator'),
  ('facility_manager', 'Facility Manager'),
  ('project_lead', 'Project Lead'),
  ('user', 'Regular User');

-- Assign roles to test users
INSERT INTO role_user (role_id, user_id)
SELECT r.role_id, u.user_id
FROM role r, users u
WHERE r.name = 'admin' AND u.username = 'test_admin';

INSERT INTO role_user (role_id, user_id)
SELECT r.role_id, u.user_id
FROM role r, users u
WHERE r.name = 'facility_manager' AND u.username = 'test_manager';

INSERT INTO role_user (role_id, user_id)
SELECT r.role_id, u.user_id
FROM role r, users u
WHERE r.name = 'project_lead' AND u.username = 'test_lead';
```

### Test Login

1. Start the dev server: `python python/webui/run.py`
2. Navigate to `http://localhost:5050`
3. Login with any SAM username and any password
4. Check which admin views are accessible based on roles

## Deployment Considerations

### Production Checklist

- [ ] Change `SECRET_KEY` to random value (not in code)
- [ ] Switch `AUTH_PROVIDER` from 'stub' to 'ldap' or 'saml'
- [ ] Enable HTTPS only cookies (`SESSION_COOKIE_SECURE = True`)
- [ ] Use production WSGI server (Gunicorn, uWSGI)
- [ ] Configure reverse proxy (Nginx, Apache)
- [ ] Set up connection pooling appropriately
- [ ] Enable logging to file/syslog
- [ ] Add rate limiting for API endpoints
- [ ] Configure CORS if needed
- [ ] Set up monitoring (Sentry, New Relic, etc.)

### Production Server Example

**Gunicorn:**
```bash
gunicorn -w 4 -b 0.0.0.0:5000 'webui.run:create_app()'
```

**With configuration file:**
```python
# gunicorn_config.py
bind = "0.0.0.0:5000"
workers = 4
worker_class = "sync"
timeout = 120
accesslog = "/var/log/sam/access.log"
errorlog = "/var/log/sam/error.log"
```

```bash
gunicorn -c gunicorn_config.py 'webui.run:create_app()'
```

## Next Steps

1. **Test authentication and RBAC** with test users
2. **Add more custom views** to Flask-Admin as needed
3. **Implement API endpoints** for programmatic access
4. **Add data serialization** with Marshmallow for clean JSON responses
5. **Integrate enterprise auth** (LDAP/SAML) when ready
6. **Add comprehensive logging** for audit trails
7. **Implement caching** for expensive queries
8. **Add automated tests** for authentication and permissions

## Questions & Customization

### How do I add a new permission?

1. Add to `Permission` enum in `webui/utils/rbac.py`
2. Add to role mappings in `ROLE_PERMISSIONS`
3. Use in views with `@require_permission()` or `has_permission()`

### How do I create a custom dashboard for a role?

Modify `MyAdminIndexView.index()` in `webui/admin/views.py`:

```python
@expose('/')
def index(self):
    from webui.utils.rbac import has_role

    if has_role(current_user, 'project_lead'):
        # Show project lead dashboard
        my_projects = get_projects_by_lead(session, current_user.username)
        return self.render('dashboards/project_lead.html',
                          projects=my_projects)

    # Default dashboard
    return self.render('admin/index.html', ...)
```

### How do I add a new Flask-Admin model view?

```python
# In webui/admin/__init__.py
from sam.models import MyNewModel
from .custom_model_views import MyNewModelAdmin

admin.add_view(MyNewModelAdmin(MyNewModel, db.session,
                               name='My Models',
                               endpoint='my_models',
                               category='My Category'))
```

### How do I switch to LDAP authentication?

1. Install python-ldap: `pip install python-ldap`
2. Implement `LDAPAuthProvider.authenticate()` in `webui/auth/providers.py`
3. Update config in `run.py`: `app.config['AUTH_PROVIDER'] = 'ldap'`
4. Configure LDAP settings (URL, base DN, etc.)
