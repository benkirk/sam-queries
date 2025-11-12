# Flask Extensions Guide for SAM Web UI

This document provides detailed descriptions of Flask extensions that would enhance the SAM Web UI, with specific use cases and implementation examples tailored to your allocation management system.

---

## 1. Flask-Marshmallow: Clean JSON Serialization for API

### Overview

Flask-Marshmallow is a serialization library that transforms complex Python objects (like SQLAlchemy models) into JSON and validates incoming data. It acts as the bridge between your database models and your REST API, providing clean, consistent JSON responses while handling relationships, nested objects, and field validation automatically.

### Why You Need It

Currently, if you want to return a User object as JSON, you'd manually build dictionaries:

```python
# Manual approach (tedious and error-prone)
return jsonify({
    'user_id': user.user_id,
    'username': user.username,
    'email': user.primary_email,
    'active': user.active,
    # ... repeat for every field
})
```

With Flask-Marshmallow, you define the schema once and reuse it everywhere:

```python
# With Marshmallow (clean and maintainable)
user_schema = UserSchema()
return jsonify(user_schema.dump(user))
```

### SAM-Specific Use Cases

**Use Case 1: Project API with Nested Allocations**
When an API client requests a project, they probably want to see its allocations too. Marshmallow handles nested relationships elegantly:

```python
from marshmallow_sqlalchemy import SQLAlchemyAutoSchema
from marshmallow import fields

class AllocationSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = Allocation
        include_relationships = True
        load_instance = True

    resource_name = fields.Method("get_resource_name")

    def get_resource_name(self, obj):
        return obj.account.resource.resource_name if obj.account else None

class ProjectSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = Project
        include_relationships = True
        exclude = ('tree_left', 'tree_right')  # Hide internal fields

    # Automatically serialize nested allocations
    allocations = fields.Nested(AllocationSchema, many=True)
    lead_name = fields.Method("get_lead_name")

    def get_lead_name(self, obj):
        return obj.lead.full_name if obj.lead else None

# Usage in API endpoint
@bp.route('/projects/<projcode>')
def get_project(projcode):
    project = find_project_by_code(db.session, projcode)
    schema = ProjectSchema()
    return jsonify(schema.dump(project))
```

**Response:**
```json
{
  "projcode": "ABC123",
  "title": "Research Project Alpha",
  "lead_name": "Dr. Jane Smith",
  "active": true,
  "allocations": [
    {
      "allocation_id": 456,
      "amount": 100000.0,
      "start_date": "2024-01-01",
      "end_date": "2024-12-31",
      "resource_name": "HPC Cluster A"
    }
  ]
}
```

**Use Case 2: Filtering Sensitive Data by Role**
Different users should see different fields. Marshmallow makes this easy:

```python
class UserSchema(SQLAlchemyAutoSchema):
    class Meta:
        model = User
        exclude = ('password_hash',)  # Never expose passwords

    # Method to exclude fields based on current user's role
    def dump(self, obj, **kwargs):
        from flask_login import current_user
        from webui.utils.rbac import has_permission, Permission

        data = super().dump(obj, **kwargs)

        # Only admins can see locked status and charging_exempt
        if not has_permission(current_user, Permission.EDIT_USERS):
            data.pop('locked', None)
            data.pop('charging_exempt', None)

        return data
```

**Use Case 3: Bulk Export with Consistent Formatting**
When facility managers export project lists, Marshmallow ensures consistent formatting:

```python
projects_schema = ProjectSchema(many=True, exclude=['description', 'contracts'])
projects = get_active_projects(db.session)

# Returns list of consistently formatted dictionaries
data = projects_schema.dump(projects)

# Can be easily converted to CSV, Excel, or JSON
```

### Benefits for SAM Web UI

1. **Consistency**: All API responses have the same structure
2. **Type Safety**: Automatic type conversion (dates, decimals, booleans)
3. **Performance**: Can selectively include/exclude fields to reduce response size
4. **Validation**: Validates incoming POST/PUT data automatically
5. **Documentation**: Schema definitions serve as API documentation
6. **Maintainability**: Change schema once, affects all endpoints

### Installation & Setup
```bash
pip install marshmallow-sqlalchemy flask-marshmallow
```

---

