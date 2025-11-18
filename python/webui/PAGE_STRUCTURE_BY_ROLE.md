# SAM Web Application - Page Structure by Role

**Last Updated:** 2025-11-15
**Status:** ✅ = Implemented | ⏳ = Planned | ❌ = Not Permitted

---

## Role Definitions

| Role | Description | Key Capabilities |
|------|-------------|------------------|
| **Normal User** | End user viewing their allocations | View own projects, resources, usage |
| **Project Lead** | Manages project membership | View projects, add/remove members, request extensions |
| **Admin/Facility Manager** | Manages projects & allocations | Edit project details, manage allocations, create projects |
| **Super Admin** | System administrator | Full access - users, roles, resources, database |

---

## 1. NORMAL USER (`user` role)

**Access:** Read-only view of own projects and allocations

```
SAM Application
│
├── 🔐 Authentication
│   ├── /login ✅ IMPLEMENTED
│   │   └── Username/password login form
│   ├── /logout ✅ IMPLEMENTED
│   └── /profile ✅ IMPLEMENTED
│       ├── View: username, full name, email, user ID, roles
│       └── ❌ Cannot edit profile
│
└── 📊 User Dashboard (/dashboard) ✅ IMPLEMENTED
    │
    ├── Tab 1: Account Statements (default)
    │   │
    │   └── Project Overview Grid ✅ IMPLEMENTED
    │       ├── Shows all user's projects
    │       ├── Project cards (collapsed by default)
    │       │   ├── Card Header (clickable, always visible):
    │       │   │   ├── Project code + status badge
    │       │   │   ├── Project title
    │       │   │   └── Lead name
    │       │   │
    │       │   └── Expand card to see:
    │       │       │
    │       │       ├── 📊 Overall Usage Stats ✅
    │       │       │   ├── Four stat boxes:
    │       │       │   │   ├── ALLOCATED
    │       │       │   │   ├── USED
    │       │       │   │   ├── REMAINING
    │       │       │   │   └── USAGE % (with progress bar)
    │       │       │   └── Visual progress bar across all resources
    │       │       │
    │       │       ├── 🌳 Project Tree ⏳ PLANNED
    │       │       │   ├── Shows parent project (if exists)
    │       │       │   ├── Shows current project (highlighted)
    │       │       │   ├── Shows child projects (if any)
    │       │       │   ├── Collapsible tree nodes
    │       │       │   └── Click project → Navigate to that project card
    │       │       │
    │       │       └── 📈 Resource Usage Breakdown ✅
    │       │           ├── Table with columns:
    │       │           │   ├── Resource Name
    │       │           │   ├── Status (Active/Inactive/Expired)
    │       │           │   ├── Start Date
    │       │           │   ├── End Date
    │       │           │   ├── Allocated
    │       │           │   ├── Used
    │       │           │   ├── Remaining
    │       │           │   └── Usage % (with progress bar)
    │       │           │
    │       │           └── Click any resource row →
    │       │               Resource Details page ✅
    │       │
    │       └── ⏳ Future: Reorganize into collapsible sections
    │           ├── Overall Usage Table (collapsed)
    │           ├── Services Breakdown (expanded)
    │           └── Project Tree (collapsed)
    │
    ├── Tab 2: User Information ✅ IMPLEMENTED
    │   └── Display only:
    │       ├── Username
    │       ├── Full name
    │       ├── Primary email
    │       ├── User ID
    │       └── Roles (badges)
    │
    └── 📈 Resource Details Page (/dashboard/resource-details) ✅ IMPLEMENTED
        ├── URL: ?projcode=XXX&resource=YYY
        ├── Back to Dashboard button
        │
        ├── Resource Usage Chart ✅
        │   ├── Time series (last 90 days, customizable)
        │   ├── Stacked area: comp/dav/disk/archive charges
        │   └── Interactive Chart.js visualization
        │
        ├── Recent Jobs Table ✅ (Collapsible)
        │   ├── Last 100 jobs (configurable)
        │   ├── Columns: Job ID, User, Queue, Machine, Date
        │   ├── Wall time, exit status
        │   └── Success/Failed indicators
        │
        ├── Charge History Table ✅ (Collapsible)
        │   ├── Daily charges breakdown
        │   ├── By type (comp/dav/disk/archive)
        │   └── Date range filterable
        │
        └── Allocation Changes ✅ (Collapsible)
            ├── Manual adjustments to charges
            ├── Columns: Date, Type, Amount, Reason
            └── Shows positive/negative adjustments
```

