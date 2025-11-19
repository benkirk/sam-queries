# Dashboard Refactoring & Project Dashboard Implementation Plan

**Date**: 2025-11-18
**Branch**: `user_dashboard` (starting point)
**Author**: Ben Kirk
**Status**: Planning Phase

---

## Executive Summary

This document outlines a comprehensive plan to:
1. **Refactor the existing user dashboard** to eliminate maintainability issues
2. **Build a new project-centric dashboard** with member management and metadata editing
3. **Extract shared components** for reuse across both dashboards
4. **Establish sustainable architecture patterns** for future dashboard development

### Current State Assessment

The `user_dashboard` branch adds **2,950 lines of code** with significant technical debt:
- ❌ **950+ lines of inline JavaScript** in templates (untestable, unreusable)
- ❌ **500+ lines of inline CSS** scattered across 3 files
- ❌ **No frontend framework** - manual DOM manipulation
- ❌ **Code duplication** across templates (formatNumber, progress bars, etc.)
- ❌ **Large monolithic templates** (573 and 887 lines)

### Target State

- ✅ **Zero inline JavaScript/CSS** in templates
- ✅ **htmx-first architecture** with Mustache templating
- ✅ **All JSON APIs preserved** and leveraged
- ✅ **Reusable components** across dashboards
- ✅ **Testable, maintainable codebase**
- ✅ **~11-15 day implementation** (2-3 weeks)

---

## Architectural Decisions

### Framework Selection: htmx + Mustache

**Primary Framework**: htmx 1.9+ with `client-side-templates` extension
**Templating**: Mustache.js for client-side rendering
**Supplemental**: Alpine.js 3.x (minimal, only for complex client-side state like filters/search)
**Charts**: Removed entirely (or minimal implementation if later needed)
**APIs**: Keep all existing JSON APIs - no HTML-returning endpoints required

### Why htmx Over Alpine.js?

| Consideration | htmx | Alpine.js |
|--------------|------|-----------|
| **Chart.js dependency** | Charts dropped from requirements | Would enable charts easily |
| **Lines of JavaScript** | 50-100 lines total | 200-300 lines |
| **Build process** | Optional (just minification) | Recommended (Vite/webpack) |
| **Learning curve** | Very low (HTML attributes) | Medium (JS framework concepts) |
| **API compatibility** | Works perfectly with JSON APIs via client-side templates | Works with JSON APIs natively |
| **Maintainability** | Extremely simple, declarative | More powerful but more complex |
| **Server round-trips** | More frequent (but fast) | Less frequent (client-side heavy) |

**Decision**: htmx provides the best balance of simplicity and maintainability for this use case.

### How htmx Works with Existing JSON APIs

htmx can consume JSON APIs using the `client-side-templates` extension:

```html
<!-- htmx fetches JSON from existing API -->
<div hx-get="/api/v1/users/me/projects"
     hx-trigger="load"
     hx-ext="client-side-templates"
     mustache-array-template="project-list-template">
</div>

<!-- Mustache template renders the JSON data -->
<script id="project-list-template" type="x-tmpl-mustache">
{{#projects}}
  <div class="project-card">
    <h5>{{ title }} ({{ projcode }})</h5>
    <p>{{ description }}</p>
  </div>
{{/projects}}
</script>
```

**No API changes required** - all existing JSON endpoints work as-is.

---

## Implementation Phases

### Phase 1: Foundation - Extract & Organize (2-3 days)

#### 1.1 Create Static Asset Structure

Create organized directory structure for static assets:

```
python/webui/static/
├── css/
│   └── custom.css              # All custom styles (502 lines from templates)
├── js/
│   ├── common.js               # Shared utilities (formatNumber, formatDate, etc.)
│   ├── htmx.min.js             # htmx core (~14KB gzipped)
│   ├── mustache.min.js         # Client-side templating (~9KB gzipped)
│   └── alpine.min.js           # Optional, for filters/search (~15KB gzipped)
└── templates/                  # Client-side Mustache templates
    ├── project-card.mustache
    ├── member-card.mustache
    ├── resource-usage.mustache
    ├── charge-row.mustache
    └── allocation-summary.mustache
```

**Deliverables**:
- [ ] Create directory structure
- [ ] Download/vendor htmx, Mustache, Alpine (or use CDN)
- [ ] Create empty placeholder files

#### 1.2 Extract Shared Utilities to `common.js`

Move all duplicated JavaScript functions to a shared utilities file.

**File**: `python/webui/static/js/common.js`

```javascript
/**
 * Shared utilities for SAM dashboard applications
 */

// Number formatting
function formatNumber(num) {
    if (num === null || num === undefined) return 'N/A';
    return num.toLocaleString('en-US', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2
    });
}

// Date formatting
function formatDate(dateStr) {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

function formatDateTime(dateStr) {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Progress bar color logic
function getProgressColor(percentUsed) {
    if (percentUsed >= 90) return 'danger';
    if (percentUsed >= 75) return 'warning';
    if (percentUsed >= 50) return 'info';
    return 'success';
}

// Status badge mapping
function getStatusBadge(status) {
    const badgeMap = {
        'active': 'success',
        'inactive': 'secondary',
        'expired': 'danger',
        'pending': 'warning'
    };
    return badgeMap[status] || 'secondary';
}

// Role badge mapping
function getRoleBadge(role) {
    const badgeMap = {
        'lead': 'primary',
        'admin': 'info',
        'member': 'secondary'
    };
    return badgeMap[role] || 'secondary';
}

// Register as Mustache helpers
if (typeof Mustache !== 'undefined') {
    Mustache.Formatters = {
        formatNumber: formatNumber,
        formatDate: formatDate,
        formatDateTime: formatDateTime,
        getProgressColor: getProgressColor,
        getStatusBadge: getStatusBadge,
        getRoleBadge: getRoleBadge
    };
}

// Export for testing (if using modules)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        formatNumber,
        formatDate,
        formatDateTime,
        getProgressColor,
        getStatusBadge,
        getRoleBadge
    };
}
```

**Deliverables**:
- [ ] Create `common.js` with all shared functions
- [ ] Add JSDoc comments for all functions
- [ ] Register Mustache formatters
- [ ] Test formatters work correctly

#### 1.3 Create Custom Stylesheet

Extract all inline CSS to a single, organized stylesheet.

**File**: `python/webui/static/css/custom.css`

```css
/**
 * Custom styles for SAM Dashboard
 * Extends Bootstrap 4.6.2
 */

/* ===========================
   CSS Variables (Theme)
   =========================== */
:root {
    /* Primary colors (NCAR blue) */
    --primary-color: #0099CC;
    --primary-dark: #003366;
    --primary-light: #66C2E0;

    /* Status colors */
    --success-color: #28a745;
    --warning-color: #ffc107;
    --danger-color: #dc3545;
    --info-color: #17a2b8;

    /* Neutral colors */
    --gray-100: #f8f9fa;
    --gray-200: #e9ecef;
    --gray-300: #dee2e6;
    --gray-600: #6c757d;
    --gray-800: #343a40;

    /* Spacing */
    --spacing-xs: 0.25rem;
    --spacing-sm: 0.5rem;
    --spacing-md: 1rem;
    --spacing-lg: 1.5rem;
    --spacing-xl: 2rem;

    /* Border radius */
    --border-radius: 0.25rem;
    --border-radius-lg: 0.5rem;
}

/* ===========================
   Base Layout
   =========================== */
body {
    background-color: var(--gray-100);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}

.container-fluid {
    max-width: 1400px;
}

/* ===========================
   Navigation
   =========================== */
.navbar {
    background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-dark) 100%);
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.navbar-brand {
    font-weight: 600;
    color: white !important;
}

.navbar-text {
    color: rgba(255, 255, 255, 0.9) !important;
}

/* ===========================
   Cards
   =========================== */
.card {
    border: none;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12), 0 1px 2px rgba(0, 0, 0, 0.24);
    transition: box-shadow 0.3s ease;
}

.card:hover {
    box-shadow: 0 3px 6px rgba(0, 0, 0, 0.16), 0 3px 6px rgba(0, 0, 0, 0.23);
}

.card-header {
    background: linear-gradient(135deg, var(--gray-100) 0%, var(--gray-200) 100%);
    border-bottom: 2px solid var(--primary-color);
    font-weight: 600;
}

.card-header h5 {
    margin-bottom: 0;
    color: var(--gray-800);
}

/* ===========================
   Project Cards (Dashboard)
   =========================== */
.project-card {
    margin-bottom: var(--spacing-lg);
}

.project-status-badge {
    float: right;
    font-size: 0.85rem;
}

.project-meta {
    color: var(--gray-600);
    font-size: 0.9rem;
    margin-top: var(--spacing-sm);
}

/* ===========================
   Resource Usage Display
   =========================== */
.resource-usage {
    padding: var(--spacing-md);
    border-left: 3px solid var(--primary-color);
    margin-bottom: var(--spacing-md);
    background: white;
    border-radius: var(--border-radius);
}

.resource-usage .resource-name {
    font-weight: 600;
    color: var(--gray-800);
    margin-bottom: var(--spacing-sm);
}

.resource-usage .progress {
    height: 25px;
    margin: var(--spacing-sm) 0;
    border-radius: var(--border-radius);
}

.resource-usage .progress-bar {
    font-weight: 600;
    line-height: 25px;
}

.resource-usage .usage-details {
    display: flex;
    justify-content: space-between;
    font-size: 0.9rem;
    color: var(--gray-600);
}

/* ===========================
   Member Cards
   =========================== */
.member-card {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--spacing-md);
    background: white;
    border: 1px solid var(--gray-300);
    border-radius: var(--border-radius);
    margin-bottom: var(--spacing-sm);
    transition: background 0.2s ease;
}

.member-card:hover {
    background: var(--gray-100);
}

.member-info {
    flex-grow: 1;
}

.member-info strong {
    display: block;
    color: var(--gray-800);
}

.member-info .text-muted {
    font-size: 0.9rem;
}

.member-actions {
    display: flex;
    gap: var(--spacing-sm);
    align-items: center;
}

/* ===========================
   Tables
   =========================== */
.table-responsive {
    border-radius: var(--border-radius);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}

.table thead th {
    border-top: none;
    background: var(--gray-200);
    font-weight: 600;
    color: var(--gray-800);
}

.table-striped tbody tr:nth-of-type(odd) {
    background-color: rgba(0, 0, 0, 0.02);
}

.table-hover tbody tr:hover {
    background-color: rgba(0, 153, 204, 0.05);
}

/* ===========================
   Filters & Search
   =========================== */
.filters {
    background: white;
    padding: var(--spacing-lg);
    border-radius: var(--border-radius);
    margin-bottom: var(--spacing-lg);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}

.filters .form-group {
    margin-bottom: var(--spacing-md);
}

.filters .btn-group {
    display: flex;
    gap: var(--spacing-sm);
}

/* ===========================
   Loading States
   =========================== */
.loading-spinner {
    text-align: center;
    padding: var(--spacing-xl);
    color: var(--gray-600);
}

.loading-spinner::after {
    content: '';
    display: inline-block;
    width: 20px;
    height: 20px;
    border: 3px solid var(--gray-300);
    border-top-color: var(--primary-color);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin-left: var(--spacing-sm);
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

/* htmx loading indicator */
.htmx-request .htmx-indicator {
    display: inline-block;
}

.htmx-indicator {
    display: none;
}

/* ===========================
   Collapsible Sections
   =========================== */
.collapsible-trigger {
    cursor: pointer;
    user-select: none;
}

.collapsible-trigger::before {
    content: '▶';
    display: inline-block;
    margin-right: var(--spacing-sm);
    transition: transform 0.2s ease;
}

.collapsible-trigger[aria-expanded="true"]::before {
    transform: rotate(90deg);
}

/* ===========================
   Responsive Design
   =========================== */
@media (max-width: 768px) {
    .member-card {
        flex-direction: column;
        align-items: flex-start;
    }

    .member-actions {
        margin-top: var(--spacing-sm);
        width: 100%;
        justify-content: flex-end;
    }

    .filters .btn-group {
        flex-direction: column;
    }
}

/* ===========================
   Accessibility
   =========================== */
.sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}

/* Focus indicators */
a:focus,
button:focus,
input:focus,
select:focus,
textarea:focus {
    outline: 2px solid var(--primary-color);
    outline-offset: 2px;
}
```

