## Summary

Implements a complete Role-Based Access Control (RBAC) system for managing project membership directly from the user dashboard. Project leads and admins can now add/remove members and manage admin roles through an intuitive UI.

## Features Implemented

### Authorization Model (Dual-Layer)
- **Project-level permissions**: Lead can manage all members + change admin; Admin can manage members but cannot reassign admin role
- **System-level override**: Users with EDIT_PROJECT_MEMBERS permission (admin, facility_manager) can manage any project's membership

### New Files
- `python/webapp/utils/project_permissions.py` - Authorization helpers:
  - `can_manage_project_members(user, project)`
  - `can_change_admin(user, project)`
- `python/webapp/templates/user/fragments/member_modals.html` - Bootstrap modals for add member, remove confirmation, and admin change dialogs

### Backend Changes
- **queries/__init__.py**: Added 5 new functions:
  - `add_user_to_project()` - Adds user to all project accounts
  - `remove_user_from_project()` - Removes user + clears admin if needed
  - `change_project_admin()` - Changes admin with validation
  - `search_users_by_pattern()` - Autocomplete search (username, name, email)
  - `get_project_member_user_ids()` - For excluding existing members
  - Enhanced `get_user_breakdown_for_project()` with display_name

- **user_dashboard.py**: Added 4 new routes:
  - `POST /projects/<projcode>/members/add`
  - `POST /projects/<projcode>/members/<username>/remove`
  - `POST /projects/<projcode>/admin`
  - `GET /users/search` (JSON autocomplete)

- **rbac.py**: Added `EDIT_PROJECT_MEMBERS` permission

### Frontend Changes
- **members_table.html**: Complete rewrite with role badges (Lead/Admin/Member), conditional action buttons, dedicated Username column
- **base.html**: Added ~270 lines of JavaScript for member management (user search autocomplete, AJAX form handling, modal interactions)
- **resource_details.html**: Added User + Username columns to breakdown table
- **dashboard.html**: Included member management modals

### UX Features
- User search with debounced autocomplete (searches name, username, email)
- Confirmation dialogs for destructive actions
- Real-time table updates via AJAX (no page reload)
- Role-based UI: buttons only shown to authorized users
- Optional end date for membership (can be NULL for no expiration)

---

## Phase 2 Plan: Request/Approval Workflow

### Overview
Phase 2 will add a request workflow for changes that require approval rather than immediate execution. This enables self-service requests while maintaining oversight for sensitive operations.

### Database Schema
```sql
CREATE TABLE change_request (
    request_id INT PRIMARY KEY AUTO_INCREMENT,
    request_type VARCHAR(50) NOT NULL,
    -- Types: 'add_member', 'remove_member', 'change_admin',
    --        'increase_allocation', 'extend_dates', 'new_project'

    requestor_user_id INT NOT NULL,
    project_id INT NULL,
    resource_id INT NULL,
    target_user_id INT NULL,

    request_payload JSON NOT NULL,
    -- Stores change details, e.g.:
    -- {"new_amount": 1500000, "justification": "..."}
    -- {"new_end_date": "2025-12-31", "reason": "..."}

    status VARCHAR(20) DEFAULT 'pending',
    -- Values: 'pending', 'approved', 'rejected', 'cancelled', 'expired'

    approver_user_id INT NULL,
    reviewer_notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP NULL,
    expires_at TIMESTAMP NULL,

    FOREIGN KEY (requestor_user_id) REFERENCES users(user_id),
    FOREIGN KEY (project_id) REFERENCES project(project_id),
    FOREIGN KEY (approver_user_id) REFERENCES users(user_id)
);
```

### Request Types & Payloads
1. **Allocation Increase**: `{current: 1M, requested: 1.5M, justification: "..."}`
2. **Date Extension**: `{resource: "Derecho", current_end: "...", new_end: "..."}`
3. **Member Addition**: `{username: "...", role: "member", justification: "..."}`
4. **New Project**: `{title: "...", allocation_type: "...", resources: [...]}`

### Approval Routing
- Configurable per request_type: who can approve
- Default: facility_manager or admin for allocation/date changes
- Project lead approval for member additions (if configured)
- Email notifications on submit/approve/reject

### UI Components (Future)
- Request submission forms (reuse existing modals with "Request" mode)
- Admin dashboard: pending requests queue with approve/reject actions
- User dashboard: "My Requests" section showing status
- Request detail view with history/comments

### Implementation Approach
- Phase 1 business logic functions remain unchanged
- New "request mode" flag in UI triggers request creation vs immediate action
- Approval action calls same business logic (add_user_to_project, etc.)
- Audit trail via request table history