**What Happens When User Expands a Project Card:**
```
Click Project Card Header
↓
Card Expands to Show:
├─ Overall Usage Stats (4 boxes + progress bar)
├─ Project Tree (parent, current, children) ⏳
└─ Resource Usage Table (all resources)
    └─ Click any resource → Resource Details page
```

**API Endpoints Accessible:**
```
✅ GET /dashboard/api/my-projects
✅ GET /dashboard/api/project/<projcode>/details
✅ GET /dashboard/api/resource-usage-timeseries
✅ GET /dashboard/api/resource-jobs
⏳ GET /dashboard/api/project/<projcode>/tree
✅ GET /api/v1/projects (filtered to user's projects)
✅ GET /api/v1/projects/<projcode> (if user is member)
✅ GET /api/v1/projects/<projcode>/allocations
✅ GET /api/v1/projects/<projcode>/charges
```

**No Access To:**
```
❌ Admin Panel (/admin)
❌ Project editing
❌ User management
❌ Allocation management
❌ Other users' projects
```

---

## 2. PROJECT LEAD (`project_lead` role)

**Access:** All Normal User features + member management + extension requests

**Inherits all Normal User pages, PLUS:**

```
📊 User Dashboard - ENHANCED
│
├── Account Statements Tab
│   └── Project Cards - ENHANCED VIEW
│       └── Expand card to see:
│           ├── Overall Usage Stats ✅
│           ├── Project Tree ⏳
│           ├── Resource Usage Table ✅
│           │   └── Enhanced with member info
│           │
│           └── 👥 Project Members Section ✅ (via API)
│               ├── List all project members
│               ├── Show roles: Lead, Admin, Member
│               └── [Future] Manage buttons:
│                   ├── "Add Member" button
│                   ├── "Remove" button per member
│                   └── "Change Role" dropdown per member
│
└── ⏳ Project Management Page (PLANNED)
    (/dashboard/project/<projcode>/manage)
    │
    ├── Project Overview Section (read-only)
    │   ├── Project code, title, dates
    │   ├── Lead & admin info
    │   ├── Current allocations summary
    │   └── ❌ Cannot edit these details
    │
    ├── ✅ Member Management Section
    │   ├── View all project members
    │   ├── Add members
    │   │   ├── Search users by username/email
    │   │   └── Select role: Member, Admin, or Lead
    │   ├── Remove members
    │   ├── Change member roles
    │   └── Audit trail of member changes
    │
    └── ✅ Extension Request Section
        ├── View current allocation end dates
        ├── Request extension form:
        │   ├── Select resource(s)
        │   ├── Requested new end date
        │   ├── Justification (text area)
        │   └── Submit button
        ├── View pending requests
        ├── View request history
        └── Request status: Pending/Approved/Denied
```

**What Happens When Project Lead Expands a Project Card:**
```
Click Project Card Header
↓
Card Expands to Show:
├─ Overall Usage Stats
├─ Project Tree (with navigation)
├─ Resource Usage Table
│   └─ Click resource → Resource Details
└─ Project Members List ← NEW
    ├─ See all members and their roles
    └─ [Future] Add/Remove/Change role buttons
```

**Additional API Endpoints:**
```
✅ GET /api/v1/projects/<projcode>/members
✅ GET /api/v1/users (view all users for member search)
✅ GET /api/v1/users/<username>
✅ GET /api/v1/users/<username>/projects

⏳ Planned APIs:
POST   /api/v1/projects/<projcode>/members (add member)
DELETE /api/v1/projects/<projcode>/members/<user_id> (remove member)
PUT    /api/v1/projects/<projcode>/members/<user_id> (change role)
POST   /api/v1/projects/<projcode>/extension-requests
GET    /api/v1/projects/<projcode>/extension-requests
```

**Can Do:**
- ✅ View all project members
- ✅ Add new members to project
- ✅ Remove members from project
- ✅ Change member roles (Member ↔ Admin)
- ✅ Request allocation extensions
- ✅ View extension request status

**Cannot Do:**
- ❌ Edit project title, abstract, dates
- ❌ Create new projects
- ❌ Delete projects
- ❌ Directly edit allocations (must request via extension)
- ❌ Create/edit resources
- ❌ Access admin panel

---

## 3. ADMIN / FACILITY MANAGER (`facility_manager` role)

**Access:** All Project Lead features + full project/allocation management

**Inherits all Project Lead pages, PLUS:**