**Deliverables**:
- [ ] Create `custom.css` with organized sections
- [ ] Use CSS variables for all colors and spacing
- [ ] Add comments for each section
- [ ] Test in multiple browsers

#### 1.4 Create Base Mustache Templates

Create reusable template partials in `python/webui/static/templates/`.

**File**: `project-card.mustache`
```mustache
{{!-- Project card component --}}
<div class="card project-card" id="project-{{ projcode }}">
    <div class="card-header">
        <h5>
            {{ title }}
            <span class="text-muted">({{ projcode }})</span>
            <span class="badge badge-{{ status_badge }} project-status-badge">
                {{ status }}
            </span>
        </h5>
        <div class="project-meta">
            <span><strong>Lead:</strong> {{ lead.full_name }}</span>
            {{#admin}}
            <span class="ml-3"><strong>Admin:</strong> {{ admin.full_name }}</span>
            {{/admin}}
        </div>
    </div>
    <div class="card-body">
        {{#has_resources}}
        <h6 class="mb-3">Resource Allocations</h6>
        {{#resources}}
            {{> resource-usage}}
        {{/resources}}
        {{/has_resources}}

        {{^has_resources}}
        <p class="text-muted">No active allocations</p>
        {{/has_resources}}

        <div class="mt-3">
            <a href="/projects/{{ projcode }}" class="btn btn-sm btn-primary">
                View Details
            </a>
        </div>
    </div>
</div>
```

**File**: `resource-usage.mustache`
```mustache
{{!-- Resource usage widget (partial) --}}
<div class="resource-usage">
    <div class="resource-name">{{ resource_name }}</div>

    <div class="progress">
        <div class="progress-bar bg-{{ percent_used | getProgressColor }}"
             role="progressbar"
             style="width: {{ percent_used }}%"
             aria-valuenow="{{ percent_used }}"
             aria-valuemin="0"
             aria-valuemax="100">
            {{ percent_used }}%
        </div>
    </div>

    <div class="usage-details">
        <span>
            <strong>Used:</strong> {{ used | formatNumber }}
        </span>
        <span>
            <strong>Allocated:</strong> {{ allocated | formatNumber }}
        </span>
        <span>
            <strong>Remaining:</strong> {{ remaining | formatNumber }}
        </span>
    </div>

    {{#end_date}}
    <div class="text-muted mt-2">
        <small>Expires: {{ end_date | formatDate }}</small>
    </div>
    {{/end_date}}
</div>
```

**File**: `member-card.mustache`
```mustache
{{!-- Member card component --}}
<div class="member-card" id="member-{{ user_id }}">
    <div class="member-info">
        <strong>{{ full_name }}</strong>
        <span class="text-muted">{{ username }}</span>
        <span class="badge badge-{{ role | getRoleBadge }} ml-2">{{ role }}</span>
    </div>

    {{#can_edit}}
    <div class="member-actions">
        <select class="form-control form-control-sm"
                hx-patch="/api/v1/projects/{{ projcode }}/members/{{ user_id }}/role"
                hx-ext="json-enc"
                hx-vals='js:{role: event.target.value}'
                hx-swap="none"
                aria-label="Change role for {{ full_name }}">
            <option value="member" {{#is_member}}selected{{/is_member}}>Member</option>
            <option value="admin" {{#is_admin}}selected{{/is_admin}}>Admin</option>
            <option value="lead" {{#is_lead}}selected{{/is_lead}}>Lead</option>
        </select>

        <button class="btn btn-sm btn-danger"
                hx-delete="/api/v1/projects/{{ projcode }}/members/{{ user_id }}"
                hx-confirm="Remove {{ full_name }} from {{ projcode }}?"
                hx-target="#member-{{ user_id }}"
                hx-swap="outerHTML swap:0.5s"
                aria-label="Remove {{ full_name }}">
            Remove
        </button>
    </div>
    {{/can_edit}}
</div>
```

**File**: `charge-row.mustache`
```mustache
{{!-- Charge table row --}}
<tr>
    <td>{{ activity_date | formatDate }}</td>
    <td>{{ charge_type }}</td>
    <td>{{ resource_name }}</td>
    <td class="text-right">{{ charges | formatNumber }}</td>
    {{#job_name}}
    <td><code>{{ job_name }}</code></td>
    {{/job_name}}
</tr>
```

