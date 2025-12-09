# Implementation Plan: Expirations Panel for User Dashboard Admin Tab

## Executive Summary

Add an expirations monitoring panel to the user dashboard's admin tab that displays ALL projects (not filtered to user's projects) using the existing project card UI pattern. This provides admins a quick way to monitor expiring/expired projects without navigating to the Flask-Admin interface.

---

## Architecture Decisions

### 1. **Route Organization: New Routes in User Dashboard Blueprint**

**Decision:** Create new routes in `/user/` blueprint rather than reusing Flask-Admin routes.

**Rationale:**
- Flask-Admin routes (`/admin/expirations/`) are designed for the separate admin interface with different layout/styling
- User dashboard uses Bootstrap 4 + custom fragment pattern (lazy loading, collapse sections)
- Flask-Admin uses its own templating system (`admin/master.html` base)
- Keeping routes separate maintains clean separation of concerns
- Allows independent evolution of both interfaces

**Implementation:**
- Add routes to `/src/webapp/dashboards/user/blueprint.py`
- Routes: `/user/admin/expirations`, `/user/admin/expirations/export`
- Reuse query functions from `sam.queries.expirations` (DRY principle)

---

### 2. **Template Structure: Separate Admin Template File**

**Decision:** Create new template file `admin_section.html` that gets included in main dashboard.

**Current Structure:**
```
dashboard.html (main file with tabs)
├── Tab: My Accounts (inline in dashboard.html)
├── Tab: User Information (inline in dashboard.html)
└── Tab: Admin (inline in dashboard.html, ~80 lines)
    ├── Impersonation form
    ├── Project search form
    └── Project card container
```

**New Structure:**
```
dashboard.html (main file with tabs)
├── Tab: My Accounts (inline)
├── Tab: User Information (inline)
└── Tab: Admin ({% include 'dashboards/user/admin_section.html' %})
    ├── Impersonation form
    ├── Project search form
    ├── Project card container
    └── NEW: Expirations section (collapsible card)
```

**Template File:** `/src/webapp/templates/dashboards/user/admin_section.html`

**Rationale:**
- Admin tab getting complex (will be ~300+ lines with expirations)
- Easier to maintain separate file
- Follows existing pattern (fragments directory for lazy-loaded content)
- Keeps main dashboard.html focused on structure

---

### 3. **Data Flow: Transform Query Results to Project Data**

**Challenge:** Query functions return tuples `(Project, Allocation, resource_name, days)`, but project cards need `project_data` dict from `get_project_dashboard_data()`.

**Solution:** Create transformation helper function in blueprint:

```python
def _build_expiration_project_data(expiring_results: List[Tuple]) -> List[Dict]:
    """
    Transform expiration query results into project_data format.
    
    Args:
        expiring_results: List of (Project, Allocation, resource_name, days) tuples
    
    Returns:
        List of dicts with structure:
        {
            'project': Project,
            'resources': [...],  # From get_detailed_allocation_usage()
            'has_children': bool,
            'expiration_info': {
                'resource_name': str,
                'end_date': datetime,
                'days_remaining': int,  # Can be negative for expired
                'allocation_id': int
            }
        }
    """
    # Group by project (one project may have multiple expiring allocations)
    projects_map = {}
    
    for project, allocation, resource_name, days in expiring_results:
        if project.projcode not in projects_map:
            # Call get_project_dashboard_data once per project
            project_data = get_project_dashboard_data(db.session, project.projcode)
            if project_data:
                projects_map[project.projcode] = project_data
                # Add expiration info container
                projects_map[project.projcode]['expirations'] = []
        
        # Add this allocation's expiration info
        projects_map[project.projcode]['expirations'].append({
            'resource_name': resource_name,
            'end_date': allocation.end_date,
            'days': days,
            'allocation_id': allocation.allocation_id
        })
    
    return list(projects_map.values())
```

**Performance Considerations:**
- `get_project_dashboard_data()` calls `project.get_detailed_allocation_usage()` which queries summary tables
- For 50 expiring projects, this is ~50 queries + summary aggregations
- **Acceptable:** Admin feature, not frequently accessed, results worth the cost
- **Future optimization:** Batch loading, caching if needed

**Alternative Considered (Rejected):**
- Building minimal project_data dicts manually without calling `get_project_dashboard_data()`
- **Rejected:** Would duplicate logic, miss future enhancements, hard to maintain

---

### 4. **UI/UX: Collapsible Grid Layout with Expiration Badges**

**Display Pattern:**

