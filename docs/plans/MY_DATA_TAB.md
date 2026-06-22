# "My Data" — per-user filesystem-scan view on the User Dashboard

## Context

PR #329 (`fs_scans_status_tab`, → `staging`) added a **whole-filesystem** scans
view as a `VIEW_ALL_FILESYSTEM_DATA`-gated "Filesystem Scans" tab on the Status
dashboard (`/status`), one subtab per scan-capable disk resource, each rendering
the shared `disk_scans_card.html` in `mode='resource'` (rooted over the whole
collection, `path_prefixes=None`).

This follow-up surfaces the **same card scoped to the logged-in user's own
files** — a **"My Data"** tab on the **User Dashboard** (`/user`,
`templates/dashboards/user/dashboard.html`), available to **every authenticated
user** (no special permission), showing Large Directories + the Access-history
and File-size histograms filtered to files **the user owns**. The "User / group
counts" tab is dropped (single user). No project scoping — it cuts across all of
the user's projects + scratch/home, i.e. their total footprint, filtered by
ownership.

**This is the recommended sequencing: merge PR #329 first, then build this on
top of its plumbing.** It is a small, SAM-only PR. **No plugin change is
required for v1** — owner filtering is already wired end-to-end (see below).

---

## Why no plugin change is needed

The whole-FS PR already built owner-filterable resource service functions, and
they are exercised today by the admin per-user drill-down. A "My Data" view is
just **resource mode with `owner_uid` pinned to the current user and the
entities tab hidden**:

| Service fn (`disk_scans/service.py`) | owner filter | line |
|---|---|---|
| `scan_directories_resource(resource, owner_uid=…)` | ✓ (`owner_id=owner_uid` → facade) | ~340 |
| `scan_access_history_resource(resource, owner_uid=…)` | ✓ | ~624 |
| `scan_file_sizes_resource(resource, owner_uid=…)` | ✓ | ~638 |

So **the service layer needs nothing new.** Cache keys already include
`owner_uid` (`{'owner_uid': owner_uid}` in `_access_history` / `_file_sizes`;
the directories opts dict carries it too), so each user's view caches
independently.

`User.unix_uid` exists; the routes already resolve `User ↔ unix_uid` both ways
(`_resolve_owner`, `routes.py:194-204`).

When a plugin change *would* be needed (out of scope for v1):
- group-membership "files I can **access**" semantics (vs "files I **own**");
- a per-owner materialized rollup, only if the on-the-fly owner-filtered
  whole-collection scan proves too hot at login scale (cache + lazy-load should
  prevent this).

---

## ⚠️ The one security-critical requirement

The existing resource routes are `VIEW_ALL_FILESYSTEM_DATA`-gated **and trust
`?owner_uid=` / `?owner_user_id=` from the query string** (`_resolve_owner`,
`routes.py:183`; `_render_distribution` reads `request.args.get('owner_uid')`
directly at `routes.py:665`; `_dir_filters` → `flt['owner_uid']` likewise comes
from request args).

The "My Data" routes are gated only by `@login_required`, so they **must pin
`owner_uid = current_user.unix_uid` server-side and ignore any client-supplied
owner param.** Otherwise any logged-in user could pass `?owner_uid=<someone
else>` and read another person's footprint — a data leak that bypasses the
VIEW_ALL gate.

