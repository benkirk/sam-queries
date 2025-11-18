# Normal User Dashboard - Implementation Plan

**Target User Role:** Normal User (read-only access to own projects)
**Status:** Ready for Implementation
**Created:** 2025-11-17
**Estimated Time:** 13 hours

---

## Overview

This plan focuses on implementing dashboard improvements for the Normal User role. Normal Users can view their projects, resource usage, and project hierarchies but cannot edit anything.

**Based on:** `PAGE_STRUCTURE_BY_ROLE.md` (Lines 19-147) and `DASHBOARD_IMPROVEMENT_PLAN.md`

---

## Current State vs Target State

### What's Already Working ✅

Normal Users currently have:
- ✅ Login/logout functionality
- ✅ Two-tab dashboard (Account Statements, User Information)
- ✅ Project cards that expand/collapse
- ✅ Overall usage stats (4 boxes: ALLOCATED, USED, REMAINING, USAGE%)
- ✅ Resource usage table with all details
- ✅ Click resource → Navigate to detailed resource page
- ✅ Resource details page with charts, jobs, and charge history
- ✅ Back to dashboard navigation

### What Needs Implementation ⏳

Normal Users need:
- ⏳ **Project cards collapsed by default** (currently expanded)
- ⏳ **Fixed header section** (visible even when card collapsed)
- ⏳ **Reorganized card sections** (3 collapsible sections)
- ⏳ **Project tree visualization** (show parent/child relationships)
- ⏳ **State persistence** (remember expand/collapse preferences)

---

## Implementation Phases

### Phase 1: Restructure Project Cards (2 hours)

**Goal:** Reorganize project cards to match requirements from `PAGE_STRUCTURE_BY_ROLE.md`

**File to Modify:** `python/webui/templates/user/dashboard.html`

#### Step 1.1: Change Default Collapse State

**Current (Line 238):**
```javascript
<div id="${cardId}" class="collapse show">
```

**Change to:**
```javascript
<div id="${cardId}" class="collapse">
```

**Result:** All project cards start collapsed on page load

---

#### Step 1.2: Add Fixed Header Section

Add non-collapsible header showing key project info even when card is collapsed.

**Insert after card header (around line 237), before the collapsible section:**

```javascript
<!-- Fixed Project Header (always visible) -->
<div class="card-body border-bottom bg-light py-2">
    <div class="row small">
        <div class="col-md-6">
            <strong>Lead:</strong> ${project.lead_name || 'Not assigned'}
            ${project.admin_name ? `<span class="ml-3"><strong>Admin:</strong> ${project.admin_name}</span>` : ''}
        </div>
        <div class="col-md-6 text-right">
            <strong>Dates:</strong>
            ${project.start_date ? new Date(project.start_date).toLocaleDateString() : 'N/A'}
            to
            ${project.end_date ? new Date(project.end_date).toLocaleDateString() : 'N/A'}
        </div>
    </div>
</div>
```

**Note:** Backend API needs to include `start_date`, `end_date`, and `admin_name` in project data.

---

#### Step 1.3: Reorganize Card Body into 3 Sections

Replace current card body (lines 239-281) with new 3-section structure:

```javascript
<div id="${cardId}" class="collapse" data-projcode="${project.projcode}">
    <div class="card-body">

        <!-- Section A: Overall Usage Table (COLLAPSED by default) -->
        <div class="card mb-2">
            <div class="card-header py-2" style="cursor: pointer; background: #f8f9fa;"
                 data-toggle="collapse" data-target="#overall-${index}">
                <h6 class="mb-0">
                    <i class="fas fa-chevron-right chevron-icon"></i>
                    Overall Usage Table
                </h6>
            </div>
            <div id="overall-${index}" class="collapse" data-section="overall_usage">
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-sm table-bordered table-hover">
                            <thead class="thead-light">
                                <tr>
                                    <th>Resource</th>
                                    <th>Status</th>
                                    <th>Start</th>
                                    <th>End</th>
                                    <th>Allocated</th>
                                    <th>Used</th>
                                    <th>Balance</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${project.resources.map(r => `
                                    <tr>
                                        <td><strong>${r.resource_name}</strong></td>
                                        <td>${r.status === 'Active' ? '<span class="badge badge-success">Active</span>' : '<span class="badge badge-secondary">Inactive</span>'}</td>
                                        <td>${r.start_date ? new Date(r.start_date).toLocaleDateString() : 'N/A'}</td>
                                        <td>${r.end_date ? new Date(r.end_date).toLocaleDateString() : 'N/A'}</td>
                                        <td class="text-right">${formatNumber(r.allocated)}</td>
                                        <td class="text-right">${formatNumber(r.used)}</td>
                                        <td class="text-right">${formatNumber(r.remaining)}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- Section B: Services Breakdown (EXPANDED by default) -->
        <div class="card mb-2">
            <div class="card-header py-2" style="cursor: pointer; background: #f8f9fa;"
                 data-toggle="collapse" data-target="#services-${index}">
                <h6 class="mb-0">
                    <i class="fas fa-chevron-down chevron-icon"></i>
                    Services Breakdown
                </h6>
            </div>
            <div id="services-${index}" class="collapse show" data-section="services">
                <div class="card-body">
                    <!-- Overall Usage Stats (keep existing 4 boxes) -->
                    <div class="row mb-3">
                        <div class="col-md-3">
                            <div class="text-center p-3 project-stats-box rounded">
                                <small class="text-muted font-weight-bold">ALLOCATED</small>
                                <h5 class="mb-0 mt-1" style="color: #0099CC;">${formatNumber(project.total_allocated)}</h5>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="text-center p-3 project-stats-box rounded">
                                <small class="text-muted font-weight-bold">USED</small>
                                <h5 class="mb-0 mt-1" style="color: #f59e0b;">${formatNumber(project.total_used)}</h5>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="text-center p-3 project-stats-box rounded">
                                <small class="text-muted font-weight-bold">REMAINING</small>
                                <h5 class="mb-0 mt-1" style="color: #10b981;">${formatNumber(project.total_remaining)}</h5>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="text-center p-3 project-stats-box rounded">
                                <small class="text-muted font-weight-bold">USAGE</small>
                                <h5 class="mb-0 mt-1" style="color: #003366;">${project.percent_used.toFixed(1)}%</h5>
                            </div>
                        </div>
                    </div>

                    <!-- Progress Bar -->
                    <div class="progress mb-3" style="height: 30px;">
                        <div class="progress-bar ${percentClass}"
                             role="progressbar"
                             style="width: ${Math.min(project.percent_used, 100)}%"
                             aria-valuenow="${project.percent_used}"
                             aria-valuemin="0"
                             aria-valuemax="100">
                            ${project.percent_used.toFixed(1)}% Used
                        </div>
                    </div>

                    <!-- Resources Table (keep existing table from lines 154-218) -->
                    ${resourcesHtml}
                </div>
            </div>
        </div>

        <!-- Section C: Project Tree (COLLAPSED by default) -->
        <div class="card mb-2">
            <div class="card-header py-2" style="cursor: pointer; background: #f8f9fa;"
                 data-toggle="collapse" data-target="#tree-${index}">
                <h6 class="mb-0">
                    <i class="fas fa-chevron-right chevron-icon"></i>
                    Project Tree
                </h6>
            </div>
            <div id="tree-${index}" class="collapse" data-section="tree">
                <div class="card-body">
                    <div id="tree-container-${project.projcode}">
                        <div class="text-center text-muted py-3">
                            <i class="fas fa-spinner fa-spin"></i> Loading project tree...
                        </div>
                    </div>
                </div>
            </div>
        </div>

    </div>