```html
<!-- Expirations Section in admin_section.html -->
<div class="card mb-3 mt-3">
    <div class="card-header" data-toggle="collapse" data-target="#expirations-section">
        <h5>
            <i class="fas fa-calendar-exclamation"></i> Project Expirations
            <span class="badge badge-info">15 upcoming</span>
        </h5>
    </div>
    <div id="expirations-section" class="collapse">
        <div class="card-body">
            <!-- Sub-tabs for upcoming/expired/abandoned -->
            <ul class="nav nav-pills mb-3">
                <li class="nav-item">
                    <a class="nav-link active" data-toggle="tab" href="#exp-upcoming">
                        Upcoming (15)
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link" data-toggle="tab" href="#exp-expired">
                        Expired (8)
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link" data-toggle="tab" href="#exp-abandoned">
                        Abandoned Users (3)
                    </a>
                </li>
            </ul>
            
            <!-- Filters (inline form) -->
            <form id="expirations-filters" class="form-inline mb-3">
                <!-- Facility checkboxes -->
                <!-- Resource filter -->
                <!-- Time range (for upcoming) -->
                <button type="button" id="apply-filters-btn">Apply</button>
                <button type="button" id="export-csv-btn">Export CSV</button>
            </form>
            
            <!-- Tab content (lazy loaded) -->
            <div class="tab-content">
                <div class="tab-pane active" id="exp-upcoming">
                    <div id="upcoming-container" class="expirations-container">
                        <!-- Project cards loaded here -->
                    </div>
                </div>
                <div class="tab-pane" id="exp-expired">
                    <div id="expired-container" class="expirations-container">
                        <!-- Project cards loaded here -->
                    </div>
                </div>
                <div class="tab-pane" id="exp-abandoned">
                    <div id="abandoned-container">
                        <!-- User table (keep table format) -->
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
```

**Project Card Enhancement:**

Add expiration badge to card header when in expirations view:

```html
<!-- Modified project_card.html macro - add optional expiration_info parameter -->
{% macro render_project_card(project_data, loop_index, user, usage_warning_threshold, usage_critical_threshold, is_expanded=false, expiration_info=none) %}
    <div class="card project-card mb-3">
        <div class="card-header" ...>
            <div class="d-flex justify-content-between align-items-center">
                <div>
                    <h5 class="mb-0">
                        <i class="fas fa-folder-open text-primary"></i>
                        <strong>{{ project.projcode }}</strong> - {{ project.title }}
                        
                        <!-- NEW: Expiration info badge -->
                        {% if expiration_info %}
                            {% for exp in expiration_info %}
                                {% if exp.days <= 7 %}
                                    <span class="badge badge-danger ml-2">
                                        <i class="fas fa-exclamation-circle"></i>
                                        {{ exp.resource_name }} expires in {{ exp.days }} days
                                    </span>
                                {% elif exp.days <= 30 %}
                                    <span class="badge badge-warning ml-2">
                                        <i class="fas fa-exclamation-triangle"></i>
                                        {{ exp.resource_name }} expires in {{ exp.days }} days
                                    </span>
                                {% elif exp.days < 0 %}
                                    <span class="badge badge-secondary ml-2">
                                        <i class="fas fa-times-circle"></i>
                                        {{ exp.resource_name }} expired {{ exp.days|abs }} days ago
                                    </span>
                                {% else %}
                                    <span class="badge badge-info ml-2">
                                        {{ exp.resource_name }} expires in {{ exp.days }} days
                                    </span>
                                {% endif %}
                            {% endfor %}
                        {% endif %}
                    </h5>
                </div>
                ...
            </div>
        </div>
        ...
    </div>
{% endmacro %}
```

**Collapsed by Default:**
- Cards start collapsed to avoid overwhelming page
- User can expand individual cards or expand all with button
- Matches existing dashboard behavior

**Grid Layout:**
- Use Bootstrap cards in column layout (no table)
- Allows rich display of project info
- Consistent with rest of user dashboard

---

### 5. **Abandoned Users: Keep Table Format**

**Decision:** Display abandoned users in table (not cards).

**Rationale:**
- Users aren't projects - card format doesn't fit semantic model
- Table is more compact for user lists
- Admin dashboard uses table successfully
- Allows quick scanning of username, email, projects

**Display:**
```html
<table class="table table-striped table-hover">
    <thead>
        <tr>
            <th>Username</th>
            <th>Name</th>
            <th>Email</th>
            <th>Expired Projects</th>
            <th>Actions</th>
        </tr>
    </thead>
    <tbody>
        {% for user_info in abandoned_users %}
        <tr>
            <td><strong>{{ user_info.username }}</strong></td>
            <td>{{ user_info.display_name }}</td>
            <td><a href="mailto:{{ user_info.email }}">{{ user_info.email }}</a></td>
            <td>{{ user_info.projects }}</td>
            <td>
                <button onclick="impersonateUser('{{ user_info.username }}')" 
                        class="btn btn-sm btn-outline-primary">
                    <i class="fas fa-user-secret"></i> Impersonate
                </button>
            </td>
        </tr>
        {% endfor %}
    </tbody>
</table>
```