**This is the single must-get-right item, and it needs a dedicated test**
(user A cannot read user B's data even with `?owner_uid=<B>` / `?owner_user_id=<B>`).

Because the three render helpers source the owner from `request.args`, pinning
must happen *inside* them when in user mode — see Step 2.

---

## Step 1 — Service (`src/webapp/disk_scans/service.py`)

**No new functions.** "My Data" reuses the existing `*_resource(…, owner_uid=…)`
fns. (Optional nicety: a one-line `scan_capable_resources()` is already present
and reused as-is to enumerate which resources to show.)

---

## Step 2 — Routes (`src/webapp/disk_scans/routes.py`)

Add a hardened owner source and three thin user-scoped routes. Import
`current_user` from `flask_login` (already imports `login_required`).

### 2a. `_user_ctx(resource_name)` — `_resource_ctx` analogue, owner pinned

```python
def _user_ctx(resource_name: str) -> dict:
    """`_resource_ctx` for user mode: whole-collection, owner pinned to me.

    `forced_owner_uid` is the authenticated user's unix_uid; the render
    helpers MUST use it and ignore any ?owner_uid / ?owner_user_id. None when
    the account has no unix_uid (caller renders the no-identity empty state).
    """
    ctx = _resource_ctx(resource_name)        # project=None, scope='', fileset, target_id
    ctx['mode'] = 'user'
    ctx['forced_owner_uid'] = getattr(current_user, 'unix_uid', None)
    return ctx
```

### 2b. Thread `forced_owner_uid` into the three render helpers

This is the hardening. Each helper currently reads owner from `request.args`;
when `ctx.get('forced_owner_uid')` is set (or pass it explicitly), override it
**before** scan + render, and drop `owner_user_id` so it can't re-enter:

- `_render_directories_fragment`: after `flt = _dir_filters()`, if a forced uid
  is present, `flt['owner_uid'] = forced; flt['owner_user_id'] = None`. The
  `_scan(flt)` closure then forwards the pinned uid unchanged.
- `_render_distribution`: replace `owner_uid = request.args.get('owner_uid', type=int)`
  (line 665) with `owner_uid = forced if forced is not None else request.args.get('owner_uid', type=int)`.
- `_render_entities`: not used by the card (entities tab hidden in user mode),
  but harden it the same way for defense-in-depth if you wire it at all.

Prefer an explicit `forced_owner_uid=None` kwarg on each helper over reading it
out of `ctx` implicitly — it makes the security contract visible at every call
site. The project/resource routes pass nothing (unchanged behavior).

### 2c. Three new routes — `@login_required` ONLY (no `require_permission`)

Mirror the `*_resource_fragment` routes but: ctx via `_user_ctx`, `_scan`
closes over the `*_resource(…, owner_uid=current_user.unix_uid)` fns, and the
`dir_fragment_url` points at the **user** directories fragment.

```
GET /user/directories           → directories_user_fragment
GET /user/access-history         → access_history_user_fragment
GET /user/file-sizes             → file_sizes_user_fragment
GET /user/explore                → directories_user_page   (full view)
```

(URL stems are suggestions; the blueprint `url_prefix` is
`/dashboards/user/disk-scans`. Avoid colliding with the existing
`/<projcode>/…` project routes — `/user/…` and `/resource/…` are both safe
static prefixes since a projcode is matched as `<projcode>`. Double-check Flask
route ordering / converter ambiguity vs `/<projcode>/directories`; if it
collides, use a distinct stem like `/me/directories`.)

Empty-state: when `forced_owner_uid is None` (account has no unix_uid), short-
circuit each fragment to render the partial's disabled/empty branch with a
"No filesystem identity on record" message rather than running an unfiltered
scan.

### 2d. User explorer "full view" page

`directories_user_page` mirrors `directories_resource_page` BUT:
- ctx via `_user_ctx`; force `flt['owner_uid']` before `_initial_fragment_url`
  so the initial table load is owner-pinned;
- **hide the owner user-picker** in the filter panel (a user can't filter by a
  different owner). The page template (`disk_scans_directories_page.html`)
  needs a `mode='user'` branch that omits the `user_search_url` picker. Confirm
  no other filter-panel control lets the owner be changed.

---

## Step 3 — Card template (`templates/dashboards/user/partials/disk_scans_card.html`)

Add `mode == 'user'` to the existing `project`/`resource` branch (top of file,
the `{% set _*_url %}` block):

```jinja
{% elif mode == 'user' %}
    {% set _dirs_url  = url_for('disk_scans.directories_user_fragment',   target_id=cid ~ '-directories') %}
    {% set _acc_url   = url_for('disk_scans.access_history_user_fragment', target_id=cid ~ '-access') %}
    {% set _files_url = url_for('disk_scans.file_sizes_user_fragment',    target_id=cid ~ '-files') %}
    {% set _full_url  = url_for('disk_scans.directories_user_page') %}
    {# no _ent_url — entities tab hidden #}
```

Wrap the **"User / group counts"** `<li>` (lines ~50-60) and its pane (lines
~100-106) in `{% if mode != 'user' %}…{% endif %}`. The remaining 3 tabs are
unchanged. (`resource_name` is irrelevant in user mode — the routes take no
resource — but the card's other params still apply.)

Card params in user mode: `mode='user'`, `cid`, `tablist_id`, `load_trigger`.
No `projcode`/`scope`/`fileset`/`resource_name` needed.

---

## Step 4 — "My Data" tab on the User Dashboard

`templates/dashboards/user/dashboard.html` currently has two tabs (`dashboardTabs`):
*My Accounts* (active) and *User Information* (`#user-info-tab`, lines 25-29 /
pane 72-80). Add **after User Information**:

```jinja
<li class="nav-item">
    <a class="nav-link" id="my-data-tab" data-bs-toggle="tab" href="#my-data" role="tab">
        <i class="fas fa-hard-drive"></i> My Data
    </a>
</li>
```
and the pane (after the User Information pane, before `</div><!-- End Tab Content -->`):

```jinja
<div class="tab-pane fade" id="my-data" role="tabpanel">
    {% if my_data_available %}
        {% with mode='user', cid='my-data', tablist_id='myDataTabs',
                load_trigger='shown.bs.tab once from:#my-data-tab' %}
            {% include 'dashboards/user/partials/disk_scans_card.html' %}
        {% endwith %}
    {% else %}
        <div class="alert alert-info"><i class="fas fa-info-circle"></i>
            Filesystem-scan data is unavailable for your account.</div>
    {% endif %}
</div>
```

`load_trigger='shown.bs.tab once from:#my-data-tab'`: the card's first inner tab
loads when the My Data tab is shown (single card, no nested-subtab gymnastics —
simpler than the Status tab's first-subtab case).

**Visibility gate** — show the tab only when scans are enabled AND the user has a
unix_uid AND at least one scan-capable resource is warmed. In the user-dashboard
blueprint (`webapp/dashboards/user/blueprint.py`, the `/user` index handler that
renders `dashboard.html`):

```python
from webapp.disk_scans import service as disk_scans_service
...
my_data_available = bool(
    getattr(current_user, 'unix_uid', None) is not None
    and disk_scans_service.scan_capable_resources()
)
```
Pass `my_data_available` to `render_template`, and gate **both** the nav `<li>`
and the pane on it (`{% if my_data_available %}`), mirroring the Status tab's
`{% if fs_scan_resources and has_permission(...) %}` pattern. Plugin off →
`scan_capable_resources()` is `[]` → tab hidden (graceful degradation, the
constraint from PR #329).

> **Note on multiple resources:** the user view is *not* per-resource-subtab'd —
> "My Data" routes take no `resource`. If "my files across **each** filesystem"
> is wanted later, either add a resource subtab strip here (like the Status tab)
> with `mode='user'` cards that DO carry a resource, or have the user service
> fns aggregate across all `scan_capable_resources()`. v1: single card, queried
> over whichever collection(s) the resource fns default to — **decide and
> document which resource(s) v1 covers** (likely Campaign_Store, matching
> `FS_SCAN_RESOURCES`). If v1 must cover all warmed resources in one card, that
> aggregation is the one place a small service addition (loop the resource fns,
> merge histograms/dir rows) may be warranted — still SAM-side, no plugin change.

---

## Step 5 — Tests

`tests/unit/test_webapp_disk_scans.py`:
- **Security (critical):** as a logged-in non-admin user A, GET each user
  fragment with `?owner_uid=<B>` and `?owner_user_id=<B's user_id>` → assert the
  scan was called with A's unix_uid, never B's. (Spy on the `*_resource` service
  fns / fake module, as the existing `_wire_resource_*` helpers do.)
- Each user route is reachable by a plain `auth_client` **without**
  `VIEW_ALL_FILESYSTEM_DATA` (contrast the resource routes' 403 test).
- `forced_owner_uid is None` (user with no unix_uid) → empty/disabled state, no
  scan call.
- Entities route is NOT exposed in user mode (no `entities_user_fragment`), and
  the card omits the tab.

`tests/integration/test_status_dashboard.py` / a user-dashboard test:
- "My Data" tab renders when `my_data_available` (monkeypatch
  `scan_capable_resources` → `['Campaign_Store']`, ensure the test user has a
  unix_uid — `benkirk` is preserved in the obfuscated DB, see
  `project_test_db_fixtures`); absent when plugin off or user has no unix_uid.

---

## Key files

| File | Change |
|---|---|
| `src/webapp/disk_scans/service.py` | **none** (reuse `*_resource(…, owner_uid=…)`); possibly an all-resource aggregator if v1 spans resources |
| `src/webapp/disk_scans/routes.py` | `_user_ctx`; `forced_owner_uid` hardening in the 3 render helpers; 3 `@login_required` user fragments + `directories_user_page` |
| `templates/.../user/partials/disk_scans_card.html` | `mode='user'` URL branch; hide entities tab |
| `templates/.../user/disk_scans_directories_page.html` | `mode='user'` branch — hide owner picker, pin owner |
| `templates/.../user/dashboard.html` | "My Data" nav-tab + pane (gated on `my_data_available`) |
| `src/webapp/dashboards/user/blueprint.py` | compute + pass `my_data_available` |
| `tests/unit/test_webapp_disk_scans.py` | own-data-only enforcement + login_required-only + empty state |
| `tests/integration/test_status_dashboard.py` (or user-dashboard test) | tab visibility |

## Gotchas

- **Owner pinning is server-side and overrides request args in all three render
  helpers** — this is the whole security story. Test it explicitly.
- **Route collision:** new static prefixes (`/user/…`) must not be shadowed by
  the existing `/<projcode>/…` converter routes; verify, or use `/me/…`.
- **No-unix_uid accounts** (admin/service): empty state, never an unfiltered scan.
- **Explorer page owner picker** must be hidden in user mode (else a user could
  re-filter to another owner there).
- **Webdev watch-sync:** after `webdev` restart, re-`touch` edited files to
  force `develop.watch` sync (no source bind-mount).
- **Semantics copy:** label it "files you own" — not "everything you can access"
  and not "your project allocation." Matches the admin drill-down's attribution.

## Verification

1. `source etc/config_env.sh && pytest tests/unit/test_webapp_disk_scans.py
   tests/integration/test_status_dashboard.py -v` then full `pytest` (~65s).
2. Manual (`docker compose up webdev --watch`, logged in as `benkirk`):
   - `/user` → "My Data" tab present; card lazy-loads on tab show; 3 tabs
     (Large directories / Access history / File sizes), NO entities tab.
   - Data is scoped to benkirk's owned files; band → directories drill stays
     user-scoped; "Open full view ↗" page is owner-pinned with no owner picker.
   - **Tamper test:** hit `…/disk-scans/user/directories?owner_uid=<other>` →
     still benkirk's data.
   - A Quick-Login user with no unix_uid: tab hidden (or empty state).
3. PR vs `staging` (`gh pr create --base staging`).