## 2. Flask-Limiter: Rate Limiting for API Endpoints

### Overview

Flask-Limiter protects your API from abuse by limiting how many requests a user can make within a time window. It prevents both malicious attacks (DoS) and accidental overuse (buggy scripts hammering your API), ensuring fair resource allocation and system stability.

### Why You Need It

Without rate limiting, a single user with a buggy script could:
- Query `/api/v1/users` 1000 times per second
- Overwhelm your database with expensive queries
- Slow down or crash the application for everyone else
- Exhaust database connection pools

Rate limiting ensures fair usage and system stability.

### SAM-Specific Use Cases

**Use Case 1: Protecting Expensive Queries**
Some endpoints are computationally expensive. Limit them more strictly:

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="redis://localhost:6379"  # or "memory://" for simple setups
)

# Expensive query: all users with their projects and allocations
@bp.route('/api/v1/reports/full-user-data')
@limiter.limit("10 per hour")  # Very strict limit
@require_permission(Permission.VIEW_REPORTS)
def get_full_user_data():
    # Complex joins across multiple tables
    ...

# Normal query: list users
@bp.route('/api/v1/users')
@limiter.limit("100 per minute")  # More generous
@require_permission(Permission.VIEW_USERS)
def list_users():
    ...
```

**Use Case 2: Role-Based Rate Limits**
Different roles get different rate limits:

```python
def get_limit_for_user():
    """Dynamic rate limiting based on user role"""
    if not current_user.is_authenticated:
        return "10 per minute"  # Strict for unauthenticated

    if current_user.has_role('admin'):
        return "1000 per minute"  # Very generous for admins

    if current_user.has_role('analyst'):
        return "500 per minute"  # Generous for analysts doing bulk queries

    return "100 per minute"  # Standard for regular users

@bp.route('/api/v1/projects')
@limiter.limit(get_limit_for_user)
def list_projects():
    ...
```

### Benefits for SAM Web UI

1. **System Protection**: Prevents database overload from buggy scripts
2. **Fair Usage**: Ensures all users get responsive access
3. **DoS Prevention**: Blocks simple denial-of-service attacks
4. **Resource Management**: Protects expensive queries (usage reports, exports)

### Installation & Setup
```bash
pip install Flask-Limiter redis  # Redis for production
```

---

## 3. Flask-Caching: Cache Expensive Queries

### Overview

Flask-Caching stores the results of expensive operations in fast storage (memory, Redis, Memcached) so repeated requests return instantly without hitting the database. For queries that don't change often (statistics, resource lists, project summaries), caching can reduce response time from seconds to milliseconds.

### SAM-Specific Use Cases

**Use Case 1: Dashboard Statistics (Infrequently Changing)**
Statistics don't need to be real-time:

```python
from flask_caching import Cache

cache = Cache(config={'CACHE_TYPE': 'redis', 'CACHE_REDIS_URL': 'redis://localhost:6379'})

@bp.route('/admin/dashboard')
@cache.cached(timeout=300)  # Cache for 5 minutes
def dashboard():
    stats = {
        'user_count': db.session.query(User).filter(User.active == True).count(),
        'project_count': db.session.query(Project).filter(Project.active == True).count(),
        'resource_count': db.session.query(Resource).filter(Resource.is_commissioned == True).count(),
    }
    return render_template('dashboard.html', stats=stats)
```

**Use Case 2: Resource Lists (Rarely Change)**
Available resources change infrequently (when new hardware is added):

```python
@cache.cached(timeout=3600, key_prefix='available_resources')  # 1 hour
def get_available_resources():
    return db.session.query(Resource).filter(
        Resource.is_commissioned == True
    ).all()
