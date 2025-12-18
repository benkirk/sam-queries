# Plan: Allocations Dashboard

## Overview
Create a drill-down allocations dashboard at `/allocations` that mimics `sam-search allocations` output with hierarchical grouping by Resource → Facility → Allocation Type → Projects.

## Requirements
- **Route**: `/allocations` (new blueprint)
- **Access Control**: Admin/Staff only - use `@require_permission(Permission.VIEW_PROJECTS)` decorator
- **Data source**: Direct ORM queries using `get_allocation_summary()` and `get_allocation_summary_with_usage()` from `src/sam/queries/allocations.py`
- **UI Structure**: Bootstrap tabs for Resource → nested tabs for Facility
- **Drill-down**: Summary tables at Resource/Facility/Type level, expandable to show individual projects
- **Usage visibility**: Hidden by default (expensive), shown on demand via Bootstrap modal when clicking individual allocations
- **Date filter**: "Active at" date picker input (defaults to today) - active allocations only
- **Export**: Not needed initially (can add later)
- **Pattern**: Server-side rendering like existing user/status dashboards

## Architecture

### 1. File Structure
```
src/webapp/dashboards/allocations/
├── __init__.py                 # Blueprint exports
├── blueprint.py                # Routes and view functions
└── (optional) helpers.py       # Data transformation helpers

src/webapp/templates/dashboards/allocations/
├── dashboard.html              # Main dashboard page with tabs
├── partials/
│   ├── summary_table.html      # Resource/Facility/Type summary table
│   ├── project_table.html      # Per-project allocation table
│   └── usage_modal.html        # Modal showing usage details
```

### 2. Blueprint Structure (`blueprint.py`)

#### Routes:
1. **`GET /allocations`** - Main dashboard page
   - **Decorator**: `@login_required` + `@require_permission(Permission.VIEW_PROJECTS)`
   - Query parameters:
     - `active_at` (optional, date YYYY-MM-DD, default: today)
   - Fetches allocation summary data grouped by Resource → Facility → Type
   - Renders dashboard.html with nested tab structure

2. **`GET /allocations/projects`** (AJAX fragment)
   - **Decorator**: `@login_required` + `@require_permission(Permission.VIEW_PROJECTS)`
   - Query parameters:
     - `resource`, `facility`, `allocation_type` (required for filtering)
     - `active_at` (date)
   - Returns HTML table of individual projects for a specific Resource/Facility/Type combination
   - Lazy-loaded when user expands a summary row (triggered by clicking [+] expand icon)

3. **`GET /allocations/usage/<projcode>/<resource>`** (AJAX/modal fragment)
   - **Decorator**: `@login_required` + `@require_permission(Permission.VIEW_PROJECTS)`
   - Returns JSON or HTML fragment for Bootstrap modal showing usage details
   - Calls `get_allocation_summary_with_usage()` for single project+resource
   - Shows: allocated, used, remaining, % used, charges by type breakdown
   - Triggered by clicking eye icon (👁) on individual project allocation row

4. **Helper function for facility overview chart**
   - Not a route - used in main dashboard route
   - Generates matplotlib pie chart showing annualized rate distribution across facilities for a resource
   - Called once per resource tab (Derecho, Casper, etc.)
   - Uses data from `get_allocation_summary()` grouped by facility

### 3. Data Flow

#### Main Dashboard Load:
```python
# In /allocations route handler:
active_at_date = parse_date_param(request.args.get('active_at', datetime.now()))

# Get summary data grouped by Resource, Facility, Type
summary_data = get_allocation_summary(
    session=db.session,
    resource_name=None,  # Group by all resources
    facility_name=None,  # Group by all facilities
    allocation_type=None,  # Group by all types
    projcode="TOTAL",  # Sum across projects
    active_only=True,
    active_at=active_at_date
)

# Group results hierarchically for tab structure
grouped_data = group_by_resource_facility(summary_data)
# Structure: {resource_name: {facility_name: [type_summaries]}}
```

#### Lazy-Load Projects Fragment:
```python
# In /allocations/projects fragment handler:
projects = get_allocation_summary(
    session=db.session,
    resource_name=request.args['resource'],
    facility_name=request.args['facility'],
    allocation_type=request.args['allocation_type'],
    projcode=None,  # Group by individual projects
    active_only=True,
    active_at=active_at_date
)
```

#### Show Usage (on-demand):
```python
# In /allocations/usage/<projcode>/<resource> handler:
usage_data = get_allocation_summary_with_usage(
    session=db.session,
    resource_name=resource,
    projcode=projcode,
    active_only=True,
    active_at=active_at_date
)
```

### 4. UI/UX Design