**Enhancement:** Add "Impersonate" button to quickly switch to user's view.

---

## File Structure

### New Files

1. **`/src/webapp/templates/dashboards/user/admin_section.html`**
   - Extracted admin tab content from dashboard.html
   - Includes existing impersonation + project search sections
   - Adds new expirations section

2. **`/src/webapp/static/js/expirations.js`**
   - Handles filter changes
   - Triggers AJAX loads of expiration data
   - CSV export functionality
   - Tab switching between upcoming/expired/abandoned

### Modified Files

1. **`/src/webapp/dashboards/user/blueprint.py`**
   - Add routes: `expirations_fragment()`, `expirations_export()`
   - Add helper: `_build_expiration_project_data()`
   - Add constants for time ranges (reuse from admin view)

2. **`/src/webapp/templates/dashboards/user/dashboard.html`**
   - Replace inline admin tab content with: `{% include 'dashboards/user/admin_section.html' %}`
   - Add new JavaScript include: `<script src=".../expirations.js"></script>`

3. **`/src/webapp/templates/dashboards/user/partials/project_card.html`**
   - Add optional `expiration_info` parameter to macro
   - Add expiration badges in card header

---

## Implementation Steps

### Phase 1: Template Refactoring (No New Features)

**Goal:** Extract admin section without changing functionality.

1. Create `/src/webapp/templates/dashboards/user/admin_section.html`
   - Copy admin tab content from dashboard.html (lines 105-174)
   - Maintain exact same structure/IDs/classes

2. Modify `/src/webapp/templates/dashboards/user/dashboard.html`
   - Replace admin tab content with: `{% include 'dashboards/user/admin_section.html' %}`
   - Verify no regressions

3. Test existing admin tab functionality:
   - Impersonation works
   - Project search works
   - Project cards display correctly

### Phase 2: Blueprint Routes

**Goal:** Add backend endpoints for expirations data.

4. Add routes to `/src/webapp/dashboards/user/blueprint.py`:

```python
@bp.route('/admin/expirations')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def expirations_fragment():
    """
    AJAX endpoint for loading expirations data.
    
    Query parameters:
        view: 'upcoming' | 'expired' | 'abandoned'
        facilities: List of facility names (multi-select)
        resource: Optional resource name
        time_range: '7days' | '31days' | '60days' (upcoming only)
    
    Returns:
        HTML fragment with project cards or user table
    """
    view_type = request.args.get('view', 'upcoming')
    facilities = request.args.getlist('facilities')
    resource = request.args.get('resource', None)
    time_range = request.args.get('time_range', '31days')
    
    if not facilities:
        facilities = ['UNIV', 'WNA']
    
    # Query based on view type
    if view_type == 'upcoming':
        days = UPCOMING_PRESETS.get(time_range, 31)
        results = get_projects_by_allocation_end_date(
            db.session,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=days),
            facility_names=facilities,
            resource_name=resource if resource else None
        )
        
    elif view_type == 'expired':
        results = get_projects_with_expired_allocations(
            db.session,
            max_days_expired=90,
            min_days_expired=365,
            facility_names=facilities,
            resource_name=resource if resource else None
        )
        
    elif view_type == 'abandoned':
        # Get expired projects first
        expired_results = get_projects_with_expired_allocations(
            db.session,
            max_days_expired=90,
            min_days_expired=365,
            facility_names=facilities,
            resource_name=resource if resource else None
        )
        # Find abandoned users
        abandoned_users = _get_abandoned_users_data(expired_results)
        
        return render_template(
            'dashboards/user/fragments/abandoned_users_table.html',
            abandoned_users=abandoned_users
        )
    
    # Transform to project_data format
    projects_data = _build_expiration_project_data(results)
    
    return render_template(
        'dashboards/user/fragments/expirations_cards.html',
        projects_data=projects_data,
        view_type=view_type,
        user=current_user,
        usage_warning_threshold=USAGE_WARNING_THRESHOLD,
        usage_critical_threshold=USAGE_CRITICAL_THRESHOLD
    )


@bp.route('/admin/expirations/export')
@login_required
@require_permission(Permission.VIEW_PROJECTS)
def expirations_export():
    """
    Export expirations data to CSV.
    
    Mirrors Flask-Admin export functionality.
    """
    # Reuse logic from admin/expiration_views.py export() method
    # Same query parameters as expirations_fragment()
    pass
```