```
🔧 Admin Panel (/admin) ✅ IMPLEMENTED
│
├── 📋 Admin Dashboard
│   └── Expiring Projects View ✅
│       ├── Tab: Upcoming Expirations (0-32 days)
│       ├── Tab: Recently Expired (90-365 days)
│       ├── Filter by facility (UNIV, WNA, etc.)
│       ├── Shows: Project, Resource, Days remaining/expired
│       └── Export to CSV
│
├── 👥 User Management (READ-ONLY)
│   ├── View Users ✅
│   ├── Search/Filter users
│   ├── View user details
│   ├── View user institutions
│   └── ❌ Cannot create/edit/delete users
│
├── 📁 Project Management (FULL EDIT) ✅
│   ├── View All Projects
│   ├── Create New Project
│   │   ├── Project code, title
│   │   ├── Assign lead & admin
│   │   ├── Set dates, area of interest
│   │   └── Unix GID settings
│   │
│   ├── Edit Project Details ✅
│   │   ├── Modify title, abstract
│   │   ├── Change lead/admin
│   │   ├── Update dates (start/end)
│   │   ├── Change area of interest
│   │   └── Manage charging exemptions
│   │
│   ├── Project Directories ✅
│   │   ├── View project directories
│   │   ├── Add directory paths
│   │   └── Remove directories
│   │
│   └── View Project Hierarchy
│       └── See parent/child relationships
│
├── 💰 Allocation Management (FULL EDIT) ✅
│   ├── View All Allocations
│   ├── Create New Allocation
│   │   ├── Select account (project + resource)
│   │   ├── Set amount
│   │   ├── Set start/end dates
│   │   ├── Choose allocation type
│   │   └── Link to parent allocation (optional)
│   │
│   ├── Edit Allocation
│   │   ├── Modify amount
│   │   ├── Extend end date
│   │   ├── Change allocation type
│   │   └── View allocation hierarchy
│   │
│   ├── Allocation Transactions ✅
│   │   └── View all allocation changes/transfers
│   │
│   └── ⏳ Extension Request Management (PLANNED)
│       ├── View pending extension requests
│       ├── Approve/Deny requests
│       ├── Add approval notes
│       └── Notify requester
│
├── 🔗 Account Management (FULL EDIT) ✅
│   ├── View All Accounts
│   ├── Create Account
│   │   ├── Link project to resource
│   │   ├── Set account parameters
│   │   └── Assign users
│   │
│   ├── Edit Account Details
│   ├── View Account Users
│   └── Delete/Deactivate Account
│
├── 🖥️ Resource Management (EDIT ONLY) ✅
│   ├── View All Resources
│   ├── Edit Resource Details
│   │   ├── Modify description
│   │   ├── Change resource type
│   │   └── Update status
│   │
│   ├── View Machines (read-only)
│   ├── View Queues (read-only)
│   └── ❌ Cannot create new resources
│
└── 📊 Reports & Analytics ✅
    ├── Charge Summaries
    │   ├── Comp Charge Summary
    │   ├── HPC Charge Summary
    │   ├── DAV/Disk/Archive Summaries
    │   └── Filter by date/account/resource
    │
    ├── System Statistics
    │   └── View usage trends, totals
    │
    └── Export Data
        └── Download reports as CSV
```

**What Happens When Admin Expands a Project Card:**
```
Click Project Card Header
↓
Card Expands to Show:
├─ Overall Usage Stats
├─ Project Tree (with navigation)
├─ Resource Usage Table
│   └─ Click resource → Resource Details
├─ Project Members List
│   └─ Add/Remove/Change role buttons (active)
└─ "Edit Project Details" button ← Links to Admin Panel
```

**Additional Capabilities:**
- ✅ View ALL projects (not just own)
- ✅ Create/edit/delete projects
- ✅ Directly modify allocations (no approval needed)
- ✅ Manage project-resource linkages (accounts)
- ✅ Approve extension requests (when implemented)
- ✅ Export system data

**Cannot Do:**
- ❌ Create/edit/delete users
- ❌ Create new resources, machines, queues
- ❌ Manage user roles
- ❌ Access database admin tables
- ❌ Modify system configuration

---

## 4. SUPER ADMIN (`admin` role)

**Access:** EVERYTHING - complete system control

**Inherits all Admin/Facility Manager pages, PLUS:**