```

### Benefits for SAM Web UI

1. **Performance**: Dashboard loads in <10ms instead of 200ms
2. **Database Load**: Reduces queries by 80-90% for frequently accessed data
3. **Cost Savings**: Fewer database queries = lower infrastructure costs
4. **User Experience**: Faster page loads and API responses
5. **Scalability**: Can handle 10x more users without database upgrades

### Caching Strategy for SAM

| Data Type | Cache Duration | Rationale |
|-----------|---------------|-----------|
| System statistics | 5-10 minutes | Changes slowly, not critical to be real-time |
| Resource lists | 1 hour | Hardware changes infrequently |
| Project lists | 10 minutes | Projects created/updated occasionally |
| User data | 5 minutes | Profile changes are rare |
| Usage reports | 24 hours | Historical data doesn't change |
| Expiration lists | 1 hour | Expirations are not time-critical |

### Installation & Setup
```bash
pip install Flask-Caching redis  # Redis for production
```

---

## 4. Flask-CORS: Cross-Origin Resource Sharing

### Overview

Flask-CORS enables your Flask API to accept requests from web applications hosted on different domains. This is essential if you plan to build a separate JavaScript frontend (React, Vue, Angular) that runs on a different port or domain than your Flask backend.

### When You Need It

**Scenario 1: Separate React/Vue Frontend**
```
┌─────────────────┐         ┌─────────────────┐
│  React Frontend │  AJAX   │  Flask Backend  │
│  localhost:3000 │────────▶│  localhost:5050 │
│                 │◀────────│  (API only)     │
└─────────────────┘  CORS   └─────────────────┘
```

**Scenario 2: Mobile App Backend**
A mobile app (iOS/Android) calling your SAM API.

### Integration Example

```python
from flask_cors import CORS

# Configure CORS for API endpoints only
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "http://localhost:3000",           # React dev server
            "https://sam-ui.university.edu",   # Production frontend
        ],
        "methods": ["GET", "POST", "PUT", "DELETE"],
        "supports_credentials": True,  # Allow cookies for authentication
    }
})
```

### When NOT to Use CORS

- **Monolithic app** (current setup): Flask-Admin serves HTML and API together
- **Server-side rendering only**: Templates are rendered by Flask
- Not needed until you build a separate frontend

### Installation & Setup
```bash
pip install Flask-CORS
```

---

## 5. Flask-Mail: Email Notifications

### Overview

Flask-Mail integrates email capabilities into Flask applications, enabling automated notifications for important events. For a system managing resource allocations and expirations, timely email notifications are critical for preventing service disruptions.

### SAM-Specific Use Cases

**Use Case 1: Allocation Expiration Warnings** (Most Critical)
Prevent unexpected service loss:

```python
from flask_mail import Mail, Message
from datetime import datetime, timedelta

mail = Mail()

def send_expiration_warning(project, allocation, days_remaining):
    """Send warning email when allocation is expiring soon"""

    lead_email = project.lead.primary_email
    admin_email = project.admin.primary_email if project.admin else None

    msg = Message(
        subject=f"⚠️ Allocation Expiring Soon: {project.projcode}",
        recipients=[lead_email],
        cc=[admin_email] if admin_email else [],
        sender=("SAM Notifications", "sam-noreply@university.edu")
    )

    msg.html = f"""
<html>
<body style="font-family: Arial, sans-serif;">
    <h2>⚠️ Allocation Expiring Soon</h2>

    <p>Dear {project.lead.full_name},</p>

    <p>Your allocation for project <strong>"{project.title}"</strong> ({project.projcode})
    will expire in <strong>{days_remaining} days</strong>.</p>

    <table style="border-collapse: collapse; margin: 20px 0;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Resource:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{allocation.account.resource.resource_name}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><strong>Expiration Date:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{allocation.end_date.strftime('%Y-%m-%d')}</td>
        </tr>
    </table>

    <p>
        <a href="https://sam.university.edu/projects/{project.projcode}/renew"
           style="background-color: #4CAF50; color: white; padding: 10px 20px;
                  text-decoration: none; border-radius: 5px;">
            Request Renewal
        </a>
    </p>
</body>
</html>
"""

    mail.send(msg)

# Scheduled task (runs daily via cron or Celery)
def send_daily_expiration_warnings():
    """Check for allocations expiring soon and send warnings"""

    # 30 days, 14 days, 7 days, and 3 days warnings
    warning_thresholds = [30, 14, 7, 3]

    today = datetime.now().date()

    for days in warning_thresholds:
        target_date = today + timedelta(days=days)

        # Find allocations expiring on target date
        expiring_allocations = db.session.query(Allocation).filter(
            Allocation.end_date == target_date,
            Allocation.deleted == False
        ).all()

        for alloc in expiring_allocations:
            project = alloc.account.project
            if project.active and project.lead.primary_email:
                send_expiration_warning(project, alloc, days)