**Deliverables**:
- [ ] Create all Mustache template files
- [ ] Use proper Mustache syntax ({{variable}}, {{#section}}, {{^inverted}})
- [ ] Add accessibility attributes (aria-label, role)
- [ ] Test templates with sample data

---

### Phase 2: Refactor User Dashboard (3-4 days)

#### 2.1 Refactor `templates/user/base.html`

**Current issues**:
- 305 lines of inline CSS
- Mixed concerns (layout + styling)

**Changes**:
1. Remove all `<style>` blocks
2. Link to `/static/css/custom.css`
3. Add htmx, Mustache, Alpine scripts
4. Keep only structure and Jinja logic

**File**: `python/webui/templates/user/base.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}SAM Dashboard{% endblock %}</title>

    <!-- Bootstrap CSS -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@4.6.2/dist/css/bootstrap.min.css">

    <!-- Font Awesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">

    <!-- Custom CSS -->
    <link rel="stylesheet" href="{{ url_for('static', filename='css/custom.css') }}">

    {% block extra_css %}{% endblock %}
</head>
<body>
    <!-- Navigation -->
    <nav class="navbar navbar-expand-lg navbar-dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="{{ url_for('user_dashboard.dashboard') }}">
                <i class="fas fa-chart-line"></i> SAM Dashboard
            </a>

            <div class="navbar-text ml-auto">
                {% if current_user.is_authenticated %}
                    <span class="mr-3">
                        <i class="fas fa-user"></i> {{ current_user.full_name }}
                    </span>
                    <a href="{{ url_for('auth.logout') }}" class="btn btn-sm btn-outline-light">
                        Logout
                    </a>
                {% endif %}
            </div>
        </div>
    </nav>

    <!-- Flash Messages -->
    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            <div class="container mt-3">
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                        {{ message }}
                        <button type="button" class="close" data-dismiss="alert" aria-label="Close">
                            <span aria-hidden="true">&times;</span>
                        </button>
                    </div>
                {% endfor %}
            </div>
        {% endif %}
    {% endwith %}

    <!-- Main Content -->
    <main class="container-fluid mt-4">
        {% block content %}{% endblock %}
    </main>

    <!-- Scripts -->
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@4.6.2/dist/js/bootstrap.bundle.min.js"></script>

    <!-- htmx -->
    <script src="{{ url_for('static', filename='js/htmx.min.js') }}"></script>

    <!-- Mustache.js -->
    <script src="{{ url_for('static', filename='js/mustache.min.js') }}"></script>

    <!-- Alpine.js (for filters/search) -->
    <script src="{{ url_for('static', filename='js/alpine.min.js') }}" defer></script>

    <!-- Common utilities -->
    <script src="{{ url_for('static', filename='js/common.js') }}"></script>

    {% block extra_js %}{% endblock %}
</body>
</html>
```

**Deliverables**:
- [ ] Remove all inline CSS
- [ ] Link to custom.css
- [ ] Add htmx, Mustache, Alpine scripts
- [ ] Keep navigation and flash message logic
- [ ] Test page loads correctly

#### 2.2 Refactor `templates/user/dashboard.html`

**Current**: 573 lines (399 JS, 71 CSS, 103 HTML)
**Target**: ~150 lines (minimal JS, no CSS, structured HTML)

**File**: `python/webui/templates/user/dashboard.html`

```html
{% extends "user/base.html" %}

{% block title %}My Projects - SAM Dashboard{% endblock %}

{% block content %}
<div class="container">
    <div class="row mb-4">
        <div class="col-md-8">
            <h2>My Projects</h2>
            <p class="text-muted">View your project allocations and resource usage</p>
        </div>
        <div class="col-md-4">
            <!-- Search/Filter (Alpine.js) -->
            <div x-data="{ search: '' }">
                <input type="text"
                       class="form-control"
                       x-model="search"
                       placeholder="Filter projects..."
                       @input.debounce.300ms="filterProjects($event.target.value)">
            </div>
        </div>
    </div>

    <!-- Projects Container (htmx loads via API) -->
    <div id="projects-container"
         hx-get="/api/v1/users/me/projects?format=dashboard"
         hx-trigger="load"
         hx-ext="client-side-templates"
         mustache-array-template="project-card-template"
         hx-indicator="#loading-spinner">

        <div id="loading-spinner" class="loading-spinner">
            Loading your projects...
        </div>
    </div>
</div>

<!-- Mustache Template for Project Cards -->
<script id="project-card-template" type="x-tmpl-mustache">
{{#projects}}
<div class="card project-card" id="project-{{ projcode }}">
    <div class="card-header">
        <h5>
            {{ title }}
            <span class="text-muted">({{ projcode }})</span>
            <span class="badge badge-{{ status | getStatusBadge }} project-status-badge">
                {{ status }}
            </span>
        </h5>
        <div class="project-meta">
            <span><strong>Lead:</strong> {{ lead.full_name }}</span>
            {{#admin}}
            <span class="ml-3"><strong>Admin:</strong> {{ admin.full_name }}</span>
            {{/admin}}
        </div>
    </div>
    <div class="card-body">
        {{#has_resources}}
        <h6 class="mb-3">Resource Allocations</h6>
        {{#resources}}
        <div class="resource-usage">
            <div class="resource-name">{{ resource_name }}</div>

            <div class="progress">
                <div class="progress-bar bg-{{ percent_used | getProgressColor }}"
                     role="progressbar"
                     style="width: {{ percent_used }}%"
                     aria-valuenow="{{ percent_used }}"
                     aria-valuemin="0"
                     aria-valuemax="100">
                    {{ percent_used }}%
                </div>
            </div>

            <div class="usage-details">
                <span><strong>Used:</strong> {{ used | formatNumber }}</span>
                <span><strong>Allocated:</strong> {{ allocated | formatNumber }}</span>
                <span><strong>Remaining:</strong> {{ remaining | formatNumber }}</span>
            </div>
        </div>
        {{/resources}}
        {{/has_resources}}

        {{^has_resources}}
        <p class="text-muted">No active allocations</p>
        {{/has_resources}}

        <!-- Collapsible Project Tree (lazy loaded) -->
        <div class="mt-3">
            <button class="btn btn-sm btn-outline-secondary collapsible-trigger"
                    data-toggle="collapse"
                    data-target="#tree-{{ projcode }}"
                    aria-expanded="false"
                    aria-controls="tree-{{ projcode }}"
                    hx-get="/projects/{{ projcode }}/tree"
                    hx-target="#tree-{{ projcode }}-content"
                    hx-trigger="click once">
                View Project Hierarchy
            </button>

            <div class="collapse mt-2" id="tree-{{ projcode }}">
                <div id="tree-{{ projcode }}-content" class="border rounded p-2">
                    <div class="loading-spinner">Loading tree...</div>
                </div>
            </div>
        </div>

        <div class="mt-3">
            <a href="/projects/{{ projcode }}" class="btn btn-sm btn-primary">
                View Project Details
            </a>
        </div>
    </div>
</div>
{{/projects}}

{{^projects}}
<div class="alert alert-info">
    <i class="fas fa-info-circle"></i>
    You are not currently a member of any projects.
</div>
{{/projects}}
</script>

{% endblock %}

{% block extra_js %}
<script>
// Simple client-side filtering (Alpine.js helper)
function filterProjects(query) {
    const cards = document.querySelectorAll('.project-card');
    const searchTerm = query.toLowerCase();

    cards.forEach(card => {
        const text = card.textContent.toLowerCase();
        card.style.display = text.includes(searchTerm) ? 'block' : 'none';
    });
}
</script>
{% endblock %}
```

**Key improvements**:
- ❌ 399 lines of inline JS → ✅ ~20 lines
- ❌ 71 lines of inline CSS → ✅ 0 lines (in custom.css)
- ✅ Declarative htmx attributes (no manual fetch)
- ✅ Mustache template (readable, maintainable)
- ✅ Lazy loading with `hx-trigger="click once"`
- ✅ Loading indicators

**Deliverables**:
- [ ] Rewrite dashboard.html using htmx + Mustache
- [ ] Remove all inline JavaScript
- [ ] Remove all inline CSS
- [ ] Test project loading works
- [ ] Test search filtering works
- [ ] Test lazy-loaded tree works

#### 2.3 Refactor `templates/user/resource_details.html`

**Current**: 887 lines (552 JS, 126 CSS, 209 HTML), makes 4 API calls
**Target**: ~200 lines, single consolidated API call

**Backend change first**: Create consolidated endpoint

**File**: `python/webui/api/v1/projects.py`

```python
@api_bp.route('/projects/<projcode>/dashboard-data')
@login_required
def project_dashboard_data(projcode):
    """
    Consolidated endpoint for project dashboard.
    Returns all data needed for resource details page in single call.
    """
    project = Project.get_by_projcode(db.session, projcode)
    if not project:
        abort(404)

    # Access check
    if not current_user.can_access_project(project):
        abort(403)

    # Get query parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    resource_id = request.args.get('resource_id')

    # Get allocations with usage
    allocations = project.active_allocations
    allocations_data = AllocationWithUsageSchema(many=True).dump(allocations)

    # Get recent charges
    charges_query = db.session.query(ChargeDetail).join(Account).filter(
        Account.project_id == project.project_id
    )

    if start_date:
        charges_query = charges_query.filter(ChargeDetail.activity_date >= start_date)
    if end_date:
        charges_query = charges_query.filter(ChargeDetail.activity_date <= end_date)
    if resource_id:
        charges_query = charges_query.filter(Account.resource_id == resource_id)

    charges = charges_query.order_by(ChargeDetail.activity_date.desc()).limit(100).all()

    # Get allocation change history
    changes = get_allocation_changes(project, limit=20)

    return jsonify({
        'project': ProjectSummarySchema().dump(project),
        'allocations': allocations_data,
        'charges': ChargeDetailSchema(many=True).dump(charges),
        'allocation_changes': AllocationChangeSchema(many=True).dump(changes),
        'filters': {
            'start_date': start_date,
            'end_date': end_date,
            'resource_id': resource_id
        }
    })
```

**Template**: `python/webui/templates/user/resource_details.html`

```html
{% extends "user/base.html" %}

{% block title %}{{ project.title }} - Resource Details{% endblock %}

{% block content %}
<div class="container">
    <!-- Header -->
    <div class="row mb-4">
        <div class="col">
            <h2>{{ project.title }} ({{ project.projcode }})</h2>
            <p class="text-muted">{{ project.description }}</p>
        </div>
    </div>

    <!-- Filters (Alpine.js for state management) -->
    <div class="filters" x-data="dashboardFilters()">
        <div class="row">
            <div class="col-md-3">
                <label>Start Date</label>
                <input type="date"
                       class="form-control"
                       x-model="startDate"
                       @change="updateDashboard()">
            </div>
            <div class="col-md-3">
                <label>End Date</label>
                <input type="date"
                       class="form-control"
                       x-model="endDate"
                       @change="updateDashboard()">
            </div>
            <div class="col-md-3">
                <label>Resource</label>
                <select class="form-control"
                        x-model="resourceId"
                        @change="updateDashboard()">
                    <option value="">All Resources</option>
                    {% for resource in resources %}
                    <option value="{{ resource.resource_id }}">{{ resource.resource_name }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-3 d-flex align-items-end">
                <button class="btn btn-secondary" @click="resetFilters()">
                    Reset Filters
                </button>
            </div>
        </div>
    </div>

    <!-- Dashboard Data (loaded via htmx) -->
    <div id="dashboard-content"
         hx-get="/api/v1/projects/{{ project.projcode }}/dashboard-data"
         hx-trigger="load"
         hx-ext="client-side-templates"
         mustache-template="dashboard-template"
         hx-indicator="#loading">
        <div id="loading" class="loading-spinner">Loading dashboard...</div>
    </div>
</div>

<!-- Dashboard Template -->
<script id="dashboard-template" type="x-tmpl-mustache">
<div class="row">
    <!-- Left Column: Allocations -->
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">
                <h5>Current Allocations</h5>
            </div>
            <div class="card-body">
                {{#allocations}}
                <div class="resource-usage">
                    <div class="resource-name">{{ resource_name }}</div>

                    <div class="progress">
                        <div class="progress-bar bg-{{ percent_used | getProgressColor }}"
                             style="width: {{ percent_used }}%">
                            {{ percent_used }}%
                        </div>
                    </div>

                    <div class="usage-details">
                        <span><strong>Used:</strong> {{ used | formatNumber }}</span>
                        <span><strong>Allocated:</strong> {{ allocated | formatNumber }}</span>
                        <span><strong>Remaining:</strong> {{ remaining | formatNumber }}</span>
                    </div>

                    {{#end_date}}
                    <div class="text-muted mt-1">
                        <small><i class="fas fa-calendar"></i> Expires: {{ end_date | formatDate }}</small>
                    </div>
                    {{/end_date}}
                </div>
                {{/allocations}}

                {{^allocations}}
                <p class="text-muted">No active allocations</p>
                {{/allocations}}
            </div>
        </div>

        <!-- Allocation Changes -->
        <div class="card mt-3">
            <div class="card-header">
                <h5>Recent Allocation Changes</h5>
            </div>
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-sm">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Resource</th>
                                <th>Change</th>
                                <th>New Balance</th>
                            </tr>
                        </thead>
                        <tbody>
                            {{#allocation_changes}}
                            <tr>
                                <td>{{ date | formatDate }}</td>
                                <td>{{ resource_name }}</td>
                                <td class="{{#is_positive}}text-success{{/is_positive}}{{^is_positive}}text-danger{{/is_positive}}">
                                    {{#is_positive}}+{{/is_positive}}{{ amount | formatNumber }}
                                </td>
                                <td>{{ new_balance | formatNumber }}</td>
                            </tr>
                            {{/allocation_changes}}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <!-- Right Column: Charges -->
    <div class="col-md-6">
        <div class="card">
            <div class="card-header">
                <h5>
                    Recent Charges
                    <button class="btn btn-sm btn-outline-primary float-right"
                            hx-get="/api/v1/projects/{{ project.projcode }}/charges/export?start={{ filters.start_date }}&end={{ filters.end_date }}"
                            hx-headers='{"Accept": "text/csv"}'>
                        <i class="fas fa-download"></i> Export CSV
                    </button>
                </h5>
            </div>
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-sm table-striped table-hover">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Type</th>
                                <th>Resource</th>
                                <th class="text-right">Charges</th>
                            </tr>
                        </thead>
                        <tbody>
                            {{#charges}}
                            <tr>
                                <td>{{ activity_date | formatDate }}</td>
                                <td>
                                    <span class="badge badge-secondary">{{ charge_type }}</span>
                                </td>
                                <td>{{ resource_name }}</td>
                                <td class="text-right">{{ charges | formatNumber }}</td>
                            </tr>
                            {{/charges}}
                        </tbody>
                    </table>
                </div>

                {{^charges}}
                <p class="text-muted">No charges in selected date range</p>
                {{/charges}}
            </div>
        </div>
    </div>
</div>
</script>

{% endblock %}

{% block extra_js %}
<script>
// Alpine.js component for filter management
function dashboardFilters() {
    return {
        startDate: '{{ default_start_date }}',
        endDate: '{{ default_end_date }}',
        resourceId: '',

        updateDashboard() {
            const params = new URLSearchParams({
                start_date: this.startDate,
                end_date: this.endDate,
                resource_id: this.resourceId
            });

            const url = `/api/v1/projects/{{ project.projcode }}/dashboard-data?${params}`;
            htmx.ajax('GET', url, '#dashboard-content');
        },

        resetFilters() {
            this.startDate = '{{ default_start_date }}';
            this.endDate = '{{ default_end_date }}';
            this.resourceId = '';
            this.updateDashboard();
        }
    };
}
</script>
{% endblock %}
```

**Key improvements**:
- ❌ 4 separate API calls → ✅ 1 consolidated call
- ❌ 552 lines of JS → ✅ ~30 lines
- ❌ 126 lines of CSS → ✅ 0 lines
- ✅ Charts removed (can add back later if needed)
- ✅ All data in single API response
- ✅ Clean filter management with Alpine.js

**Deliverables**:
- [ ] Create `/api/v1/projects/<projcode>/dashboard-data` endpoint
- [ ] Rewrite resource_details.html with htmx + Mustache
- [ ] Remove all inline JS/CSS
- [ ] Test filters work correctly
- [ ] Test CSV export works
- [ ] Verify performance improvement (4 calls → 1 call)

#### 2.4 Update Backend Schemas

Ensure proper schemas exist for dashboard data.

**File**: `python/webui/schemas/project.py`

Add `DashboardProjectSchema` if not exists:

```python
class DashboardProjectSchema(SQLAlchemyAutoSchema):
    """
    Schema for project data in dashboard format.
    Optimized for dashboard display with computed fields.
    """
    class Meta:
        model = Project
        include_relationships = True
        load_instance = True

    # Basic fields
    projcode = fields.String()
    title = fields.String()
    description = fields.String()
    status = fields.String()

    # Nested relationships (summary versions)
    lead = fields.Nested('UserSummarySchema')
    admin = fields.Nested('UserSummarySchema', allow_none=True)

    # Computed fields
    status_badge = fields.Method('get_status_badge')
    has_resources = fields.Method('get_has_resources')

    def get_status_badge(self, obj):
        """Return Bootstrap badge class for status."""
        if obj.active:
            return 'success'
        return 'secondary'

    def get_has_resources(self, obj):
        """Check if project has any active allocations."""
        return len(obj.active_allocations) > 0
```

**Deliverables**:
- [ ] Verify DashboardProjectSchema exists
- [ ] Add any missing computed fields
- [ ] Test schema serialization

---

### Phase 3: Build Project Dashboard (3-4 days)

#### 3.1 Create Project Dashboard Blueprint

**File**: `python/webui/blueprints/project_dashboard.py`

```python
"""
Project-centric dashboard blueprint.
Allows project members to view project details.
Allows leads/admins to manage members and edit metadata.
"""

from flask import Blueprint, render_template, abort, jsonify, request
from flask_login import login_required, current_user
from sqlalchemy.orm import Session

from sam.models import Project, User
from sam.session import get_db_session
from webui.schemas import ProjectSchema, UserListSchema

project_bp = Blueprint('project_dashboard', __name__, url_prefix='/projects')

def _user_can_access_project(user, project):
    """Check if user has access to view project."""
    return user in project.users

def _user_can_edit_project(user, project):
    """Check if user can edit project (lead or admin)."""
    # Implement based on your role system
    # Example: check if user is lead or admin
    return (project.lead_id == user.user_id or
            project.admin_id == user.user_id)

@project_bp.route('/<projcode>')
@login_required
def dashboard(projcode):
    """Project overview dashboard."""
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project:
        abort(404, description=f"Project {projcode} not found")

    if not _user_can_access_project(current_user, project):
        abort(403, description="You do not have access to this project")

    can_edit = _user_can_edit_project(current_user, project)

    # Get default date range (last 90 days)
    from datetime import datetime, timedelta
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)

    return render_template('project/dashboard.html',
                          project=project,
                          can_edit=can_edit,
                          default_start_date=start_date.strftime('%Y-%m-%d'),
                          default_end_date=end_date.strftime('%Y-%m-%d'))

@project_bp.route('/<projcode>/members')
@login_required
def members(projcode):
    """Member management page."""
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project:
        abort(404)

    if not _user_can_access_project(current_user, project):
        abort(403)

    can_edit = _user_can_edit_project(current_user, project)

    return render_template('project/members.html',
                          project=project,
                          can_edit=can_edit)

@project_bp.route('/<projcode>/settings')
@login_required
def settings(projcode):
    """Project settings/metadata editing page."""
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project:
        abort(404)

    if not _user_can_edit_project(current_user, project):
        abort(403, description="Only project leads/admins can edit settings")

    return render_template('project/settings.html',
                          project=project,
                          can_edit=True)

@project_bp.route('/<projcode>/tree')
@login_required
def project_tree(projcode):
    """
    Lazy-loaded project tree HTML fragment.
    Called by htmx when user expands tree section.
    """
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project or not _user_can_access_project(current_user, project):
        abort(403)

    # Return HTML fragment (not full page)
    return render_template('project/partials/tree.html', project=project)
```

**Register blueprint** in `python/webui/run.py`:

```python
from webui.blueprints.project_dashboard import project_bp

# ... existing code ...

app.register_blueprint(project_bp)
```

**Deliverables**:
- [ ] Create `project_dashboard.py` blueprint
- [ ] Implement access control helpers
- [ ] Add routes for dashboard, members, settings
- [ ] Register blueprint in `run.py`
- [ ] Test routes return 404/403 correctly

#### 3.2 Create Project Dashboard Templates

**Base template**: `python/webui/templates/project/base.html`

```html
{% extends "user/base.html" %}

{% block content %}
<div class="container">
    <!-- Project Header (shown on all project pages) -->
    <div class="row mb-4">
        <div class="col">
            <h2>
                {{ project.title }}
                <span class="text-muted">({{ project.projcode }})</span>
            </h2>

            <!-- Project Navigation Tabs -->
            <ul class="nav nav-tabs mt-3">
                <li class="nav-item">
                    <a class="nav-link {{ 'active' if request.endpoint == 'project_dashboard.dashboard' }}"
                       href="{{ url_for('project_dashboard.dashboard', projcode=project.projcode) }}">
                        <i class="fas fa-chart-bar"></i> Overview
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link {{ 'active' if request.endpoint == 'project_dashboard.members' }}"
                       href="{{ url_for('project_dashboard.members', projcode=project.projcode) }}">
                        <i class="fas fa-users"></i> Members
                    </a>
                </li>
                {% if can_edit %}
                <li class="nav-item">
                    <a class="nav-link {{ 'active' if request.endpoint == 'project_dashboard.settings' }}"
                       href="{{ url_for('project_dashboard.settings', projcode=project.projcode) }}">
                        <i class="fas fa-cog"></i> Settings
                    </a>
                </li>
                {% endif %}
            </ul>
        </div>
    </div>

    <!-- Page-specific content -->
    {% block project_content %}{% endblock %}
</div>
{% endblock %}
```

**Dashboard page**: `python/webui/templates/project/dashboard.html`

```html
{% extends "project/base.html" %}

{% block title %}{{ project.title }} - Dashboard{% endblock %}

{% block project_content %}
<!-- This is nearly identical to user/resource_details.html -->
<!-- We can reuse the same filters and dashboard template -->

<!-- Filters -->
<div class="filters" x-data="dashboardFilters()">
    <div class="row">
        <div class="col-md-3">
            <label>Start Date</label>
            <input type="date" class="form-control" x-model="startDate" @change="updateDashboard()">
        </div>
        <div class="col-md-3">
            <label>End Date</label>
            <input type="date" class="form-control" x-model="endDate" @change="updateDashboard()">
        </div>
        <div class="col-md-3 d-flex align-items-end">
            <button class="btn btn-secondary" @click="resetFilters()">Reset</button>
        </div>
    </div>
</div>

<!-- Dashboard Data -->
<div id="dashboard-content"
     hx-get="/api/v1/projects/{{ project.projcode }}/dashboard-data"
     hx-trigger="load"
     hx-ext="client-side-templates"
     mustache-template="dashboard-template"
     hx-indicator="#loading">
    <div id="loading" class="loading-spinner">Loading...</div>
</div>

<!-- Reuse the same Mustache template from user/resource_details.html -->
<script id="dashboard-template" type="x-tmpl-mustache">
<!-- ... same as Phase 2.3 ... -->
</script>

{% endblock %}

{% block extra_js %}
<script>
function dashboardFilters() {
    return {
        startDate: '{{ default_start_date }}',
        endDate: '{{ default_end_date }}',

        updateDashboard() {
            const params = new URLSearchParams({
                start_date: this.startDate,
                end_date: this.endDate
            });
            const url = `/api/v1/projects/{{ project.projcode }}/dashboard-data?${params}`;
            htmx.ajax('GET', url, '#dashboard-content');
        },

        resetFilters() {
            this.startDate = '{{ default_start_date }}';
            this.endDate = '{{ default_end_date }}';
            this.updateDashboard();
        }
    };
}
</script>
{% endblock %}
```

**Members page**: `python/webui/templates/project/members.html`

```html
{% extends "project/base.html" %}

{% block title %}{{ project.title }} - Members{% endblock %}

{% block project_content %}
<div class="row">
    <div class="col-md-8">
        <div class="card">
            <div class="card-header">
                <h5>
                    Project Members
                    <span class="badge badge-secondary" id="member-count">
                        <!-- Updated dynamically -->
                    </span>
                </h5>
            </div>
            <div class="card-body">
                <!-- Search (Alpine.js) -->
                <div x-data="{ search: '' }" class="mb-3">
                    <input type="text"
                           class="form-control"
                           x-model="search"
                           placeholder="Search members..."
                           @input="filterMembers($event.target.value)">
                </div>

                <!-- Member List (htmx + Mustache) -->
                <div id="member-list"
                     hx-get="/api/v1/projects/{{ project.projcode }}/members"
                     hx-trigger="load, refreshMembers from:body"
                     hx-ext="client-side-templates"
                     mustache-array-template="member-template"
                     hx-indicator="#loading-members">
                    <div id="loading-members" class="loading-spinner">
                        Loading members...
                    </div>
                </div>
            </div>
        </div>
    </div>

    {% if can_edit %}
    <div class="col-md-4">
        <div class="card">
            <div class="card-header">
                <h5>Add Member</h5>
            </div>
            <div class="card-body">
                <form hx-post="/api/v1/projects/{{ project.projcode }}/members"
                      hx-ext="json-enc"
                      hx-on::after-request="if(event.detail.successful) this.reset(); htmx.trigger('body', 'refreshMembers')">

                    <div class="form-group">
                        <label for="username">Username</label>
                        <input type="text"
                               class="form-control"
                               name="username"
                               id="username"
                               required
                               placeholder="e.g., benkirk">
                    </div>

                    <div class="form-group">
                        <label for="role">Role</label>
                        <select class="form-control" name="role" id="role">
                            <option value="member">Member</option>
                            <option value="admin">Admin</option>
                            <option value="lead">Lead</option>
                        </select>
                    </div>

                    <button type="submit" class="btn btn-primary btn-block">
                        <i class="fas fa-user-plus"></i> Add Member
                    </button>
                </form>

                <!-- Error display -->
                <div id="add-member-error" class="alert alert-danger mt-3" style="display: none;"></div>
            </div>
        </div>
    </div>
    {% endif %}
</div>

<!-- Mustache Template for Members -->
<script id="member-template" type="x-tmpl-mustache">
{{#.}}
<div class="member-card" id="member-{{ user_id }}">
    <div class="member-info">
        <strong>{{ full_name }}</strong>
        <span class="text-muted">{{ username }}</span>
        <span class="badge badge-{{ role | getRoleBadge }} ml-2">{{ role }}</span>

        {{#primary_email}}
        <div class="text-muted small">
            <i class="fas fa-envelope"></i> {{ primary_email }}
        </div>
        {{/primary_email}}
    </div>

    {{#can_edit}}
    <div class="member-actions">
        <select class="form-control form-control-sm"
                hx-patch="/api/v1/projects/{{ projcode }}/members/{{ user_id }}/role"
                hx-ext="json-enc"
                hx-vals='js:{role: event.target.value}'
                hx-swap="none"
                aria-label="Change role for {{ full_name }}">
            <option value="member" {{#is_member}}selected{{/is_member}}>Member</option>
            <option value="admin" {{#is_admin}}selected{{/is_admin}}>Admin</option>
            <option value="lead" {{#is_lead}}selected{{/is_lead}}>Lead</option>
        </select>

        <button class="btn btn-sm btn-danger"
                hx-delete="/api/v1/projects/{{ projcode }}/members/{{ user_id }}"
                hx-confirm="Remove {{ full_name }} from {{ projcode }}?"
                hx-target="#member-{{ user_id }}"
                hx-swap="outerHTML swap:0.5s"
                hx-on::after-request="htmx.trigger('body', 'refreshMembers')"
                aria-label="Remove {{ full_name }}">
            <i class="fas fa-trash"></i>
        </button>
    </div>
    {{/can_edit}}
</div>

{{^.}}
<p class="text-muted">No members found</p>
{{/.}}
</script>

{% endblock %}

{% block extra_js %}
<script>
function filterMembers(query) {
    const cards = document.querySelectorAll('.member-card');
    const searchTerm = query.toLowerCase();

    cards.forEach(card => {
        const text = card.textContent.toLowerCase();
        card.style.display = text.includes(searchTerm) ? 'block' : 'none';
    });
}

// Update member count badge
document.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target.id === 'member-list') {
        const count = document.querySelectorAll('.member-card').length;
        document.getElementById('member-count').textContent = count;
    }
});
</script>
{% endblock %}
```

**Settings page**: `python/webui/templates/project/settings.html`

```html
{% extends "project/base.html" %}

{% block title %}{{ project.title }} - Settings{% endblock %}

{% block project_content %}
<div class="row">
    <div class="col-md-8">
        <div class="card">
            <div class="card-header">
                <h5>Project Settings</h5>
            </div>
            <div class="card-body">
                <!-- Alpine.js manages form state -->
                <div x-data="projectSettings()" x-init="loadProject()">
                    <form @submit.prevent="saveChanges">
                        <div class="form-group">
                            <label for="title">Project Title</label>
                            <input type="text"
                                   class="form-control"
                                   id="title"
                                   x-model="project.title"
                                   required>
                        </div>

                        <div class="form-group">
                            <label for="description">Description</label>
                            <textarea class="form-control"
                                      id="description"
                                      x-model="project.description"
                                      rows="4"></textarea>
                        </div>

                        <div class="form-group">
                            <label for="keywords">Keywords</label>
                            <input type="text"
                                   class="form-control"
                                   id="keywords"
                                   x-model="project.keywords"
                                   placeholder="climate, modeling, analysis">
                            <small class="form-text text-muted">
                                Comma-separated keywords
                            </small>
                        </div>

                        <div class="form-group">
                            <label>Project Code (read-only)</label>
                            <input type="text"
                                   class="form-control"
                                   :value="project.projcode"
                                   disabled>
                        </div>

                        <!-- Save button (only if changes exist) -->
                        <div x-show="hasChanges" x-transition>
                            <button type="submit" class="btn btn-primary">
                                <i class="fas fa-save"></i> Save Changes
                            </button>
                            <button type="button"
                                    class="btn btn-secondary ml-2"
                                    @click="loadProject()">
                                Cancel
                            </button>
                        </div>

                        <!-- Success/Error messages -->
                        <div x-show="saveSuccess"
                             x-transition
                             class="alert alert-success mt-3">
                            <i class="fas fa-check-circle"></i> Changes saved successfully!
                        </div>

                        <div x-show="saveError"
                             x-transition
                             class="alert alert-danger mt-3">
                            <i class="fas fa-exclamation-circle"></i> <span x-text="errorMessage"></span>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>

    <div class="col-md-4">
        <div class="card">
            <div class="card-header">
                <h5>Project Information</h5>
            </div>
            <div class="card-body">
                <dl>
                    <dt>Project Code</dt>
                    <dd>{{ project.projcode }}</dd>

                    <dt>Lead</dt>
                    <dd>{{ project.lead.full_name }}</dd>

                    {% if project.admin %}
                    <dt>Admin</dt>
                    <dd>{{ project.admin.full_name }}</dd>
                    {% endif %}

                    <dt>Status</dt>
                    <dd>
                        <span class="badge badge-{{ 'success' if project.active else 'secondary' }}">
                            {{ 'Active' if project.active else 'Inactive' }}
                        </span>
                    </dd>

                    <dt>Created</dt>
                    <dd>{{ project.creation_time.strftime('%Y-%m-%d') if project.creation_time else 'N/A' }}</dd>
                </dl>
            </div>
        </div>
    </div>
</div>

{% endblock %}

{% block extra_js %}
<script>
function projectSettings() {
    return {
        project: {},
        originalProject: {},
        saveSuccess: false,
        saveError: false,
        errorMessage: '',

        async loadProject() {
            try {
                const response = await fetch('/api/v1/projects/{{ project.projcode }}');
                if (!response.ok) throw new Error('Failed to load project');

                this.project = await response.json();
                this.originalProject = JSON.parse(JSON.stringify(this.project));
                this.saveSuccess = false;
                this.saveError = false;
            } catch (error) {
                console.error('Error loading project:', error);
            }
        },

        get hasChanges() {
            return JSON.stringify(this.project) !== JSON.stringify(this.originalProject);
        },

        async saveChanges() {
            this.saveSuccess = false;
            this.saveError = false;

            try {
                const response = await fetch('/api/v1/projects/{{ project.projcode }}', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: this.project.title,
                        description: this.project.description,
                        keywords: this.project.keywords
                    })
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.message || 'Failed to save changes');
                }

                this.originalProject = JSON.parse(JSON.stringify(this.project));
                this.saveSuccess = true;

                // Hide success message after 3 seconds
                setTimeout(() => { this.saveSuccess = false; }, 3000);

            } catch (error) {
                console.error('Error saving project:', error);
                this.saveError = true;
                this.errorMessage = error.message;
            }
        }
    };
}
</script>
{% endblock %}
```

**Deliverables**:
- [ ] Create `project/base.html` with tabbed navigation
- [ ] Create `project/dashboard.html` (reuses components)
- [ ] Create `project/members.html` with add/remove functionality
- [ ] Create `project/settings.html` with form validation
- [ ] Test all templates render correctly
- [ ] Test navigation between tabs works

#### 3.3 Create API Endpoints for Member Management

**File**: `python/webui/api/v1/projects.py` (add to existing file)

```python
from flask import request, jsonify, abort
from flask_login import login_required, current_user
from webui.schemas import ProjectMemberSchema, UserListSchema

@api_bp.route('/projects/<projcode>/members', methods=['GET'])
@login_required
def get_project_members(projcode):
    """
    Get all members of a project.
    Returns: JSON array of users with role information
    """
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project:
        abort(404, description=f"Project {projcode} not found")

    if not _user_can_access_project(current_user, project):
        abort(403, description="Access denied")

    # Get members with role information
    members = project.users  # SQLAlchemy relationship

    # Determine if current user can edit
    can_edit = _user_can_edit_project(current_user, project)

    # Add role and edit permission to each member
    members_data = []
    for member in members:
        member_dict = UserListSchema().dump(member)
        member_dict['role'] = get_user_role_in_project(member, project)
        member_dict['can_edit'] = can_edit
        member_dict['projcode'] = projcode

        # Add boolean flags for role selection
        member_dict['is_member'] = member_dict['role'] == 'member'
        member_dict['is_admin'] = member_dict['role'] == 'admin'
        member_dict['is_lead'] = member_dict['role'] == 'lead'

        members_data.append(member_dict)

    return jsonify(members_data)

@api_bp.route('/projects/<projcode>/members', methods=['POST'])
@login_required
def add_project_member(projcode):
    """
    Add a user to the project.
    Request body: {"username": "benkirk", "role": "member"}
    """
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project:
        abort(404)

    if not _user_can_edit_project(current_user, project):
        abort(403, description="Only leads/admins can add members")

    data = request.get_json()
    username = data.get('username')
    role = data.get('role', 'member')

    if not username:
        return jsonify({'error': 'Username is required'}), 400

    # Find user
    user = User.get_by_username(db, username)
    if not user:
        return jsonify({'error': f'User {username} not found'}), 404

    # Check if already a member
    if user in project.users:
        return jsonify({'error': f'{username} is already a member'}), 400

    try:
        # Add to project (implement this method in your Project model)
        project.add_member(user, role=role)
        db.commit()

        # Return the new member
        member_data = UserListSchema().dump(user)
        member_data['role'] = role
        member_data['can_edit'] = True
        member_data['projcode'] = projcode

        return jsonify(member_data), 201

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500

@api_bp.route('/projects/<projcode>/members/<int:user_id>', methods=['DELETE'])
@login_required
def remove_project_member(projcode, user_id):
    """
    Remove a user from the project.
    """
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project:
        abort(404)

    if not _user_can_edit_project(current_user, project):
        abort(403)

    user = db.get(User, user_id)
    if not user:
        abort(404, description="User not found")

    if user not in project.users:
        abort(400, description="User is not a member of this project")

    # Prevent removing lead/admin (optional safety check)
    if user.user_id == project.lead_id:
        abort(400, description="Cannot remove project lead")

    try:
        project.remove_member(user)
        db.commit()
        return '', 204  # No content

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500

@api_bp.route('/projects/<projcode>/members/<int:user_id>/role', methods=['PATCH'])
@login_required
def update_member_role(projcode, user_id):
    """
    Change a member's role.
    Request body: {"role": "admin"}
    """
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project or not _user_can_edit_project(current_user, project):
        abort(403)

    data = request.get_json()
    new_role = data.get('role')

    if new_role not in ['member', 'admin', 'lead']:
        abort(400, description="Invalid role")

    user = db.get(User, user_id)
    if not user or user not in project.users:
        abort(404)

    try:
        # Implement role update logic
        project.update_member_role(user, new_role)
        db.commit()

        return jsonify({'success': True, 'role': new_role})

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500

@api_bp.route('/projects/<projcode>', methods=['PATCH'])
@login_required
def update_project(projcode):
    """
    Update project metadata (title, description, keywords).
    Request body: {"title": "...", "description": "...", "keywords": "..."}
    """
    db = get_db_session()
    project = Project.get_by_projcode(db, projcode)

    if not project:
        abort(404)

    if not _user_can_edit_project(current_user, project):
        abort(403, description="Only leads/admins can edit project settings")

    data = request.get_json()

    try:
        # Update allowed fields
        if 'title' in data:
            project.title = data['title']
        if 'description' in data:
            project.description = data['description']
        if 'keywords' in data:
            project.keywords = data['keywords']

        db.commit()

        # Return updated project
        return jsonify(ProjectSchema().dump(project))

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500

# Helper function
def get_user_role_in_project(user, project):
    """Determine user's role in project."""
    if user.user_id == project.lead_id:
        return 'lead'
    elif user.user_id == project.admin_id:
        return 'admin'
    else:
        return 'member'
```

**Deliverables**:
- [ ] Implement GET /projects/<projcode>/members
- [ ] Implement POST /projects/<projcode>/members
- [ ] Implement DELETE /projects/<projcode>/members/<user_id>
- [ ] Implement PATCH /projects/<projcode>/members/<user_id>/role
- [ ] Implement PATCH /projects/<projcode>
- [ ] Add access control checks to all endpoints
- [ ] Test endpoints with curl or Postman

#### 3.4 Implement Project Model Methods

**File**: `python/sam/projects/models.py` (or wherever Project is defined)

Add these methods to the `Project` class:

```python
class Project(Base, TimestampMixin):
    # ... existing code ...

    def add_member(self, user, role='member'):
        """
        Add a user to the project with specified role.

        Args:
            user: User object
            role: 'member', 'admin', or 'lead'
        """
        # Implementation depends on your database schema
        # Example if using association table:
        if user not in self.users:
            self.users.append(user)

            # If you have a role column in the association table:
            # project_user = ProjectUser(
            #     project_id=self.project_id,
            #     user_id=user.user_id,
            #     role=role
            # )
            # self.session.add(project_user)

    def remove_member(self, user):
        """Remove a user from the project."""
        if user in self.users:
            self.users.remove(user)

    def update_member_role(self, user, new_role):
        """
        Update a member's role in the project.

        Args:
            user: User object
            new_role: 'member', 'admin', or 'lead'
        """
        # Implementation depends on schema
        # If new_role is 'lead', update project.lead_id
        if new_role == 'lead':
            self.lead_id = user.user_id
        elif new_role == 'admin':
            self.admin_id = user.user_id
        # Otherwise update association table role column
```

**Note**: The exact implementation depends on how your database tracks project membership and roles. Adjust based on your schema.

**Deliverables**:
- [ ] Implement `add_member()` method
- [ ] Implement `remove_member()` method
- [ ] Implement `update_member_role()` method
- [ ] Test methods with pytest
- [ ] Document methods with docstrings

---

### Phase 4: Testing & Documentation (2 days)

#### 4.1 Backend API Tests

**File**: `tests/api/test_project_members.py`

```python
"""
Tests for project member management API endpoints.
"""

import pytest
from flask import url_for
from sam.models import Project, User

def test_get_members_as_project_member(client, sample_project, sample_user):
    """Test that project members can view member list."""
    # Add user to project
    sample_project.add_member(sample_user)
    db.session.commit()

    # Login as member
    login(client, sample_user)

    # Get members
    response = client.get(f'/api/v1/projects/{sample_project.projcode}/members')

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) >= 1
    assert any(m['username'] == sample_user.username for m in data)

def test_get_members_as_non_member(client, sample_project, other_user):
    """Test that non-members cannot view member list."""
    login(client, other_user)

    response = client.get(f'/api/v1/projects/{sample_project.projcode}/members')

    assert response.status_code == 403

def test_add_member_as_admin(client, sample_project, admin_user, new_user):
    """Test that admins can add members."""
    login(client, admin_user)

    response = client.post(
        f'/api/v1/projects/{sample_project.projcode}/members',
        json={'username': new_user.username, 'role': 'member'}
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data['username'] == new_user.username

    # Verify user is now in project
    assert new_user in sample_project.users

def test_add_member_as_regular_user(client, sample_project, regular_user, new_user):
    """Test that regular users cannot add members."""
    login(client, regular_user)

    response = client.post(
        f'/api/v1/projects/{sample_project.projcode}/members',
        json={'username': new_user.username, 'role': 'member'}
    )

    assert response.status_code == 403

def test_add_duplicate_member(client, sample_project, admin_user, existing_member):
    """Test that adding duplicate member returns error."""
    login(client, admin_user)

    response = client.post(
        f'/api/v1/projects/{sample_project.projcode}/members',
        json={'username': existing_member.username, 'role': 'member'}
    )

    assert response.status_code == 400
    assert 'already a member' in response.get_json()['error']

def test_remove_member_as_admin(client, sample_project, admin_user, member_to_remove):
    """Test that admins can remove members."""
    login(client, admin_user)

    response = client.delete(
        f'/api/v1/projects/{sample_project.projcode}/members/{member_to_remove.user_id}'
    )

    assert response.status_code == 204
    assert member_to_remove not in sample_project.users

def test_update_member_role(client, sample_project, admin_user, regular_member):
    """Test changing a member's role."""
    login(client, admin_user)

    response = client.patch(
        f'/api/v1/projects/{sample_project.projcode}/members/{regular_member.user_id}/role',
        json={'role': 'admin'}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data['role'] == 'admin'

def test_update_project_metadata(client, sample_project, admin_user):
    """Test updating project title and description."""
    login(client, admin_user)

    new_title = "Updated Project Title"
    new_description = "Updated description"

    response = client.patch(
        f'/api/v1/projects/{sample_project.projcode}',
        json={
            'title': new_title,
            'description': new_description
        }
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data['title'] == new_title
    assert data['description'] == new_description

# Fixtures
@pytest.fixture
def sample_project(db):
    """Create a sample project for testing."""
    project = Project(
        projcode='TEST0001',
        title='Test Project',
        description='Test description'
    )
    db.session.add(project)
    db.session.commit()
    return project

# Add more fixtures as needed...
```

**Deliverables**:
- [ ] Write tests for all member management endpoints
- [ ] Write tests for project metadata updates
- [ ] Write tests for access control (403 errors)
- [ ] Write tests for edge cases (duplicate members, invalid data)
- [ ] Ensure >90% code coverage
- [ ] Run tests: `cd tests && pytest api/test_project_members.py -v`

#### 4.2 Frontend Integration Tests

**File**: `tests/integration/test_project_dashboard.py`

```python
"""
Integration tests for project dashboard pages.
"""

import pytest
from flask import url_for

def test_project_dashboard_loads(client, sample_project, sample_user):
    """Test that project dashboard page loads correctly."""
    sample_project.add_member(sample_user)
    db.session.commit()

    login(client, sample_user)

    response = client.get(f'/projects/{sample_project.projcode}')

    assert response.status_code == 200
    assert sample_project.title.encode() in response.data
    assert b'Overview' in response.data

def test_members_page_loads(client, sample_project, sample_user):
    """Test that members page loads correctly."""
    sample_project.add_member(sample_user)
    db.session.commit()

    login(client, sample_user)

    response = client.get(f'/projects/{sample_project.projcode}/members')

    assert response.status_code == 200
    assert b'Project Members' in response.data

def test_settings_page_requires_admin(client, sample_project, regular_user):
    """Test that settings page requires admin/lead role."""
    sample_project.add_member(regular_user)
    db.session.commit()

    login(client, regular_user)

    response = client.get(f'/projects/{sample_project.projcode}/settings')

    assert response.status_code == 403

def test_htmx_member_list_returns_json(client, sample_project, sample_user):
    """Test that htmx endpoint returns correct JSON."""
    sample_project.add_member(sample_user)
    db.session.commit()

    login(client, sample_user)

    response = client.get(
        f'/api/v1/projects/{sample_project.projcode}/members',
        headers={'HX-Request': 'true'}  # htmx header
    )

    assert response.status_code == 200
    assert response.content_type == 'application/json'
```

**Deliverables**:
- [ ] Write integration tests for all dashboard pages
- [ ] Test htmx endpoints return correct data
- [ ] Test access control on pages
- [ ] Test forms submit correctly
- [ ] Run tests: `cd tests && pytest integration/test_project_dashboard.py -v`

#### 4.3 JavaScript Unit Tests (Optional)

**File**: `tests/frontend/test_common.js` (using Jest)

```javascript
/**
 * Unit tests for common.js utilities
 */

const {
    formatNumber,
    formatDate,
    getProgressColor,
    getStatusBadge,
    getRoleBadge
} = require('../../python/webui/static/js/common.js');

describe('formatNumber', () => {
    test('formats integers correctly', () => {
        expect(formatNumber(1000)).toBe('1,000');
        expect(formatNumber(1234567)).toBe('1,234,567');
    });

    test('formats decimals correctly', () => {
        expect(formatNumber(1234.56)).toBe('1,234.56');
        expect(formatNumber(1234.567)).toBe('1,234.57'); // Rounds to 2 decimals
    });

    test('handles null/undefined', () => {
        expect(formatNumber(null)).toBe('N/A');
        expect(formatNumber(undefined)).toBe('N/A');
    });
});

describe('formatDate', () => {
    test('formats dates correctly', () => {
        expect(formatDate('2025-01-15')).toMatch(/Jan.*15.*2025/);
    });

    test('handles null', () => {
        expect(formatDate(null)).toBe('N/A');
    });
});

describe('getProgressColor', () => {
    test('returns correct colors for thresholds', () => {
        expect(getProgressColor(95)).toBe('danger');
        expect(getProgressColor(80)).toBe('warning');
        expect(getProgressColor(60)).toBe('info');
        expect(getProgressColor(30)).toBe('success');
    });
});

// Add more tests...
```

**Setup Jest** (if desired):

```json
// package.json
{
  "name": "sam-dashboard-tests",
  "version": "1.0.0",
  "devDependencies": {
    "jest": "^29.0.0"
  },
  "scripts": {
    "test": "jest"
  }
}
```

**Deliverables**:
- [ ] Set up Jest (optional)
- [ ] Write unit tests for `common.js` functions
- [ ] Achieve >80% coverage for JavaScript
- [ ] Run tests: `npm test`

#### 4.4 Documentation Updates

**Update CLAUDE.md**:

```markdown
## Web Dashboard Architecture

### Technology Stack

**Backend:**
- Flask with Blueprint-based modular organization
- SQLAlchemy 2.0 ORM
- Marshmallow schemas for JSON serialization

**Frontend:**
- htmx 1.9+ for AJAX interactions
- Mustache.js for client-side templating
- Alpine.js 3.x for complex client state (filters, forms)
- Bootstrap 4.6.2 for styling
- Custom CSS with CSS variables

### htmx + Mustache Pattern

All dashboards use htmx to fetch JSON from existing APIs and render using Mustache templates:

```html
<!-- htmx loads data from API -->
<div hx-get="/api/v1/users/me/projects"
     hx-trigger="load"
     hx-ext="client-side-templates"
     mustache-array-template="project-template">
</div>

<!-- Mustache template renders the data -->
<script id="project-template" type="x-tmpl-mustache">
{{#projects}}
  <div>{{ title }} ({{ projcode }})</div>
{{/projects}}
</script>
```

**Benefits:**
- ✅ Keep existing JSON APIs unchanged
- ✅ No inline JavaScript in templates
- ✅ Declarative, maintainable code
- ✅ Easy to test and debug

### File Organization

```
python/webui/
├── static/
│   ├── css/custom.css          # All custom styles
│   ├── js/
│   │   ├── common.js           # Shared utilities
│   │   ├── htmx.min.js
│   │   ├── mustache.min.js
│   │   └── alpine.min.js
│   └── templates/              # Mustache templates
│       ├── project-card.mustache
│       └── member-card.mustache
├── templates/
│   ├── user/                   # User dashboard
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   └── resource_details.html
│   └── project/                # Project dashboard
│       ├── base.html
│       ├── dashboard.html
│       ├── members.html
│       └── settings.html
├── blueprints/
│   ├── user_dashboard.py
│   └── project_dashboard.py
└── api/v1/
    ├── users.py
    ├── projects.py
    └── charges.py
```

### Common Patterns

#### Loading data with htmx
```html
<div hx-get="/api/v1/endpoint"
     hx-trigger="load"
     hx-ext="client-side-templates"
     mustache-template="my-template">
</div>
```

#### Updating data with htmx
```html
<form hx-post="/api/v1/projects/PROJ0001/members"
      hx-ext="json-enc"
      hx-on::after-request="this.reset()">
</form>
```

#### Client-side filtering with Alpine.js
```html
<div x-data="{ search: '' }">
    <input x-model="search" @input="filterItems($event.target.value)">
</div>
```

### Adding a New Dashboard

1. Create blueprint in `blueprints/`
2. Create templates in `templates/new_dashboard/`
3. Create Mustache templates in `static/templates/`
4. Add API endpoints in `api/v1/`
5. Write tests in `tests/`
```

**Create new document**: `docs/DASHBOARD_DEVELOPMENT_GUIDE.md`

```markdown
# Dashboard Development Guide

## Overview

This guide explains how to add new dashboards to the SAM web interface using our htmx + Mustache architecture.

## Prerequisites

- Understanding of Flask blueprints
- Basic htmx knowledge (https://htmx.org)
- Mustache templating (https://mustache.github.io)
- Alpine.js for complex state (https://alpinejs.dev)

## Step-by-Step Guide

### 1. Create Blueprint

Create `python/webui/blueprints/my_dashboard.py`:

```python
from flask import Blueprint, render_template
from flask_login import login_required

my_bp = Blueprint('my_dashboard', __name__, url_prefix='/my')

@my_bp.route('/')
@login_required
def index():
    return render_template('my_dashboard/index.html')
```

Register in `run.py`:
```python
from webui.blueprints.my_dashboard import my_bp
app.register_blueprint(my_bp)
```

### 2. Create Template

Create `python/webui/templates/my_dashboard/index.html`:

```html
{% extends "user/base.html" %}

{% block content %}
<div hx-get="/api/v1/my-data"
     hx-trigger="load"
     hx-ext="client-side-templates"
     mustache-template="my-template">
    <div class="loading-spinner">Loading...</div>
</div>

<script id="my-template" type="x-tmpl-mustache">
{{#items}}
  <div>{{ name }}: {{ value | formatNumber }}</div>
{{/items}}
</script>
{% endblock %}
```

### 3. Create API Endpoint

Add to `python/webui/api/v1/my_api.py`:

```python
@api_bp.route('/my-data')
@login_required
def get_my_data():
    # Query database
    items = query_items()

    # Serialize with schema
    return jsonify(MySchema(many=True).dump(items))
```

### 4. Add Tests

Create `tests/integration/test_my_dashboard.py`:

```python
def test_my_dashboard_loads(client, user):
    login(client, user)
    response = client.get('/my/')
    assert response.status_code == 200

def test_my_api_returns_data(client, user):
    login(client, user)
    response = client.get('/api/v1/my-data')
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) > 0
```

### 5. Run and Verify

```bash
# Start dev server
python python/webui/run.py

# Run tests
cd tests && pytest integration/test_my_dashboard.py -v
```

## Best Practices

### DO:
✅ Use htmx for AJAX calls
✅ Use Mustache for templating
✅ Keep JavaScript in `static/js/` files
✅ Use CSS variables for colors
✅ Write tests for all endpoints
✅ Use schemas for JSON serialization

### DON'T:
❌ Write inline JavaScript in templates
❌ Write inline CSS in templates
❌ Create duplicate utility functions
❌ Return HTML from API endpoints (return JSON)
❌ Skip access control checks
❌ Forget to test error cases

## Common Patterns

### Lazy Loading
```html
<button hx-get="/api/v1/lazy-data"
        hx-target="#lazy-container"
        hx-trigger="click once">
    Load More
</button>
<div id="lazy-container"></div>
```

### Form Submission
```html
<form hx-post="/api/v1/submit"
      hx-ext="json-enc"
      hx-on::after-request="this.reset()">
    <input name="field" required>
    <button type="submit">Submit</button>
</form>
```

### Filters with Alpine.js
```html
<div x-data="{ filter: '' }">
    <input x-model="filter" @input.debounce="applyFilter()">
</div>
```

## Troubleshooting

**Problem**: htmx request not triggering
- Check `hx-trigger` attribute
- Verify endpoint URL is correct
- Check browser console for errors

**Problem**: Mustache template not rendering
- Verify template ID matches `mustache-template` attribute
- Check JSON structure matches template
- Use `{{.}}` for debugging to see raw data

**Problem**: Access denied (403)
- Add `@login_required` decorator
- Implement access control checks
- Verify user has permission

## Resources

- [htmx Documentation](https://htmx.org/docs/)
- [Mustache Manual](https://mustache.github.io/mustache.5.html)
- [Alpine.js Documentation](https://alpinejs.dev)
- [Flask Blueprints](https://flask.palletsprojects.com/en/latest/blueprints/)
```

**Deliverables**:
- [ ] Update CLAUDE.md with architecture section
- [ ] Create DASHBOARD_DEVELOPMENT_GUIDE.md
- [ ] Add inline code comments
- [ ] Document all API endpoints (OpenAPI/Swagger)
- [ ] Create architecture diagram (optional)

---

### Phase 5: Performance & Polish (1-2 days)

#### 5.1 Performance Optimization

**Enable CSS/JS Minification**:

Create `python/webui/build.py`:

```python
"""
Build script for minifying CSS and JavaScript.
Run before deployment: python build.py
"""

import os
from pathlib import Path

def minify_css():
    """Minify CSS files."""
    # Use cssmin or similar
    import cssmin

    css_file = Path('static/css/custom.css')
    minified = Path('static/css/custom.min.css')

    with open(css_file) as f:
        css = f.read()

    with open(minified, 'w') as f:
        f.write(cssmin.cssmin(css))

    print(f"✓ Minified {css_file} -> {minified}")

def minify_js():
    """Minify JavaScript files."""
    # Use jsmin or similar
    import jsmin

    js_files = [
        'static/js/common.js',
    ]

    for js_file in js_files:
        source = Path(js_file)
        minified = Path(str(source).replace('.js', '.min.js'))

        with open(source) as f:
            js = f.read()

        with open(minified, 'w') as f:
            f.write(jsmin.jsmin(js))

        print(f"✓ Minified {source} -> {minified}")

if __name__ == '__main__':
    minify_css()
    minify_js()
    print("✓ Build complete!")
```

**Add Caching Headers**:

In `python/webui/run.py`:

```python
@app.after_request
def add_cache_headers(response):
    """Add caching headers for static assets."""
    if request.path.startswith('/static/'):
        # Cache static files for 1 year
        response.cache_control.max_age = 31536000
        response.cache_control.public = True
    return response
```

**Add Loading Indicators**:

In `static/css/custom.css`:

```css
/* htmx loading states */
.htmx-request .htmx-indicator {
    display: inline-block;
}

.htmx-indicator {
    display: none;
}

.htmx-request.htmx-indicator {
    opacity: 0.5;
    pointer-events: none;
}
```

**Optimize Database Queries**:

Check for N+1 queries:

```python
# BAD: N+1 query
for project in projects:
    print(project.lead.full_name)  # Lazy loads each lead

# GOOD: Eager loading
projects = db.query(Project).options(
    joinedload(Project.lead)
).all()
```

**Deliverables**:
- [ ] Create build script for minification
- [ ] Add cache headers for static files
- [ ] Add loading indicators
- [ ] Profile and optimize slow queries
- [ ] Test page load times (<2 seconds)

#### 5.2 Accessibility & UX

**Add ARIA Labels**:

```html
<!-- Before -->
<button hx-delete="/api/v1/members/123">Remove</button>

<!-- After -->
<button hx-delete="/api/v1/members/123"
        aria-label="Remove John Doe from project">
    Remove
</button>
```

**Keyboard Navigation**:

```css
/* Focus indicators */
a:focus, button:focus, input:focus {
    outline: 2px solid var(--primary-color);
    outline-offset: 2px;
}

/* Skip to main content link */
.skip-link {
    position: absolute;
    top: -40px;
    left: 0;
    background: var(--primary-color);
    color: white;
    padding: 8px;
    z-index: 100;
}

.skip-link:focus {
    top: 0;
}
```

**Success/Error Toasts**:

Add to `static/js/common.js`:

```javascript
/**
 * Show toast notification
 */
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `alert alert-${type} toast-notification`;
    toast.textContent = message;
    toast.setAttribute('role', 'alert');

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Listen for htmx events
document.addEventListener('htmx:afterRequest', function(event) {
    if (event.detail.successful) {
        const method = event.detail.requestConfig.verb;
        if (method === 'DELETE') {
            showToast('Item removed successfully', 'success');
        } else if (method === 'POST') {
            showToast('Item added successfully', 'success');
        }
    } else {
        showToast('An error occurred', 'danger');
    }
});
```

**Form Validation**:

```html
<form hx-post="/api/v1/members"
      hx-ext="json-enc"
      hx-on::before-request="return validateForm(this)">
    <input name="username" required pattern="[a-z]+"
           aria-describedby="username-help">
    <small id="username-help">Lowercase letters only</small>
</form>

<script>
function validateForm(form) {
    if (!form.checkValidity()) {
        form.reportValidity();
        return false;
    }
    return true;
}
</script>
```

**Deliverables**:
- [ ] Add ARIA labels to all interactive elements
- [ ] Implement keyboard navigation
- [ ] Add skip-to-content link
- [ ] Create toast notification system
- [ ] Add client-side form validation
- [ ] Test with screen reader (NVDA/VoiceOver)

#### 5.3 Browser Testing

**Test Matrix**:

| Browser | Version | Status |
|---------|---------|--------|
| Chrome | Latest | ✅ Pass |
| Firefox | Latest | ✅ Pass |
| Safari | Latest | ⚠️ Test |
| Edge | Latest | ✅ Pass |
| Mobile Safari | iOS 15+ | ⚠️ Test |
| Mobile Chrome | Latest | ⚠️ Test |

**Responsive Design Check**:

```bash
# Test at different viewport sizes
- Desktop: 1920x1080
- Laptop: 1366x768
- Tablet: 768x1024
- Mobile: 375x667
```

**JavaScript Disabled**:

Add `<noscript>` fallback:

```html
<noscript>
    <div class="alert alert-warning">
        This application requires JavaScript to be enabled.
        Please enable JavaScript in your browser settings.
    </div>
</noscript>
```

**Deliverables**:
- [ ] Test in Chrome, Firefox, Safari, Edge
- [ ] Test on mobile devices (iOS, Android)
- [ ] Verify responsive design at all breakpoints
- [ ] Test with JavaScript disabled (graceful degradation)
- [ ] Fix any browser-specific issues

---

## Timeline & Milestones

### Week 1: Foundation & Refactoring
- **Days 1-3**: Phase 1 - Extract assets, create structure
- **Days 4-5**: Phase 2 (start) - Begin user dashboard refactoring

### Week 2: Complete Refactoring & Start Building
- **Days 1-2**: Phase 2 (finish) - Complete user dashboard refactoring
- **Days 3-5**: Phase 3 (start) - Create project dashboard blueprint & templates

### Week 3: Complete Project Dashboard & Polish
- **Days 1-2**: Phase 3 (finish) - API endpoints, member management
- **Day 3**: Phase 4 - Testing
- **Days 4-5**: Phase 5 - Performance, accessibility, polish

**Total: 11-15 days** (2-3 weeks with focused effort)

---

## Success Criteria

### Technical Metrics
- ✅ **Zero inline JavaScript** in templates (except tiny Alpine snippets)
- ✅ **Zero inline CSS** in templates
- ✅ **Code reduction**: 950 lines JS → 50-100 lines
- ✅ **All JSON APIs preserved** and working
- ✅ **Test coverage**: Backend >90%, Frontend >70%
- ✅ **Page load time**: <2 seconds for dashboard
- ✅ **Accessibility**: WCAG 2.1 AA compliant

### Functional Requirements
- ✅ **User dashboard**: View projects, allocations, usage
- ✅ **Project dashboard**: View project details, usage, members
- ✅ **Member management**: Add/remove users, change roles (leads/admins only)
- ✅ **Metadata editing**: Update project title, description, keywords (leads/admins only)
- ✅ **Access control**: Only project members can view, only leads/admins can edit
- ✅ **Responsive design**: Works on desktop, tablet, mobile

### Developer Experience
- ✅ **Clear patterns**: Documented approach for adding new dashboards
- ✅ **Reusable components**: Mustache templates shared across dashboards
- ✅ **Easy to test**: Separate JS files, unit testable
- ✅ **Easy to maintain**: No monolithic templates, clear separation of concerns

---

## Post-Implementation Notes

### Future Enhancements (Optional)

1. **Add charts back** (if desired):
   - Use Chart.js with Alpine.js for reactivity
   - Fetch time-series data from API
   - Render in dashboard alongside tables

2. **Real-time updates**:
   - WebSockets or Server-Sent Events
   - Live notifications for allocation changes
   - Collaborative editing indicators

3. **Advanced filtering**:
   - Save filter preferences
   - URL-based filters (shareable links)
   - Export filtered data

4. **Batch operations**:
   - Bulk add/remove members
   - Bulk role changes
   - CSV import for members

5. **Audit logging**:
   - Track who added/removed members
   - Track metadata changes
   - Display audit log in UI

### Maintenance Tasks

- **Weekly**: Review error logs, fix bugs
- **Monthly**: Update dependencies (htmx, Bootstrap, Alpine)
- **Quarterly**: Performance review, optimize slow queries
- **Annually**: Accessibility audit, security review

---

## References

### Documentation
- [htmx Documentation](https://htmx.org/docs/)
- [Mustache.js](https://github.com/janl/mustache.js/)
- [Alpine.js Guide](https://alpinejs.dev/start-here)
- [Bootstrap 4 Docs](https://getbootstrap.com/docs/4.6/)
- [Flask Documentation](https://flask.palletsprojects.com/)

### Examples
- [htmx Examples](https://htmx.org/examples/)
- [Alpine.js Examples](https://alpinejs.dev/examples)

### Tools
- [htmx VSCode Extension](https://marketplace.visualstudio.com/items?itemName=otovo-oss.htmx-tags)
- [Alpine.js DevTools](https://github.com/alpine-collective/alpinejs-devtools)
- [Lighthouse (Performance)](https://developers.google.com/web/tools/lighthouse)

---

## Conclusion

This plan transforms a complex, monolithic dashboard implementation into a clean, maintainable architecture using htmx and Mustache templating. The refactoring eliminates 900+ lines of inline JavaScript while preserving all existing JSON APIs and adding powerful new features for project management.

The result is a codebase that is:
- **Simpler** to understand and modify
- **Faster** to develop (less boilerplate)
- **Easier** to test (separation of concerns)
- **More accessible** (WCAG compliant)
- **More performant** (fewer API calls, better caching)

By following this plan, you'll establish patterns that make future dashboard development straightforward and consistent.

---

**Questions or Issues?**
Document any problems or deviations from this plan in `docs/IMPLEMENTATION_NOTES.md` for future reference.