#### Tab Structure:
```
[Derecho Tab] [Casper Tab] [Campaign_Store Tab] ...
    └─ Active at: [date picker]

    Within Derecho Tab:
    ┌─────────────────────────────────────────────────────┐
    │ Resource Overview - Derecho                         │
    │ ┌─────────────────────────────────────────────────┐ │
    │ │ Annualized Rate by Facility                     │ │
    │ ├──────────┬───────────────┬─────────┬──────────┤ │
    │ │ Facility │ Annual Rate   │ Count   │ % Total  │ │
    │ ├──────────┼───────────────┼─────────┼──────────┤ │
    │ │ UNIV     │ 450,000       │ 689     │ 45%      │ │
    │ │ NCAR     │ 380,000       │ 125     │ 38%      │ │
    │ │ WNA      │ 170,000       │ 45      │ 17%      │ │
    │ └──────────┴───────────────┴─────────┴──────────┘ │
    │                                                     │
    │ [Pie Chart: Annualized Rate Distribution]           │
    │     (matplotlib SVG showing facility breakdown)     │
    └─────────────────────────────────────────────────────┘
    │                                                     │
    │ [UNIV Tab] [WNA Tab] [NCAR Tab] ...                 │
    │                                                     │
    │  Within UNIV Tab:                                   │
    │  ┌──────────────────────────────┐                   │
    │  │ Allocation Summary           │                   │
    │  ├──────┬────────┬────────┬─────┤                   │
    │  │ Type │ Total  │ Count  │ ... │                   │
    │  ├──────┼────────┼────────┼─────┤                   │
    │  │ NSC  │ 641.7M │ 26     │ [+] │ ← Click to expand projects
    │  │ Small│ 177.3M │ 248    │ [+] │                   │
    │  └──────┴────────┴────────┴─────┘                   │
    │                                                     │
    │  Expanded row (lazy-loaded):                        │
    │  ┌──────────────────────────────┐                   │
    │  │ Project Details - NSC        │                   │
    │  ├─────────┬─────────┬────┬─────┤                   │
    │  │ Project │ Amt     │ Dates    │ Usage │           │
    │  ├─────────┼─────────┼────┼─────┤                   │
    │  │ SCSG0001│ 25.0M   │... │ [👁] │ ← Click to show usage
    │  │ UCXX1234│ 50.0K   │... │ [👁] │                   │
    │  └─────────┴─────────┴────┴─────┘                   │
    └─────────────────────────────────────────────────────┘
```

#### Usage Modal (Bootstrap):
- Bootstrap modal overlay (`<div class="modal">`)
- Triggered by clicking eye icon (👁) on individual allocation row
- AJAX loads modal content from `/allocations/usage/<projcode>/<resource>`
- Modal body shows:
  - Header: Project code, resource name, allocation period
  - Summary stats: Allocated, Used, Remaining, % Used (color-coded)
  - Breakdown table: Charges by type (comp, dav, disk, archive)
  - Optional: Adjustments (if any)
  - Optional: Small matplotlib bar chart or pie chart
- Close button returns to main view without page reload

### 5. Implementation Details

#### Data Transformation Helper:
```python
def group_by_resource_facility(summary_data: List[Dict]) -> Dict:
    """
    Transform flat summary list into nested structure for tabs.

    Returns:
        {
            'Derecho': {
                'UNIV': [
                    {'allocation_type': 'NSC', 'total_amount': 641710650, 'count': 26, ...},
                    {'allocation_type': 'Small', 'total_amount': 177267070, 'count': 248, ...}
                ],
                'WNA': [...]
            },
            'Casper': {...}
        }
    """
    grouped = {}
    for row in summary_data:
        resource = row['resource']
        facility = row['facility']

        if resource not in grouped:
            grouped[resource] = {}
        if facility not in grouped[resource]:
            grouped[resource][facility] = []

        grouped[resource][facility].append(row)

    return grouped
```

#### Date Picker Integration:
- Use HTML5 `<input type="date">` for simplicity
- On change, reload page with `?active_at=YYYY-MM-DD` parameter
- JavaScript to auto-submit on date change

### 6. Blueprint Registration

Update `src/webapp/run.py`:
```python
from webapp.dashboards.allocations.blueprint import bp as allocations_dashboard_bp

# Register blueprint
app.register_blueprint(allocations_dashboard_bp)
```

### 7. Navigation Link

Add to main navigation template (likely `templates/base.html` or nav partial):
```html
<a class="nav-link" href="{{ url_for('allocations_dashboard.index') }}">
    <i class="fas fa-chart-pie"></i> Allocations
</a>
```

## Implementation Steps

### Step 1: Create Blueprint Structure
- Create `src/webapp/dashboards/allocations/` directory
- Create `__init__.py`, `blueprint.py`
- Define blueprint: `bp = Blueprint('allocations_dashboard', __name__, url_prefix='/allocations')`