```
🔧 Admin Panel - FULL ACCESS
│
├── All Admin/Facility Manager Features
│   └── Plus write access where they had read-only
│
├── 👥 User Management (FULL CRUD) ⏳ PLANNED
│   ├── Create Users
│   │   ├── Username, names, email
│   │   ├── UPID, unix_uid
│   │   ├── Organization/institution
│   │   └── Initial role assignment
│   │
│   ├── Edit User Details
│   │   ├── Update contact info
│   │   ├── Change organizations
│   │   └── Modify status (active/inactive)
│   │
│   ├── Delete Users
│   └── Manage User Institutions
│       ├── Add institution affiliations
│       └── Remove affiliations
│
├── 🖥️ Resource Management (FULL CRUD) ⏳ PLANNED
│   ├── Create New Resources
│   │   ├── Resource name, type
│   │   ├── Facility assignment
│   │   └── Configuration settings
│   │
│   ├── Create Machines
│   ├── Create Queues
│   ├── Edit Resources/Machines/Queues
│   └── Delete Resources
│
├── 🔐 System Administration ✅
│   │
│   ├── Role Management ⏳ PLANNED
│   │   ├── View All Roles
│   │   ├── Create New Role
│   │   ├── Assign Roles to Users
│   │   ├── Edit Role Permissions
│   │   └── Delete Roles
│   │
│   ├── System Configuration ⏳ PLANNED
│   │   ├── Charging Formulas
│   │   ├── Charging Factors
│   │   ├── Facility Settings
│   │   └── Email Templates
│   │
│   └── API Credentials ✅ (via Everything tables)
│       ├── View API keys
│       ├── Create API credentials
│       └── Manage API roles
│
└── 🗄️ Database Admin - "Everything" Tables ✅ IMPLEMENTED
    (/admin/everything/*)
    │
    └── Direct CRUD access to 91+ tables:
        │
        ├── Core Tables
        │   ├── users, email_address, user_institution
        │   ├── organization, institution
        │   └── project, project_number, project_code
        │
        ├── Accounting Tables
        │   ├── account, account_user
        │   ├── allocation, allocation_transaction
        │   ├── allocation_type
        │   └── charge_adjustment
        │
        ├── Resource Tables
        │   ├── resources, resource_type
        │   ├── machine, queue
        │   ├── facility, factor, formula
        │   └── access_branch, access_branch_resource
        │
        ├── Activity Tables
        │   ├── comp_job, comp_activity
        │   ├── hpc_activity, hpc_charge
        │   ├── dav_activity, dav_charge
        │   ├── disk_activity, disk_charge
        │   └── archive_activity, archive_charge
        │
        ├── Summary Tables
        │   ├── comp_charge_summary, hpc_charge_summary
        │   ├── dav_charge_summary
        │   ├── disk_charge_summary
        │   └── archive_charge_summary
        │
        ├── Security Tables
        │   ├── role, role_user
        │   ├── api_credentials, role_api_credentials
        │   └── responsible_party
        │
        ├── Integration Tables
        │   ├── xras_* (XRAS integration)
        │   └── Various mapping tables
        │
        └── Operational Tables
            ├── synchronizer, task, product
            ├── adhoc_group, adhoc_group_tag
            └── project_contract, project_directory
```

**What Happens When Super Admin Expands a Project Card:**
```
Click Project Card Header
↓
Card Expands to Show (same as Admin):
├─ Overall Usage Stats
├─ Project Tree (with navigation)
├─ Resource Usage Table
├─ Project Members List (with manage buttons)
└─ "Edit Project Details" button
    └─ Plus access to Everything tables for direct DB editing
```

**Unique Super Admin Capabilities:**
- ✅ Full CRUD on all 91+ database tables
- ✅ Create/edit/delete users
- ✅ Create resources, machines, queues
- ✅ Manage user roles and permissions
- ✅ System-level configuration
- ✅ Direct SQL access via Flask-Admin
- ✅ No restrictions anywhere

---

## Permission Matrix