5. Add helper functions to blueprint:

```python
def _build_expiration_project_data(expiring_results: List[Tuple]) -> List[Dict]:
    """Transform expiration tuples to project_data dicts."""
    # Implementation as described in Data Flow section
    pass

def _get_abandoned_users_data(expired_results: List[Tuple]) -> List[Dict]:
    """Find users who only have expired projects."""
    # Reuse logic from admin/expiration_views.py _get_abandoned_users()
    # Return list of dicts with username, display_name, email, projects
    pass

# Constants (from admin view)
UPCOMING_PRESETS = {
    '7days': 7,
    '31days': 31,
    '60days': 60
}
```

### Phase 3: Fragment Templates

**Goal:** Create HTML fragments for AJAX-loaded content.

6. Create `/src/webapp/templates/dashboards/user/fragments/expirations_cards.html`:

```html
{% from 'dashboards/user/partials/project_card.html' import render_project_card with context %}

{% if projects_data %}
    <div class="row">
        {% for project_data in projects_data %}
            <div class="col-12">
                {{ render_project_card(
                    project_data,
                    loop.index0,
                    user,
                    usage_warning_threshold,
                    usage_critical_threshold,
                    is_expanded=false,
                    expiration_info=project_data.get('expirations', [])
                ) }}
            </div>
        {% endfor %}
    </div>
    
    <!-- Summary footer -->
    <div class="text-muted text-center mt-3">
        <small>Showing {{ projects_data|length }} project{{ 's' if projects_data|length != 1 else '' }}</small>
    </div>
{% else %}
    <div class="alert alert-info">
        <i class="fas fa-info-circle"></i>
        No {{ view_type }} expirations found with current filters.
    </div>
{% endif %}
```

7. Create `/src/webapp/templates/dashboards/user/fragments/abandoned_users_table.html`:

```html
{% if abandoned_users %}
<div class="table-responsive">
    <table class="table table-striped table-hover">
        <thead class="thead-light">
            <tr>
                <th>Username</th>
                <th>Name</th>
                <th>Email</th>
                <th>Expired Projects</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for user_info in abandoned_users %}
            <tr>
                <td><strong>{{ user_info.username }}</strong></td>
                <td>{{ user_info.display_name }}</td>
                <td>
                    {% if user_info.email != 'N/A' %}
                    <a href="mailto:{{ user_info.email }}">{{ user_info.email }}</a>
                    {% else %}
                    {{ user_info.email }}
                    {% endif %}
                </td>
                <td><small class="text-muted">{{ user_info.projects }}</small></td>
                <td>
                    <button type="button" 
                            class="btn btn-sm btn-outline-primary impersonate-user-btn"
                            data-username="{{ user_info.username }}">
                        <i class="fas fa-user-secret"></i> Impersonate
                    </button>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>

<div class="text-muted text-center mt-3">
    <small>{{ abandoned_users|length }} abandoned user{{ 's' if abandoned_users|length != 1 else '' }}</small>
</div>
{% else %}
<div class="alert alert-success">
    <i class="fas fa-check-circle"></i>
    No abandoned users found with current filters.
</div>
{% endif %}
```

### Phase 4: Expirations Section in Admin Tab

**Goal:** Add collapsible expirations section to admin tab.

8. Modify `/src/webapp/templates/dashboards/user/admin_section.html`:

Add after project search section:

```html
<!-- Expirations Section -->
<div class="row mt-4">
    <div class="col-12">
        <div class="card">
            <div class="card-header" style="cursor: pointer;" 
                 data-toggle="collapse" data-target="#expirations-section">
                <h5 class="mb-0">
                    <i class="fas fa-calendar-exclamation"></i> Project Expirations
                    <i class="fas fa-chevron-down float-right"></i>
                </h5>
            </div>
            <div id="expirations-section" class="collapse">
                <div class="card-body">
                    <!-- Filters Form -->
                    <form id="expirations-filters-form" class="mb-3">
                        <div class="row">
                            <!-- Facilities -->
                            <div class="col-md-4">
                                <label><strong>Facilities</strong></label>
                                <div class="form-group">
                                    <div class="form-check">
                                        <input type="checkbox" class="form-check-input facility-filter" 
                                               id="fac-univ" name="facilities" value="UNIV" checked>
                                        <label class="form-check-label" for="fac-univ">UNIV</label>
                                    </div>
                                    <div class="form-check">
                                        <input type="checkbox" class="form-check-input facility-filter" 
                                               id="fac-wna" name="facilities" value="WNA" checked>
                                        <label class="form-check-label" for="fac-wna">WNA</label>
                                    </div>
                                    <div class="form-check">
                                        <input type="checkbox" class="form-check-input facility-filter" 
                                               id="fac-csl" name="facilities" value="CSL">
                                        <label class="form-check-label" for="fac-csl">CSL</label>
                                    </div>
                                </div>
                            </div>
                            
                            <!-- Resource -->
                            <div class="col-md-4">
                                <div class="form-group">
                                    <label for="resource-filter"><strong>Resource (optional)</strong></label>
                                    <input type="text" class="form-control" id="resource-filter" 
                                           placeholder="e.g., Derecho, GLADE">
                                    <small class="form-text text-muted">Leave blank for all resources</small>
                                </div>
                            </div>
                            
                            <!-- Time Range (for upcoming) -->
                            <div class="col-md-4">
                                <div class="form-group">
                                    <label for="time-range-filter"><strong>Time Range</strong></label>
                                    <select class="form-control" id="time-range-filter">
                                        <option value="7days">Next 7 days</option>
                                        <option value="31days" selected>Next 31 days</option>
                                        <option value="60days">Next 60 days</option>
                                    </select>
                                    <small class="form-text text-muted">For upcoming expirations</small>
                                </div>
                            </div>
                        </div>
                        
                        <div class="row">
                            <div class="col-12">
                                <button type="button" id="apply-expirations-filters" class="btn btn-primary">
                                    <i class="fas fa-search"></i> Apply Filters
                                </button>
                                <button type="button" id="export-expirations-csv" class="btn btn-success">
                                    <i class="fas fa-download"></i> Export CSV
                                </button>
                            </div>
                        </div>
                    </form>
                    
                    <!-- Nav Pills for Sub-tabs -->
                    <ul class="nav nav-pills mb-3" id="expirations-tabs" role="tablist">
                        <li class="nav-item">
                            <a class="nav-link active" id="upcoming-tab" data-toggle="pill" 
                               href="#exp-upcoming" role="tab" data-view="upcoming">
                                <i class="fas fa-calendar-alt"></i> Upcoming
                                <span class="badge badge-light" id="upcoming-count">-</span>
                            </a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" id="expired-tab" data-toggle="pill" 
                               href="#exp-expired" role="tab" data-view="expired">
                                <i class="fas fa-exclamation-triangle"></i> Expired
                                <span class="badge badge-light" id="expired-count">-</span>
                            </a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" id="abandoned-tab" data-toggle="pill" 
                               href="#exp-abandoned" role="tab" data-view="abandoned">
                                <i class="fas fa-user-slash"></i> Abandoned Users
                                <span class="badge badge-light" id="abandoned-count">-</span>
                            </a>
                        </li>
                    </ul>
                    
                    <!-- Tab Content -->
                    <div class="tab-content" id="expirations-tab-content">
                        <div class="tab-pane fade show active" id="exp-upcoming" role="tabpanel">
                            <div id="upcoming-container" class="expirations-container">
                                <div class="text-center py-4">
                                    <div class="spinner-border text-primary" role="status">
                                        <span class="sr-only">Loading upcoming expirations...</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="tab-pane fade" id="exp-expired" role="tabpanel">
                            <div id="expired-container" class="expirations-container">
                                <!-- Lazy loaded -->
                            </div>
                        </div>
                        
                        <div class="tab-pane fade" id="exp-abandoned" role="tabpanel">
                            <div id="abandoned-container" class="expirations-container">
                                <!-- Lazy loaded -->
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
```

### Phase 5: JavaScript for Interactivity

**Goal:** Handle filter changes, tab switching, AJAX loading.

9. Create `/src/webapp/static/js/expirations.js`:

```javascript
/**
 * Expirations panel functionality for admin tab
 */

(function() {
    'use strict';
    
    let currentView = 'upcoming';
    
    /**
     * Initialize expirations panel
     */
    function initExpirations() {
        const expirationsSection = document.getElementById('expirations-section');
        if (!expirationsSection) {
            return; // Not on admin tab
        }
        
        // Load upcoming data immediately when section is shown
        expirationsSection.addEventListener('shown.bs.collapse', function() {
            if (!this.dataset.loaded) {
                loadExpirations('upcoming');
                this.dataset.loaded = 'true';
            }
        });
        
        // Tab switching
        document.querySelectorAll('#expirations-tabs a[data-toggle="pill"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', function(e) {
                currentView = this.dataset.view;
                const container = document.getElementById(`${currentView}-container`);
                
                // Load if not already loaded
                if (container && !container.dataset.loaded) {
                    loadExpirations(currentView);
                    container.dataset.loaded = 'true';
                }
            });
        });
        
        // Apply filters button
        document.getElementById('apply-expirations-filters')?.addEventListener('click', function() {
            // Reload current view with new filters
            const container = document.getElementById(`${currentView}-container`);
            if (container) {
                container.dataset.loaded = 'false'; // Force reload
                loadExpirations(currentView);
            }
        });
        
        // Export CSV button
        document.getElementById('export-expirations-csv')?.addEventListener('click', function() {
            exportExpirations();
        });
    }
    
    /**
     * Load expirations data via AJAX
     */
    function loadExpirations(view) {
        const container = document.getElementById(`${view}-container`);
        if (!container) return;
        
        // Show loading spinner
        container.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="sr-only">Loading...</span>
                </div>
            </div>
        `;
        
        // Build query parameters from filters
        const params = new URLSearchParams();
        params.append('view', view);
        
        // Facilities
        document.querySelectorAll('.facility-filter:checked').forEach(input => {
            params.append('facilities', input.value);
        });
        
        // Resource
        const resource = document.getElementById('resource-filter')?.value;
        if (resource) {
            params.append('resource', resource);
        }
        
        // Time range (for upcoming only)
        if (view === 'upcoming') {
            const timeRange = document.getElementById('time-range-filter')?.value;
            if (timeRange) {
                params.append('time_range', timeRange);
            }
        }
        
        // Fetch data
        fetch(`/user/admin/expirations?${params.toString()}`)
            .then(response => response.text())
            .then(html => {
                container.innerHTML = html;
                
                // Update count badge
                const count = extractCount(html, view);
                updateCountBadge(view, count);
                
                // Initialize lazy loading for project cards
                if (window.initLazyLoading) {
                    window.initLazyLoading();
                }
                
                // Attach impersonate handlers for abandoned users
                if (view === 'abandoned') {
                    attachImpersonateHandlers();
                }
            })
            .catch(error => {
                console.error('Error loading expirations:', error);
                container.innerHTML = `
                    <div class="alert alert-danger">
                        <i class="fas fa-exclamation-triangle"></i>
                        Error loading expirations data
                    </div>
                `;
            });
    }
    
    /**
     * Export expirations to CSV
     */
    function exportExpirations() {
        const params = new URLSearchParams();
        params.append('export_type', currentView);
        
        // Add filters
        document.querySelectorAll('.facility-filter:checked').forEach(input => {
            params.append('facilities', input.value);
        });
        
        const resource = document.getElementById('resource-filter')?.value;
        if (resource) {
            params.append('resource', resource);
        }
        
        if (currentView === 'upcoming') {
            const timeRange = document.getElementById('time-range-filter')?.value;
            if (timeRange) {
                params.append('time_range', timeRange);
            }
        }
        
        // Open in new window (downloads file)
        window.open(`/user/admin/expirations/export?${params.toString()}`, '_blank');
    }
    
    /**
     * Extract count from HTML (for badge)
     */
    function extractCount(html, view) {
        // Parse count from "Showing X projects" or table rows
        const match = html.match(/Showing (\d+) project/i) || 
                     html.match(/(\d+) abandoned user/i) ||
                     html.match(/<tr[^>]*>/g);
        
        if (match) {
            if (typeof match[1] === 'string') {
                return parseInt(match[1], 10);
            } else {
                return match.length - 1; // Subtract header row
            }
        }
        return 0;
    }
    
    /**
     * Update count badge in tab
     */
    function updateCountBadge(view, count) {
        const badge = document.getElementById(`${view}-count`);
        if (badge) {
            badge.textContent = count;
            badge.classList.remove('badge-light');
            badge.classList.add(count > 0 ? 'badge-primary' : 'badge-secondary');
        }
    }
    
    /**
     * Attach impersonate button handlers
     */
    function attachImpersonateHandlers() {
        document.querySelectorAll('.impersonate-user-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const username = this.dataset.username;
                if (username && confirm(`Impersonate user ${username}?`)) {
                    // Use existing impersonation form
                    const form = document.getElementById('impersonateUserForm');
                    const usernameInput = document.getElementById('selectedUsernameImpersonate');
                    
                    if (form && usernameInput) {
                        usernameInput.value = username;
                        form.submit();
                    }
                }
            });
        });
    }
    
    // Initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initExpirations);
    } else {
        initExpirations();
    }
})();
```

10. Add JavaScript include to `/src/webapp/templates/dashboards/user/dashboard.html`:

```html
{% block extra_js %}
<script src="{{ url_for('static', filename='js/impersonation.js') }}"></script>
<script src="{{ url_for('static', filename='js/project-search.js') }}"></script>
<script src="{{ url_for('static', filename='js/expirations.js') }}"></script>
{% endblock %}
```

### Phase 6: Project Card Enhancement

**Goal:** Add expiration badges to project cards.

11. Modify `/src/webapp/templates/dashboards/user/partials/project_card.html`:

Update macro signature:
```html
{% macro render_project_card(project_data, loop_index, user, usage_warning_threshold, usage_critical_threshold, is_expanded=false, expiration_info=none) %}
```

Add expiration badges after project title (in card header):
```html
<h5 class="mb-0">
    <i class="fas fa-folder-open text-primary"></i>
    <strong>{{ project.projcode }}</strong> - {{ project.title or 'Untitled Project' }}
    
    <!-- NEW: Expiration badges -->
    {% if expiration_info %}
        {% for exp in expiration_info %}
            {% set days = exp.days %}
            {% if days is not none %}
                {% if days <= 0 %}
                    {# Expired #}
                    <span class="badge badge-secondary ml-2" 
                          title="Expired {{ days|abs }} days ago">
                        <i class="fas fa-times-circle"></i>
                        {{ exp.resource_name }}: {{ days|abs }} days ago
                    </span>
                {% elif days <= 7 %}
                    {# Critical: 7 days or less #}
                    <span class="badge badge-danger ml-2" 
                          title="Expires in {{ days }} days">
                        <i class="fas fa-exclamation-circle"></i>
                        {{ exp.resource_name }}: {{ days }}d
                    </span>
                {% elif days <= 30 %}
                    {# Warning: 8-30 days #}
                    <span class="badge badge-warning ml-2" 
                          title="Expires in {{ days }} days">
                        <i class="fas fa-exclamation-triangle"></i>
                        {{ exp.resource_name }}: {{ days }}d
                    </span>
                {% else %}
                    {# Info: 31+ days #}
                    <span class="badge badge-info ml-2" 
                          title="Expires in {{ days }} days">
                        <i class="fas fa-calendar"></i>
                        {{ exp.resource_name }}: {{ days }}d
                    </span>
                {% endif %}
            {% endif %}
        {% endfor %}
    {% endif %}
</h5>
```

---

## Testing Strategy

### Manual Testing Checklist

**Phase 1 (Template Refactoring):**
- [ ] Admin tab loads without errors
- [ ] Impersonation form works
- [ ] Project search works
- [ ] Project cards display correctly
- [ ] No visual regressions

**Phase 2-4 (Expirations Section):**
- [ ] Expirations section appears in admin tab
- [ ] Section collapses/expands correctly
- [ ] Filters form displays properly
- [ ] Three tabs (Upcoming/Expired/Abandoned) are visible

**Phase 5-6 (Interactivity):**
- [ ] Upcoming expirations load on first expand
- [ ] Project cards display with expiration badges
- [ ] Badge colors correct (red ≤7d, yellow ≤30d, info >30d, gray expired)
- [ ] Tab switching loads respective data
- [ ] Filters (facilities, resource, time range) apply correctly
- [ ] CSV export downloads file
- [ ] Abandoned users table displays
- [ ] Impersonate button works from abandoned users table
- [ ] Card lazy-loading (members, tree) works in expiration cards
- [ ] Multiple expiring allocations show multiple badges

### Edge Cases

1. **No results:** Each view should show "No X found" message
2. **Mixed facilities:** Project with allocations from multiple facilities
3. **NULL end_dates:** Should not appear in results (handled by query)
4. **Inactive projects:** Should not appear (filtered by query)
5. **Deleted allocations:** Should not appear (filtered by query)
6. **Multiple expiring allocations on same project:** Show all badges
7. **Long resource names:** Badge text should wrap/truncate gracefully

### Performance Testing

1. Test with ~50 expiring projects (typical case)
2. Test with ~200 expiring projects (stress case)
3. Monitor query times in debug toolbar
4. Verify no N+1 queries in `_build_expiration_project_data()`

### Browser Testing

- [ ] Chrome/Edge (latest)
- [ ] Firefox (latest)
- [ ] Safari (latest)

---

## Future Enhancements (Out of Scope)

1. **Real-time updates:** WebSocket to push new expirations
2. **Email notifications:** "Remind PI" button to send expiration emails
3. **Bulk actions:** "Extend all" button to batch-extend allocations
4. **Pagination:** For >100 results, add pagination controls
5. **Advanced filters:** Filter by allocation type, panel, organization
6. **Caching:** Cache project_data for 5 minutes to improve performance
7. **Custom date ranges:** Allow admin to specify exact date range
8. **Project comparison:** Side-by-side comparison of multiple projects

---

## Migration/Rollback Plan

**Safe Deployment:**
- Feature is additive (no existing functionality removed)
- Template refactoring (Phase 1) can deploy independently
- If issues arise, remove `{% include 'admin_section.html' %}` line to revert

**Rollback Steps:**
1. Revert dashboard.html to inline admin tab content
2. Remove new routes from blueprint.py
3. Remove new template files
4. Remove expirations.js

**Database Changes:**
- None (read-only feature)

---

## Performance Considerations

**Query Complexity:**
- `get_projects_by_allocation_end_date()`: 1 query (with subquery), ~50ms for 1000 projects
- `get_project_dashboard_data()`: 1 query + 1 usage calculation per project
- For 50 projects: ~50 queries + 50 usage calculations = ~2-3 seconds total
- **Acceptable:** Admin feature, not on critical path, shows loading spinner

**Optimization Opportunities (if needed):**
1. Batch load projects instead of one-by-one
2. Cache usage calculations for 5 minutes
3. Add pagination (load 20 projects at a time)
4. Implement lazy scroll (infinite scroll pattern)

**Memory Usage:**
- 50 project objects + usage data = ~5-10 MB
- **Acceptable:** Admin users expected to have good network/browser

---

## Security Considerations

**Permission Checks:**
- All new routes require `@require_permission(Permission.VIEW_PROJECTS)`
- Inherits existing admin tab permission checks
- No PII exposed beyond what admin already sees

**Data Exposure:**
- Shows ALL projects (not filtered to user) - by design
- Admin dashboard already shows same data in different format
- CSV export matches Flask-Admin export (already approved)

**XSS Prevention:**
- Jinja2 auto-escapes all variables
- No raw HTML injection
- User input (filters) properly escaped in query parameters

**CSRF Protection:**
- GET-only endpoints (no state changes)
- CSV export uses GET (standard download pattern)
- Impersonate button reuses existing POST form with CSRF token

---

## Documentation Updates

**Files to Update:**

1. **`CLAUDE.md`** - Add section:
```markdown
## User Dashboard Expirations Panel

### Location
User Dashboard → Admin Tab → Expirations (collapsible section)

### Features
- View upcoming/expired allocations across ALL projects
- Three sub-tabs: Upcoming, Expired, Abandoned Users
- Filters: Facilities (UNIV/WNA/CSL), Resource, Time Range
- CSV export for all views
- Project cards with expiration badges
- Quick impersonate from abandoned users table

### Routes
- `GET /user/admin/expirations` - AJAX fragment loader
- `GET /user/admin/expirations/export` - CSV export

### Usage
```python
# In blueprint routes
from sam.queries.expirations import get_projects_by_allocation_end_date

results = get_projects_by_allocation_end_date(
    session,
    days_from_now=31,
    facility_names=['UNIV', 'WNA']
)

# Transform to project_data format
projects_data = _build_expiration_project_data(results)
```
```

2. **README.md** (if exists) - Update screenshots/features list

---

## Dependencies

**External Libraries:**
- None (uses existing Bootstrap 4, jQuery, Font Awesome)

**Internal Dependencies:**
- `sam.queries.expirations` - Already exists, well-tested
- `sam.queries.dashboard.get_project_dashboard_data()` - Already exists
- `webapp.utils.rbac.require_permission` - Already exists
- Project card macro - Already exists, adding optional parameter

**Database:**
- Read-only queries, no migrations needed

---

## Critical Files for Implementation

1. **`/src/webapp/dashboards/user/blueprint.py`**
   - Core implementation: Routes, helper functions, data transformation logic
   - ~200 lines of new code
   - Most complex file with business logic

2. **`/src/webapp/templates/dashboards/user/admin_section.html`**
   - New template file extracted from dashboard.html
   - Contains expirations section HTML structure
   - ~250 lines total (80 existing + 170 new)

3. **`/src/webapp/static/js/expirations.js`**
   - Frontend interactivity: AJAX loading, filters, tab switching
   - ~200 lines of JavaScript
   - Critical for user experience

4. **`/src/webapp/templates/dashboards/user/partials/project_card.html`**
   - Modify macro to accept expiration_info parameter
   - Add expiration badges rendering logic
   - ~30 lines of changes to existing ~230 line file

5. **`/src/sam/queries/expirations.py`**
   - Reference implementation for query logic (already exists)
   - Blueprint will reuse these functions directly
   - No modifications needed, but critical to understand