</div>
```

---

#### Step 1.4: Update Chevron Icon Rotation

Update the chevron rotation logic (around lines 312-317) to handle nested sections:

```javascript
// Add chevron rotation handlers for all collapsible sections
$('.collapse').on('show.bs.collapse', function() {
    $(this).prev('.card-header').find('.chevron-icon').removeClass('fa-chevron-right').addClass('fa-chevron-down');
});
$('.collapse').on('hide.bs.collapse', function() {
    $(this).prev('.card-header').find('.chevron-icon').removeClass('fa-chevron-down').addClass('fa-chevron-right');
});
```

---

### Phase 2: Add Project Tree Visualization (8 hours)

**Goal:** Display parent/child project relationships with clickable navigation

#### Step 2.1: Create Backend API Endpoint

**File to Modify:** `python/webui/blueprints/user_dashboard.py`

Add new endpoint after `get_project_details` (around line 150):

```python
@bp.route('/api/project/<projcode>/tree')
@login_required
def get_project_tree(projcode):
    """
    Get project hierarchy (parent and children).

    Returns:
        JSON with parent project and list of child projects
    """
    from sam.projects.projects import Project

    # Get project
    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    # Check user has access to this project
    if not current_user.can_view_project(project):
        return jsonify({'error': 'Access denied'}), 403

    # Get parent
    parent_data = None
    if project.parent:
        parent_data = {
            'projcode': project.parent.projcode,
            'title': project.parent.title,
            'active': project.parent.active
        }

    # Get children
    children_data = []
    children = project.get_children()
    for child in children:
        children_data.append({
            'projcode': child.projcode,
            'title': child.title,
            'active': child.active,
            'has_children': child.has_children
        })

    return jsonify({
        'projcode': project.projcode,
        'title': project.title,
        'parent': parent_data,
        'children': children_data,
        'child_count': len(children_data)
    })
```

---

#### Step 2.2: Add Frontend Tree Component

**File to Modify:** `python/webui/templates/user/dashboard.html`

Add JavaScript functions in the `extra_js` block (after line 343):

```javascript
// Load project tree from API
function loadProjectTree(projcode, containerId) {
    const container = document.getElementById(containerId);

    // Show loading state
    container.innerHTML = '<div class="text-center text-muted py-3"><i class="fas fa-spinner fa-spin"></i> Loading project tree...</div>';

    fetch(`/dashboard/api/project/${projcode}/tree`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load project tree');
            }
            return response.json();
        })
        .then(data => {
            renderProjectTree(data, containerId);
        })
        .catch(error => {
            console.error('Error loading project tree:', error);
            container.innerHTML = '<div class="alert alert-warning">Unable to load project tree</div>';
        });
}