### Step 2: Implement Main Dashboard Route
- Route: `@bp.route('/')`
- Add decorators: `@login_required` and `@require_permission(Permission.VIEW_PROJECTS)`
- Import: `from webapp.utils.rbac import require_permission, Permission`
- Parse `active_at` query parameter (default to today)
- Call `get_allocation_summary()` with grouping by Resource/Facility/Type, sum across projects (`projcode="TOTAL"`)
- Transform data into nested structure using helper function
- Render `dashboard.html` template

### Step 3: Create Main Dashboard Template
- Extend `dashboards/base.html`
- Top-level Bootstrap tabs for each Resource (Derecho, Casper, etc.)
- **Within each resource tab (BEFORE facility tabs):**
  - Display "Resource Overview" section
  - Facility summary table: Facility | Annual Rate | Count | % Total
  - Matplotlib pie chart showing annualized rate distribution by facility
  - Both generated server-side and passed to template
- **After overview section:**
  - Nested tabs for Facilities (UNIV, WNA, NCAR, etc.)
- Date picker form for "Active at" filter
- Summary table showing allocation types with expandable rows
- Use HTMX or vanilla JS to lazy-load project details on row expand

### Step 4: Implement Project Details Fragment
- Route: `@bp.route('/projects')`
- Query params: resource, facility, allocation_type, active_at
- Call `get_allocation_summary()` with projcode=None (group by projects)
- Render `partials/project_table.html` fragment
- Each row has "View Usage" button/icon

### Step 5: Implement Usage Modal Fragment
- Route: `@bp.route('/usage/<projcode>/<resource>')`
- Call `get_allocation_summary_with_usage()` for specific project+resource
- Render `partials/usage_modal.html` with detailed usage breakdown
- Show: allocated, used, remaining, % used, charges by type
- Optional: Generate matplotlib chart for daily usage trend

### Step 6: Register Blueprint
- Update `src/webapp/run.py` to register `allocations_dashboard_bp`
- Add navigation link to main nav template

### Step 6.5: Add Pie Chart Generator to charts.py
- Create new function: `generate_facility_pie_chart_matplotlib(facility_data: List[Dict]) -> str`
- Input: List of dicts with keys `facility`, `annualized_rate`, `count`
- Generate matplotlib pie chart showing percentage breakdown by facility
- Return SVG string for embedding in template
- Style: Use tab10 colormap, show percentages on pie slices

### Step 7: Styling and Polish
- Apply Bootstrap styling consistent with existing dashboards
- Color-code usage percentages (green < 75%, yellow < 90%, red >= 90%)
- Add loading spinners for AJAX requests
- Format large numbers with commas (e.g., 641,710,650)

### Step 8: Testing
- Test with various date filters
- Test drill-down from Resource → Facility → Type → Projects
- Test usage modal for different resource types (HPC, DISK, etc.)
- Verify decommissioned resources are excluded (Cheyenne shouldn't appear)

## Key Files to Create/Modify

### New Files:
- `src/webapp/dashboards/allocations/__init__.py`
- `src/webapp/dashboards/allocations/blueprint.py`
- `src/webapp/templates/dashboards/allocations/dashboard.html`
- `src/webapp/templates/dashboards/allocations/partials/summary_table.html`
- `src/webapp/templates/dashboards/allocations/partials/project_table.html`
- `src/webapp/templates/dashboards/allocations/partials/usage_modal.html`

### Modified Files:
- `src/webapp/run.py` (blueprint registration)
- `src/webapp/dashboards/charts.py` (add pie chart function)
- Navigation template (add allocations link)

## User Preferences (from clarification)
- **Access**: Admin/Staff only (use `@require_permission(Permission.VIEW_PROJECTS)`)
- **Export**: Not needed initially
- **Inactive allocations**: Active only (no toggle needed)
- **Usage display**: Bootstrap modal (recommended)
- **Facility overview**: Table + pie chart showing annualized rate distribution before facility tabs

## Notes
- Query functions already exist (`get_allocation_summary`, `get_allocation_summary_with_usage`) - no backend changes needed
- These functions already filter out inactive resources (Cheyenne won't appear)
- Usage queries are expensive - only run when explicitly requested via modal
- Follow existing dashboard patterns for consistency (see `user/blueprint.py` and `status/blueprint.py`)
- Use server-side rendering (no React/Vue needed)
- Leverage Bootstrap 4 tabs and modals
- Consider pagination if project lists exceed ~100 rows
- Color-code % used: green (<75%), yellow (75-90%), red (>90%)

## Estimated Complexity
- **Backend**: Low - reuse existing query functions
- **Frontend**: Medium - nested tabs + AJAX fragments + modal + pie chart
- **Total effort**: ~4-6 hours of focused development
