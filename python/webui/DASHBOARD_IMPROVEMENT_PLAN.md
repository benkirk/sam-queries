# HPC User Dashboard - Improvement Plan

**Status:** Ready for Implementation
**Created:** 2025-11-15
**Source:** prompt.md requirements

---

## Current State Analysis

### What's Already Working ✅

- ✅ Two-tab interface (Account Statements, User Information)
- ✅ Project cards with collapsible sections
- ✅ Resource breakdown table showing allocations
- ✅ Usage progress bars and stats (Allocated, Used, Remaining, Usage %)
- ✅ API endpoint `/dashboard/api/my-projects` returning project data
- ✅ Click-through to resource details page
- ✅ Status badges (Active/Inactive)
- ✅ Responsive design with Bootstrap 4

### What Needs to Change 🔄

1. **Project Cards Default State** - Currently EXPANDED, should be COLLAPSED
2. **Project Detail Structure** - Needs reorganization into sections
3. **State Persistence** - Add localStorage to remember user preferences
4. **Project Tree** - Add parent/child navigation hierarchy

---

## Implementation Plan

### Phase 1: Restructure Project Cards (Low Effort, ~2 hours)

**Goal:** Reorganize project cards to match requirements

**Changes to `python/webui/templates/user/dashboard.html`:**

1. **Change default collapse state:**
   ```javascript
   // Line 238: Change from
   <div id="${cardId}" class="collapse show">
   // To
   <div id="${cardId}" class="collapse">
   ```

2. **Add Fixed Header Section:**
   Create a non-collapsible header showing:
   - Project Code (e.g., SCSG0001)
   - Project Title
   - Project Lead name
   - Project Admin name
   - Start/End dates
   - "More Details" button (optional link to full project page)

3. **Reorganize Card Body into 3 Collapsible Sections:**

   **Section A: Overall Usage Table (COLLAPSED by default)**
   - Table with columns: Resource | Status | Start | End | Allocated | Used | Balance
   - Shows all resources in a simple table format
   - Sortable columns

   **Section B: Services Breakdown (EXPANDED by default)**
   - This is the current resources table (lines 154-218)
   - Enhanced with sortable columns
   - Keep progress bars and status badges

   **Section C: Project Tree (COLLAPSED by default)**
   - Placeholder div for future tree implementation
   - Shows message: "Project hierarchy will be displayed here"
   - Ready to populate with API data when backend is ready

**Example Structure:**
```html
<div class="card">
    <!-- Card Header (clickable, collapses all 3 sections) -->
    <div class="card-header" onclick="toggleCard()">
        Project: SCSG0001 [Badge: Active]
        Title: CSG Systems Project
    </div>

    <!-- Main Collapsible Container -->
    <div id="project-123" class="collapse">
        <div class="card-body">
            <!-- Fixed Header (non-collapsible) -->
            <div class="project-fixed-header">
                <div>Lead: Ben Kirk | Admin: Ben Kirk</div>
                <div>Dates: 2012-05-11 to 2026-09-30</div>
                <button>More Details</button>
            </div>

            <!-- Section A: Overall Usage Table (collapsible) -->
            <div class="card">
                <div class="card-header" onclick="toggleSection()">
                    Overall Usage Table [collapsed]
                </div>
                <div class="collapse">
                    [Table with simple resource summary]
                </div>
            </div>

            <!-- Section B: Services Breakdown (collapsible, default open) -->
            <div class="card">
                <div class="card-header" onclick="toggleSection()">
                    Services Breakdown [expanded]
                </div>
                <div class="collapse show">
                    [Current resources table - KEEP THIS]
                </div>
            </div>

            <!-- Section C: Project Tree (collapsible) -->
            <div class="card">
                <div class="card-header" onclick="toggleSection()">
                    Project Tree [collapsed]
                </div>
                <div class="collapse">
                    [Placeholder for tree]
                </div>
            </div>
        </div>
    </div>
</div>
```

---

### Phase 2: Add State Persistence (Medium Effort, ~3 hours)

**Goal:** Remember user's collapse/expand preferences across sessions

**Changes to `python/webui/templates/user/dashboard.html`:**

1. **Create localStorage Utility Functions:**
   ```javascript
   // Add to extra_js block

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
       }
   };
   ```