| Capability | Normal User | Project Lead | Admin | Super Admin |
|-----------|:-----------:|:------------:|:-----:|:-----------:|
| **Viewing** | | | | |
| View own projects | ✅ | ✅ | ✅ | ✅ |
| View all projects | ❌ | ❌ | ✅ | ✅ |
| View project members | ❌ | ✅ | ✅ | ✅ |
| View project tree | ⏳ | ⏳ | ⏳ | ⏳ |
| View allocations | ✅ | ✅ | ✅ | ✅ |
| View resources | ✅ | ✅ | ✅ | ✅ |
| View users | ❌ | ✅ | ✅ | ✅ |
| View reports | ✅ | ✅ | ✅ | ✅ |
| Access admin panel | ❌ | ❌ | ✅ | ✅ |
| **Project Management** | | | | |
| Add/remove members | ❌ | ✅ | ✅ | ✅ |
| Request extensions | ❌ | ✅ | N/A | N/A |
| Edit project details | ❌ | ❌ | ✅ | ✅ |
| Create projects | ❌ | ❌ | ✅ | ✅ |
| Delete projects | ❌ | ❌ | ✅ | ✅ |
| **Allocation Management** | | | | |
| Edit allocations | ❌ | ❌ | ✅ | ✅ |
| Create allocations | ❌ | ❌ | ✅ | ✅ |
| Approve extensions | ❌ | ❌ | ✅ | ✅ |
| **Resource Management** | | | | |
| Edit resources | ❌ | ❌ | ✅ | ✅ |
| Create resources | ❌ | ❌ | ❌ | ✅ |
| **User Management** | | | | |
| Edit users | ❌ | ❌ | ❌ | ✅ |
| Create users | ❌ | ❌ | ❌ | ✅ |
| Delete users | ❌ | ❌ | ❌ | ✅ |
| **System Admin** | | | | |
| Manage roles | ❌ | ❌ | ❌ | ✅ |
| System config | ❌ | ❌ | ❌ | ✅ |
| Database admin | ❌ | ❌ | ❌ | ✅ |
| Export data | ❌ | ❌ | ✅ | ✅ |

---

## Project Card Expanded View (All Roles)

### What Each Role Sees When They Expand a Project Card:

**Normal User:**
```
┌─ Project: SCSG0001 [Active] ────────────────────────┐
│ CSG Systems Project                                 │
│ Lead: Ben Kirk                                      │
└─────────────────────────────────────────────────────┘
    │
    ├─ 📊 Overall Usage Stats
    │   ├─ ALLOCATED: 1,000,000
    │   ├─ USED: 456,789
    │   ├─ REMAINING: 543,211
    │   └─ USAGE: 45.7% [████████░░░░░░░░]
    │
    ├─ 🌳 Project Tree (⏳ Future)
    │   ├─ Parent: (none)
    │   ├─ Current: SCSG0001 ← You are here
    │   └─ Children: (none)
    │
    └─ 📈 Resource Usage Table
        ├─ Derecho: Active, 500k allocated, 234k used → Click for details
        ├─ Casper: Active, 300k allocated, 123k used → Click for details
        └─ Campaign: Active, 200k allocated, 99k used → Click for details
```

**Project Lead (adds member list):**
```
┌─ Project: SCSG0001 [Active] ────────────────────────┐
│ CSG Systems Project                                 │
│ Lead: Ben Kirk                                      │
└─────────────────────────────────────────────────────┘
    │
    ├─ 📊 Overall Usage Stats (same as Normal User)
    ├─ 🌳 Project Tree (same as Normal User)
    ├─ 📈 Resource Usage Table (same as Normal User)
    │
    └─ 👥 Project Members ← NEW
        ├─ Ben Kirk (Lead)
        ├─ Mary Smith (Admin)
        ├─ John Doe (Member)
        └─ [Future] Buttons: [Add Member] [Request Extension]
```

**Admin (adds edit button):**
```
┌─ Project: SCSG0001 [Active] ────────────────────────┐
│ CSG Systems Project                                 │
│ Lead: Ben Kirk                 [Edit Project] ← NEW │
└─────────────────────────────────────────────────────┘
    │
    ├─ 📊 Overall Usage Stats
    ├─ 🌳 Project Tree
    ├─ 📈 Resource Usage Table
    │
    └─ 👥 Project Members
        ├─ Ben Kirk (Lead) [Remove] [Change Role] ← Active buttons
        ├─ Mary Smith (Admin) [Remove] [Change Role]
        ├─ John Doe (Member) [Remove] [Change Role]
        └─ [Add Member] [Directly Edit Allocation] ← Admin powers
```

**Super Admin (same as Admin + database access):**
```
Same as Admin view
    +
Access to /admin/everything/* for direct database editing
```

---

## URL Map

### Public (Unauthenticated)
```
GET  /login                     → Login page
POST /login                     → Login form submission
```

### User Dashboard (All Authenticated Users)
```
GET  /                          → Redirect to dashboard or login
GET  /dashboard                 → Main user dashboard
                                  ├─ Shows project cards (collapsed)
                                  └─ Expand to see: stats, tree, resources
GET  /dashboard/resource-details → Resource charts & jobs
GET  /profile                   → User profile (read-only)
GET  /logout                    → Logout action
```