// Render project tree HTML
function renderProjectTree(data, containerId) {
    const container = document.getElementById(containerId);

    let html = '<div class="project-tree">';

    // Show parent (if exists)
    if (data.parent) {
        html += `
            <div class="tree-item tree-parent">
                <i class="fas fa-level-up-alt text-secondary"></i>
                <a href="#" onclick="navigateToProject('${data.parent.projcode}'); return false;">
                    <strong>${data.parent.projcode}</strong> - ${data.parent.title}
                </a>
                <span class="badge badge-secondary ml-2">Parent</span>
            </div>
        `;
    }

    // Show current project
    html += `
        <div class="tree-item tree-current">
            <i class="fas fa-folder-open text-primary"></i>
            <strong>${data.projcode} - ${data.title}</strong>
            <span class="badge badge-primary ml-2">Current</span>
        </div>
    `;

    // Show children (if any)
    if (data.children && data.children.length > 0) {
        html += '<div class="tree-children ml-4">';
        html += `
            <div class="text-muted mb-2 small">
                <i class="fas fa-sitemap"></i> ${data.child_count} Child Project${data.child_count > 1 ? 's' : ''}
            </div>
        `;

        data.children.forEach(child => {
            const activeClass = child.active ? 'tree-item-active' : 'tree-item-inactive';
            const statusBadge = child.active
                ? '<span class="badge badge-success badge-sm ml-2">Active</span>'
                : '<span class="badge badge-secondary badge-sm ml-2">Inactive</span>';

            html += `
                <div class="tree-item ${activeClass}">
                    <i class="fas fa-folder text-muted"></i>
                    <a href="#" onclick="navigateToProject('${child.projcode}'); return false;">
                        ${child.projcode} - ${child.title}
                    </a>
                    ${statusBadge}
                    ${child.has_children ? '<i class="fas fa-sitemap ml-2 text-muted" title="Has children"></i>' : ''}
                </div>
            `;
        });
        html += '</div>';
    } else {
        html += '<div class="text-muted ml-4 small">No child projects</div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

// Navigate to another project card
function navigateToProject(projcode) {
    // Find the project card
    const cards = document.querySelectorAll('[data-projcode]');
    let targetCard = null;

    for (const card of cards) {
        if (card.getAttribute('data-projcode') === projcode) {
            targetCard = card;
            break;
        }
    }

    if (targetCard) {
        // Scroll to card
        targetCard.scrollIntoView({ behavior: 'smooth', block: 'start' });

        // Expand the card
        $(targetCard).collapse('show');

        // Highlight briefly
        targetCard.style.backgroundColor = '#e3f2fd';
        setTimeout(() => {
            targetCard.style.backgroundColor = '';
        }, 2000);
    } else {
        // Project not in current user's list
        alert(`Project ${projcode} is not in your project list.`);
    }
}
```

---

#### Step 2.3: Load Tree When Section Expands

Add event listener to lazy-load tree data (add after loadProjects function):

```javascript
// Lazy load project tree when tree section is first opened
$(document).on('show.bs.collapse', '[data-section="tree"]', function() {
    const treeSection = $(this);
    const projcode = treeSection.closest('[data-projcode]').data('projcode');
    const containerId = `tree-container-${projcode}`;

    // Only load once
    if (!treeSection.data('tree-loaded')) {
        loadProjectTree(projcode, containerId);
        treeSection.data('tree-loaded', true);
    }
});
```

---

#### Step 2.4: Add Tree CSS Styling

**File to Modify:** `python/webui/templates/user/dashboard.html`

Add to `extra_css` block (around line 100):

```css
/* Project Tree Styles */
.project-tree {
    font-size: 0.9rem;
    padding: 1rem;
    background: #f8f9fa;
    border-radius: 6px;
}

.tree-item {
    padding: 0.5rem;
    margin: 0.25rem 0;
    border-left: 3px solid #dee2e6;
    transition: all 0.2s ease;
}

.tree-item:hover {
    background: white;
    border-left-color: #0099CC;
}

.tree-current {
    background: #e3f2fd;
    border-left-color: #0099CC;
    font-weight: 600;
}

.tree-parent {
    border-left-color: #6c757d;
}

.tree-item a {
    color: #0099CC;
    text-decoration: none;
}

.tree-item a:hover {
    text-decoration: underline;
}

.tree-item-inactive {
    opacity: 0.6;
}

.tree-children {
    margin-left: 1.5rem;
    border-left: 2px dashed #dee2e6;
    padding-left: 1rem;
}

.badge-sm {
    font-size: 0.75rem;
    padding: 0.2rem 0.4rem;
}
```

---

### Phase 3: Add State Persistence (3 hours)

**Goal:** Remember user's collapse/expand preferences using localStorage

#### Step 3.1: Create localStorage Utility Functions

**File to Modify:** `python/webui/templates/user/dashboard.html`

Add to `extra_js` block (before loadProjects function):

```javascript
// LocalStorage utilities for dashboard state
const DashboardState = {
    // Save which projects are expanded/collapsed
    saveProjectState: function(projcode, isExpanded) {
        const key = `dashboard_project_${projcode}`;
        localStorage.setItem(key, isExpanded ? 'expanded' : 'collapsed');
    },

    getProjectState: function(projcode) {
        const key = `dashboard_project_${projcode}`;
        return localStorage.getItem(key) || 'collapsed'; // default collapsed
    },

    // Save which sections within a project are expanded
    saveSectionState: function(projcode, sectionName, isExpanded) {
        const key = `dashboard_${projcode}_${sectionName}`;
        localStorage.setItem(key, isExpanded ? 'expanded' : 'collapsed');
    },

    getSectionState: function(projcode, sectionName) {
        const key = `dashboard_${projcode}_${sectionName}`;
        // Defaults: overall_usage=collapsed, services=expanded, tree=collapsed
        const defaults = {
            'overall_usage': 'collapsed',
            'services': 'expanded',
            'tree': 'collapsed'
        };
        return localStorage.getItem(key) || defaults[sectionName];
    },

    // Remember last viewed project
    saveLastProject: function(projcode) {
        localStorage.setItem('dashboard_last_project', projcode);
    },

    getLastProject: function() {
        return localStorage.getItem('dashboard_last_project');
    },

    // Clear all dashboard state (for testing)
    clearAll: function() {
        const keys = Object.keys(localStorage);
        keys.forEach(key => {
            if (key.startsWith('dashboard_')) {
                localStorage.removeItem(key);
            }
        });
    }
};
```

---

#### Step 3.2: Apply Saved State on Page Load

Update `createProjectCard` function to use saved state:

```javascript
function createProjectCard(project, index) {
    const percentClass = getProgressColor(project.percent_used);
    const cardId = `project-${index}`;

    // Get saved state from localStorage
    const savedProjectState = DashboardState.getProjectState(project.projcode);
    const projectCollapseClass = savedProjectState === 'expanded' ? 'collapse show' : 'collapse';

    const savedOverallState = DashboardState.getSectionState(project.projcode, 'overall_usage');
    const overallCollapseClass = savedOverallState === 'expanded' ? 'collapse show' : 'collapse';
    const overallChevron = savedOverallState === 'expanded' ? 'fa-chevron-down' : 'fa-chevron-right';

    const savedServicesState = DashboardState.getSectionState(project.projcode, 'services');
    const servicesCollapseClass = savedServicesState === 'expanded' ? 'collapse show' : 'collapse';
    const servicesChevron = savedServicesState === 'expanded' ? 'fa-chevron-down' : 'fa-chevron-right';

    const savedTreeState = DashboardState.getSectionState(project.projcode, 'tree');
    const treeCollapseClass = savedTreeState === 'expanded' ? 'collapse show' : 'collapse';
    const treeChevron = savedTreeState === 'expanded' ? 'fa-chevron-down' : 'fa-chevron-right';

    // Update main project card div
    // Change: <div id="${cardId}" class="collapse">
    // To:     <div id="${cardId}" class="${projectCollapseClass}" data-projcode="${project.projcode}">

    // Update section collapse classes
    // Overall: <div id="overall-${index}" class="${overallCollapseClass}" data-section="overall_usage" data-projcode="${project.projcode}">
    // Services: <div id="services-${index}" class="${servicesCollapseClass}" data-section="services" data-projcode="${project.projcode}">
    // Tree: <div id="tree-${index}" class="${treeCollapseClass}" data-section="tree" data-projcode="${project.projcode}">

    // Update chevron icons
    // Overall: <i class="fas ${overallChevron} chevron-icon"></i>
    // Services: <i class="fas ${servicesChevron} chevron-icon"></i>
    // Tree: <i class="fas ${treeChevron} chevron-icon"></i>

    // ... rest of card creation ...
}
```

---

#### Step 3.3: Save State on User Interaction

Add event listeners to save state when user expands/collapses:

```javascript
// Save project card state
$(document).on('shown.bs.collapse', '[data-projcode]', function() {
    const projcode = $(this).data('projcode');
    const sectionName = $(this).data('section');

    if (sectionName) {
        // This is a section within a project
        DashboardState.saveSectionState(projcode, sectionName, true);
    } else {
        // This is the main project card
        DashboardState.saveProjectState(projcode, true);
        DashboardState.saveLastProject(projcode);
    }
});

$(document).on('hidden.bs.collapse', '[data-projcode]', function() {
    const projcode = $(this).data('projcode');
    const sectionName = $(this).data('section');

    if (sectionName) {
        DashboardState.saveSectionState(projcode, sectionName, false);
    } else {
        DashboardState.saveProjectState(projcode, false);
    }
});
```

---

#### Step 3.4: Optional - Auto-expand Last Viewed Project

Add to end of `loadProjects` function (after rendering cards):

```javascript
// Auto-expand last viewed project (optional feature)
const lastProject = DashboardState.getLastProject();
if (lastProject) {
    const lastCard = document.querySelector(`[data-projcode="${lastProject}"]`);
    if (lastCard) {
        // Scroll to last viewed project
        setTimeout(() => {
            lastCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 500);
    }
}
```

---

## Testing Checklist

### Phase 1: Restructure
- [ ] All project cards are collapsed by default on page load
- [ ] Clicking card header expands/collapses the card
- [ ] Fixed header shows Lead, Admin, Dates even when card collapsed
- [ ] Three sub-sections are present: Overall Usage, Services, Tree
- [ ] Services Breakdown is expanded by default when card opens
- [ ] Overall Usage and Tree are collapsed by default
- [ ] Chevron icons rotate correctly on expand/collapse
- [ ] Clicking resource row still navigates to resource details page

### Phase 2: Project Tree
- [ ] Backend API `/dashboard/api/project/<projcode>/tree` returns parent/children
- [ ] Tree section shows "Loading..." while fetching
- [ ] Tree displays parent project (if exists) with "Parent" badge
- [ ] Tree displays current project with "Current" badge
- [ ] Tree displays all children with correct titles and status
- [ ] Clicking parent/child navigates to that project card (smooth scroll)
- [ ] Projects without children show "No child projects" message
- [ ] Tree handles errors gracefully (project not found, network error)
- [ ] Tree only loads once (lazy loading works)
- [ ] Inactive children show with reduced opacity

### Phase 3: State Persistence
- [ ] Expand a project card, refresh page → card stays expanded
- [ ] Collapse a section, refresh page → section stays collapsed
- [ ] Navigate to resource details and back → state preserved
- [ ] Clear localStorage → defaults restored (cards collapsed)
- [ ] Last viewed project is remembered (auto-scroll on page load)
- [ ] State is saved per project (expanding one doesn't affect others)

---

## Backend API Requirements

### Modifications Needed in `python/webui/blueprints/user_dashboard.py`

1. **Update `get_my_projects` endpoint** to include additional fields:
   ```python
   # Add to project data returned by API:
   'start_date': project.start_date,
   'end_date': project.end_date,
   'admin_name': project.admin.full_name if project.admin else None,
   'admin_username': project.admin.username if project.admin else None,
   ```

2. **Add new endpoint** `get_project_tree(projcode)` (see Phase 2, Step 2.1)

### No Database Changes Required
All data already exists in database:
- Project hierarchy: `parent_id`, `tree_left`, `tree_right` columns
- ORM methods: `project.get_children()`, `project.parent`
- Dates: `start_date`, `end_date` exist on allocations

---

## File Summary

### Files to Modify
1. **`python/webui/templates/user/dashboard.html`** (Main file)
   - Update `createProjectCard` function (~100 lines)
   - Add localStorage utilities (~50 lines)
   - Add tree rendering functions (~80 lines)
   - Add event listeners (~30 lines)
   - Add CSS for tree (~60 lines)

2. **`python/webui/blueprints/user_dashboard.py`**
   - Update `get_my_projects` to include admin and dates (~5 lines)
   - Add `get_project_tree` endpoint (~40 lines)

### No New Files Required
All changes are modifications to existing files.

---

## Estimated Effort

| Phase | Tasks | Duration |
|-------|-------|----------|
| Phase 1: Restructure | Change defaults, add fixed header, reorganize sections | 2 hours |
| Phase 2: Project Tree | Backend API + Frontend component + CSS | 8 hours |
| Phase 3: State Persistence | localStorage utilities + event listeners | 3 hours |
| **Total** | | **13 hours** |

---

## Success Criteria

Normal User dashboard is complete when:

1. ✅ **Cards start collapsed** - Less overwhelming on page load
2. ✅ **Key info always visible** - Fixed header shows lead, admin, dates
3. ✅ **Organized sections** - Three clear sections with appropriate defaults
4. ✅ **Project hierarchy visible** - Users can see parent/child relationships
5. ✅ **Interactive navigation** - Click tree nodes to navigate between projects
6. ✅ **State persistence** - User preferences remembered across sessions
7. ✅ **Responsive and fast** - Lazy loading, smooth animations
8. ✅ **No regressions** - All existing functionality still works (resource details navigation)

---

## Notes

- All changes maintain backward compatibility
- No database schema changes required
- Uses existing ORM methods from `sam/projects/projects.py`
- Bootstrap 4 and jQuery already available in base template
- Follows existing patterns in codebase
- Responsive design maintained throughout
- Error handling included for API failures
- Historical usage graphs remain in resource details pages only (not added to dashboard)

---

## Future Enhancements (Out of Scope)

For Normal User role, potential future improvements:
- Sortable columns in Overall Usage Table
- Export project data to CSV
- Filtering projects by status (Active/Inactive)
- Search projects by code or title
- Bulk expand/collapse all projects
- Email notifications for allocation warnings
- Project comparison view (side-by-side)

---

**Status:** Ready to implement
**Next Step:** Begin Phase 1 (Restructure Project Cards)
**Last Updated:** 2025-11-17