2. **Apply State on Page Load:**
   ```javascript
   // In createProjectCard(), check localStorage and apply state
   function createProjectCard(project, index) {
       const savedState = DashboardState.getProjectState(project.projcode);
       const collapseClass = savedState === 'expanded' ? 'collapse show' : 'collapse';

       // ... rest of card creation
   }
   ```

3. **Save State on User Interaction:**
   ```javascript
   // Add event listeners to capture collapse/expand events
   $('.collapse').on('shown.bs.collapse', function() {
       const projcode = $(this).data('projcode');
       const sectionName = $(this).data('section');
       DashboardState.saveSectionState(projcode, sectionName, true);
   });

   $('.collapse').on('hidden.bs.collapse', function() {
       const projcode = $(this).data('projcode');
       const sectionName = $(this).data('section');
       DashboardState.saveSectionState(projcode, sectionName, false);
   });
   ```

---

### Phase 3: Add Project Tree Navigation (High Effort, ~8 hours)

**Goal:** Display parent/child project relationships with clickable navigation

**Backend Changes Required:**

1. **New API Endpoint** (`python/webui/blueprints/user_dashboard.py`):
   ```python
   @bp.route('/api/project/<projcode>/tree')
   @login_required
   def get_project_tree(projcode):
       """
       Get project hierarchy (parent and children).

       Returns:
           JSON with parent project and list of child projects
       """
       from sam.queries import find_project_by_code

       project = find_project_by_code(db.session, projcode)
       if not project:
           return jsonify({'error': 'Project not found'}), 404

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
       children = project.get_children()  # Uses ORM method
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

2. **Frontend Tree Component** (`python/webui/templates/user/dashboard.html`):
   ```javascript
   // Add to extra_js block

   function loadProjectTree(projcode, containerId) {
       fetch(`/dashboard/api/project/${projcode}/tree`)
           .then(response => response.json())
           .then(data => {
               renderProjectTree(data, containerId);
           })
           .catch(error => {
               console.error('Error loading project tree:', error);
           });
   }

   function renderProjectTree(data, containerId) {
       const container = document.getElementById(containerId);

       let html = '<div class="project-tree">';

       // Show parent (if exists)
       if (data.parent) {
           html += `
               <div class="tree-item tree-parent">
                   <i class="fas fa-level-up-alt"></i>
                   <a href="#" onclick="navigateToProject('${data.parent.projcode}')">
                       ${data.parent.projcode} - ${data.parent.title}
                   </a>
                   <span class="badge badge-secondary ml-2">Parent</span>
               </div>
           `;
       }

       // Show current project
       html += `
           <div class="tree-item tree-current">
               <i class="fas fa-folder-open"></i>
               <strong>${data.projcode} - ${data.title}</strong>
               <span class="badge badge-primary ml-2">Current</span>
           </div>
       `;

       // Show children (if any)
       if (data.children && data.children.length > 0) {
           html += '<div class="tree-children ml-4">';
           html += `<div class="text-muted mb-2">
               <i class="fas fa-sitemap"></i> ${data.child_count} Child Projects:
           </div>`;

           data.children.forEach(child => {
               const activeClass = child.active ? 'tree-item-active' : 'tree-item-inactive';
               html += `
                   <div class="tree-item ${activeClass}">
                       <i class="fas fa-folder"></i>
                       <a href="#" onclick="navigateToProject('${child.projcode}')">
                           ${child.projcode} - ${child.title}
                       </a>
                       ${child.has_children ? '<i class="fas fa-sitemap ml-2 text-muted"></i>' : ''}
                   </div>
               `;
           });
           html += '</div>';
       } else {
           html += '<div class="text-muted ml-4">No child projects</div>';
       }

       html += '</div>';
       container.innerHTML = html;
   }

   function navigateToProject(projcode) {
       // Scroll to project card or reload with project highlighted
       const card = document.querySelector(`[data-projcode="${projcode}"]`);
       if (card) {
           card.scrollIntoView({ behavior: 'smooth', block: 'start' });
           // Optionally expand the card
           $(card).find('.collapse').collapse('show');
       }
   }
   ```

3. **CSS Styling** (add to extra_css block):
   ```css
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
   ```

4. **Load Tree on Card Expand:**
   ```javascript
   // In createProjectCard(), add data attribute
   <div id="${cardId}" class="collapse" data-projcode="${project.projcode}">

   // Add listener to load tree when section is first expanded
   $('.collapse[data-section="tree"]').on('show.bs.collapse', function() {
       const projcode = $(this).closest('.collapse').data('projcode');
       const treeContainerId = `tree-${projcode}`;

       // Only load once
       if (!$(this).data('tree-loaded')) {
           loadProjectTree(projcode, treeContainerId);
           $(this).data('tree-loaded', true);
       }
   });
   ```

---

## Testing Checklist

### Phase 1: Restructure
- [ ] All project cards are collapsed by default on page load
- [ ] Clicking card header expands/collapses the card
- [ ] Fixed header is visible even when card is collapsed
- [ ] Three sub-sections are present: Overall Usage, Services, Tree
- [ ] Services Breakdown is expanded by default when card opens
- [ ] Overall Usage and Tree are collapsed by default
- [ ] Clicking resource row still navigates to resource details page

### Phase 2: State Persistence
- [ ] Expand a project card, refresh page → card stays expanded
- [ ] Collapse a section, refresh page → section stays collapsed
- [ ] Navigate to resource details and back → state preserved
- [ ] Clear localStorage → defaults restored (cards collapsed)

### Phase 3: Project Tree
- [ ] Backend API `/api/project/<projcode>/tree` returns parent/children
- [ ] Tree section shows "Loading..." while fetching
- [ ] Tree displays parent project (if exists) with "Parent" badge
- [ ] Tree displays current project with "Current" badge
- [ ] Tree displays all children with correct titles
- [ ] Clicking parent/child navigates to that project card
- [ ] Projects without children show "No child projects" message
- [ ] Tree handles errors gracefully (project not found, network error)

---

## Data Source Reference

### Existing API Endpoint
`GET /dashboard/api/my-projects` returns:
```json
{
  "username": "benkirk",
  "projects": [
    {
      "projcode": "SCSG0001",
      "title": "CSG systems project",
      "active": true,
      "lead_username": "benkirk",
      "lead_name": "Benjamin Shelton Kirk",
      "total_allocated": 1000000.0,
      "total_used": 456789.12,
      "total_remaining": 543210.88,
      "percent_used": 45.68,
      "resources": [
        {
          "resource_name": "Derecho",
          "allocated": 500000.0,
          "used": 234567.89,
          "remaining": 265432.11,
          "percent_used": 46.91,
          "status": "Active",
          "start_date": "2024-01-01T00:00:00",
          "end_date": "2025-12-31T23:59:59"
        }
      ]
    }
  ],
  "total_projects": 5
}
```

### Project Hierarchy (from sam_search.py)
```bash
./python/sam_search.py project CESM0002 --verbose
# Shows:
# Child Projects: 28
#   - P93300007
#   - P93300012
#   - P93300041
#   ...
```

### ORM Methods Available (from CLAUDE.md)
```python
# In sam/projects/projects.py
project.get_children()  # Returns list of child projects
project.get_siblings()  # Returns sibling projects
project.parent          # Parent project (or None)
project.has_children    # Boolean, True if project has children
```

---

## Files to Modify

### Phase 1: Restructure
- `python/webui/templates/user/dashboard.html` (JavaScript changes only)

### Phase 2: State Persistence
- `python/webui/templates/user/dashboard.html` (Add localStorage utilities)

### Phase 3: Project Tree
- `python/webui/blueprints/user_dashboard.py` (New API endpoint)
- `python/webui/templates/user/dashboard.html` (Tree rendering logic)

---

## Estimated Effort

| Phase | Effort | Duration | Dependencies |
|-------|--------|----------|--------------|
| Phase 1: Restructure | Low | 2 hours | None |
| Phase 2: State Persistence | Medium | 3 hours | Phase 1 complete |
| Phase 3: Project Tree | High | 8 hours | Phase 1 complete, Backend ORM support (✅ exists) |

**Total Estimated Time:** 13 hours

---

## Future Enhancements (Out of Scope)

- Sortable columns in Overall Usage Table
- Export project data to CSV
- Filtering projects by status (Active/Inactive)
- Search projects by code or title
- Bulk expand/collapse all projects
- Project comparison view (side-by-side)
- Email notifications for allocation warnings
- Historical usage graphs in dashboard (currently only in resource details)

---

## Notes

- All changes maintain backward compatibility with existing API
- No database schema changes required
- Uses existing ORM methods from `sam/projects/projects.py`
- Follows patterns documented in `CLAUDE.md`
- Bootstrap 4 and jQuery already available in base template
- Responsive design maintained throughout

---

**Last Updated:** 2025-11-15
**Status:** Ready to implement when approved