```

**Use Case 2: Weekly Digest for Administrators**
Facility managers get weekly summaries:

```python
def send_weekly_admin_digest(admin_user):
    """Send weekly summary to facility managers"""

    # Gather statistics
    upcoming_expirations = get_projects_expiring_soon(db.session, days=30)
    recently_expired = get_projects_with_expired_allocations(db.session, max_days_expired=7)

    msg = Message(
        subject=f"SAM Weekly Digest - {datetime.now().strftime('%B %d, %Y')}",
        recipients=[admin_user.primary_email],
        sender=("SAM Notifications", "sam-noreply@university.edu")
    )

    msg.html = f"""
<html>
<body>
    <h2>SAM Weekly Digest</h2>
    <h3>Week of {datetime.now().strftime('%B %d, %Y')}</h3>

    <div style="margin: 20px 0;">
        <h4>🔔 Upcoming Expirations (Next 30 Days)</h4>
        <p>{len(upcoming_expirations)} projects will expire soon.</p>
    </div>

    <div style="margin: 20px 0;">
        <h4>⏰ Recently Expired</h4>
        <p>{len(recently_expired)} projects expired in the last 7 days.</p>
    </div>
</body>
</html>
"""

    mail.send(msg)
```

### Benefits for SAM Web UI

1. **Proactive Management**: Users get warnings before problems occur
2. **Reduced Support Load**: Automated reminders reduce manual communication
3. **Better UX**: Users appreciate timely notifications
4. **Compliance**: Email trail for allocation expiration warnings
5. **Operational Awareness**: Admins stay informed of system state

### Email Notification Strategy

| Event | Recipients | Timing | Priority |
|-------|-----------|--------|----------|
| Allocation expiring | Project lead, admin | 30, 14, 7, 3 days before | High |
| Allocation expired | Project lead, admin, facility manager | Day after expiration | High |
| Weekly admin digest | Facility managers | Monday 9 AM | Medium |
| System alerts | System admins | Immediately | Critical |

### Integration Steps

**Step 1: Configure Flask-Mail**
```python
# webui/run.py
from flask_mail import Mail

mail = Mail()

def create_app():
    app = Flask(__name__)

    # Email configuration
    app.config['MAIL_SERVER'] = 'smtp.university.edu'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = ('SAM Notifications', 'sam-noreply@university.edu')

    mail.init_app(app)

    return app
```

**Step 2: Schedule Notifications**
```python
# Using APScheduler or cron
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

# Daily at 8 AM: Send expiration warnings
scheduler.add_job(
    func=send_daily_expiration_warnings,
    trigger='cron',
    hour=8,
    minute=0
)

scheduler.start()
```

### Installation & Setup
```bash
pip install Flask-Mail
```

---

## Summary: Which Extensions Should You Add?

### Immediate Priority (Next 1-2 Months)

1. **Flask-Mail** - Most critical for SAM's mission
   - Allocation expirations are time-sensitive
   - Prevents service disruptions
   - Reduces support burden

2. **Flask-Marshmallow** - Essential for clean API
   - Makes API development much faster
   - Ensures consistency across endpoints
   - Simplifies maintenance

### Medium Priority (3-6 Months)

3. **Flask-Caching** - Improves performance significantly
   - Dashboard loads become instant
   - Reduces database load
   - Better user experience

4. **Flask-Limiter** - Important for API stability
   - Prevents abuse and accidents
   - Protects expensive queries
   - Essential once API is public

### Future Consideration

5. **Flask-CORS** - Only if building separate frontend
   - Not needed for current monolithic design
   - Required for React/Vue SPA
   - Useful for mobile apps

### Recommended Implementation Order

**Phase 1: Core Functionality** (Immediate)
- Flask-Marshmallow (API serialization)
- Flask-Mail (expiration warnings)

**Phase 2: Performance & Stability** (3-6 months)
- Flask-Caching (dashboard and frequent queries)
- Flask-Limiter (protect API endpoints)

**Phase 3: Architecture Evolution** (As needed)
- Flask-CORS (if moving to SPA frontend)

Each extension is production-ready, well-documented, and commonly used in Flask applications. They integrate seamlessly with your existing authentication and RBAC system.