### Project Lead Features (Project Lead & Above)
```
⏳ GET  /dashboard/project/<projcode>/manage → Project management page
⏳ POST /api/v1/projects/<projcode>/members  → Add member
⏳ DELETE /api/v1/projects/<projcode>/members/<user_id> → Remove member
⏳ PUT  /api/v1/projects/<projcode>/members/<user_id> → Change role
⏳ POST /api/v1/projects/<projcode>/extension-requests → Request extension
⏳ GET  /api/v1/projects/<projcode>/extension-requests → View requests
```

### Admin Panel (Admin & Super Admin)
```
GET  /admin                     → Admin dashboard
GET  /admin/user                → User management (view for admin, full for super admin)
GET  /admin/project             → Project management
GET  /admin/account             → Account management
GET  /admin/allocation          → Allocation management
GET  /admin/resource            → Resource management
GET  /admin/projectexpirationview → Expiring projects dashboard
POST /admin/project/new         → Create project
POST /admin/allocation/new      → Create allocation
...  (standard Flask-Admin CRUD endpoints)
```

### Super Admin Only
```
GET  /admin/everything/*        → Direct database table access (91 tables)
POST /admin/everything/*/new    → Create records in any table
PUT  /admin/everything/*/edit   → Edit records in any table
DELETE /admin/everything/*/delete → Delete records in any table
```

### API Endpoints (Role-Dependent)
```
# All Authenticated Users
GET  /dashboard/api/my-projects
GET  /dashboard/api/project/<projcode>/details
GET  /dashboard/api/resource-usage-timeseries
GET  /dashboard/api/resource-jobs
⏳ GET  /dashboard/api/project/<projcode>/tree
GET  /api/v1/projects (filtered by permissions)
GET  /api/v1/projects/<projcode>
GET  /api/v1/projects/<projcode>/allocations
GET  /api/v1/projects/<projcode>/charges

# Project Lead & Above
GET  /api/v1/projects/<projcode>/members
GET  /api/v1/users
GET  /api/v1/users/<username>

# Admin & Above
GET  /api/v1/projects/expiring
GET  /api/v1/projects/recently_expired
```

---

## User Flow Diagram

### Normal User Journey
```
1. Login (/login)
   ↓
2. Dashboard (/dashboard)
   ├─→ See collapsed project cards
   ├─→ Click card → Expands to show:
   │   ├─ Usage stats
   │   ├─ Project tree ⏳
   │   └─ Resource table
   │       └─ Click resource → Resource Details
   └─→ User Info tab → View profile

3. Resource Details (/dashboard/resource-details?projcode=X&resource=Y)
   ├─→ View charts
   ├─→ View jobs
   └─→ Back to Dashboard
```

### Project Lead Journey
```
1. Login
   ↓
2. Dashboard
   ├─→ Click project card → Expands to show:
   │   ├─ Usage stats
   │   ├─ Project tree ⏳
   │   ├─ Resource table
   │   └─ Members list ← NEW
   │       └─ [Future] Manage members
   └─→ [Future] "Manage Project" button
       ↓
3. [Planned] Project Management Page
   ├─→ Add/remove members
   └─→ Request extension
```

### Admin Journey
```
1. Login
   ↓
2. Choose:
   ├─→ User Dashboard
   │   └─→ View/manage projects
   │       └─ "Edit Project" button → Admin Panel
   │
   └─→ Admin Panel (/admin)
       ├─→ Expiring Projects
       ├─→ Manage Projects (create/edit)
       ├─→ Manage Allocations
       └─→ Reports
```

---

## Development Test Users

**Configured in** `python/webui/run.py`:

| Username | Role | What They See in Dashboard |
|----------|------|----------------------------|
| `negins` | Normal User | Projects, tree, resources (read-only) |
| `rory` | Project Lead | + Members list |
| `mtrahan` | Facility Manager | + Edit buttons, Admin panel |
| `benkirk` | Super Admin | + Everything tables |

---

## Implementation Status

### ✅ Currently Working
- Authentication & RBAC
- User dashboard with project cards
- Expand/collapse project cards
- Overall usage stats
- Resource usage table
- Click resource → Resource details page
- Admin panel for project/allocation management

### ⏳ Planned Next
- Project tree visualization (backend ready, needs UI)
- Member list in expanded card (API ready, needs UI)
- Add/remove members UI
- Extension request UI
- State persistence (localStorage)

---

**Last Updated:** 2025-11-15
**Document Version:** 3.0
