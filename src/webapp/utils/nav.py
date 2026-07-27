"""
Navigation registry — single source of truth for the site's navigation chrome.

Consumed by:
- the desktop navbar section dropdowns (templates/dashboards/base.html)
- the mobile offcanvas menu (templates/dashboards/fragments/mobile_nav.html)
- breadcrumbs (templates/dashboards/fragments/breadcrumbs.html)

The in-page ``page_tabs`` strips in each section's ``base_*.html`` keep their
own tab lists on purpose: they carry request-specific labels and visibility
(reservation counts, the impersonation-aware "X's Accounts" label,
``my_data_available``) that the global registry can't know cheaply.

Register in the app factory:
    app.context_processor(nav_context_processor)
"""

from flask import request
from flask_login import current_user

from webapp.utils.rbac import (
    Permission,
    has_permission,
    has_permission_any_facility,
)


# ── Visibility predicates (evaluated per request) ─────────────────────────

def _authenticated():
    return current_user.is_authenticated


def _can_view_projects():
    return (current_user.is_authenticated
            and has_permission_any_facility(current_user, Permission.VIEW_PROJECTS))


def _can_admin():
    return (current_user.is_authenticated
            and has_permission_any_facility(current_user, Permission.ACCESS_ADMIN_DASHBOARD))


def _can_view_config():
    return (current_user.is_authenticated
            and has_permission(current_user, Permission.VIEW_SYSTEM_CONFIG))


def _can_view_fs_scans():
    # Same gate as the status tab strip: permission AND at least one warmed
    # scan collection. scan_capable_resources() is a config-list +
    # in-memory lookup — cheap enough per request. Keeping the data check
    # here preserves the established contract (pinned by
    # test_fs_scans_tab_hidden_when_no_resources): when the plugin is off
    # or unwarmed, no fs-scans link appears anywhere on the page.
    if not (current_user.is_authenticated
            and has_permission(current_user, Permission.VIEW_ALL_FILESYSTEM_DATA)):
        return False
    from webapp.disk_scans import service as disk_scans_service
    return bool(disk_scans_service.scan_capable_resources())


def _can_view_job_history():
    # Same gate as the status tab strip: permission AND at least one warmed
    # job-history engine. job_history_machines() is an in-memory extension
    # lookup — cheap per request. When the plugin is off, no job-history
    # link appears anywhere on the page.
    if not (current_user.is_authenticated
            and has_permission(current_user, Permission.VIEW_ALL_JOB_DATA)):
        return False
    from webapp.jobs import service as jobs_service
    return bool(jobs_service.job_history_machines())


def _my_data_available():
    # Mirrors user_dashboard._page_context(): needs a filesystem identity and
    # at least one warmed scan collection. scan_capable_resources() is a
    # config-list + in-memory collection lookup — cheap enough per request.
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, 'unix_uid', None) is None:
        return False
    from webapp.disk_scans import service as disk_scans_service
    return bool(disk_scans_service.scan_capable_resources())


def _my_jobs_available():
    # Mirrors user_dashboard._page_context(): any authenticated user (the
    # job routes pin the username server-side); hidden only when no
    # job-history machine engine is warmed.
    if not current_user.is_authenticated:
        return False
    from webapp.jobs import service as jobs_service
    return bool(jobs_service.job_history_machines())


# ── The registry ──────────────────────────────────────────────────────────
#
# Section keys: 'blueprint' drives section-level active state; 'endpoint' is
# the section's default page (what the navbar label links to). Item keys:
# 'active_endpoints' lists sub-pages that count as "on this item" (same idea
# as the page_tabs macro). 'visible' callables gate rendering; omitted means
# always visible (within a visible section).

NAV_SECTIONS = (
    {
        'key': 'user',
        'label': 'User Dashboard',
        'blueprint': 'user_dashboard',
        'endpoint': 'user_dashboard.accounts',
        'visible': _authenticated,
        'items': (
            {'endpoint': 'user_dashboard.accounts', 'label': 'My Accounts',
             'icon': 'fas fa-file-invoice-dollar'},
            {'endpoint': 'user_dashboard.info', 'label': 'User Information',
             'icon': 'fas fa-user-circle'},
            {'endpoint': 'user_dashboard.my_data', 'label': 'My Data',
             'icon': 'fas fa-hard-drive', 'visible': _my_data_available},
            {'endpoint': 'user_dashboard.my_jobs', 'label': 'My Jobs',
             'icon': 'fas fa-list-check', 'visible': _my_jobs_available},
        ),
    },
    {
        'key': 'status',
        'label': 'System Status',
        'blueprint': 'status_dashboard',
        'endpoint': 'status_dashboard.derecho',
        'items': (
            {'endpoint': 'status_dashboard.derecho', 'label': 'Derecho',
             'icon': 'fas fa-server'},
            {'endpoint': 'status_dashboard.casper', 'label': 'Casper',
             'icon': 'fas fa-hdd'},
            {'endpoint': 'status_dashboard.jupyterhub', 'label': 'JupyterHub',
             'icon': 'fas fa-book'},
            {'endpoint': 'status_dashboard.reservations', 'label': 'Reservations',
             'icon': 'fas fa-calendar-alt'},
            {'endpoint': 'status_dashboard.filesystem_scans', 'label': 'Filesystem Scans',
             'icon': 'fas fa-magnifying-glass-chart', 'visible': _can_view_fs_scans},
            {'endpoint': 'status_dashboard.job_history', 'label': 'Job History',
             'icon': 'fas fa-list-check', 'visible': _can_view_job_history},
        ),
    },
    {
        'key': 'allocations',
        'label': 'Allocations',
        'blueprint': 'allocations_dashboard',
        'endpoint': 'allocations_dashboard.projects',
        'visible': _can_view_projects,
        'items': (
            {'endpoint': 'allocations_dashboard.projects', 'label': 'Projects',
             'icon': 'fas fa-folder-open'},
            {'endpoint': 'allocations_dashboard.transactions', 'label': 'Transactions',
             'icon': 'fas fa-exchange-alt'},
            {'endpoint': 'allocations_dashboard.adjustments', 'label': 'Adjustments',
             'icon': 'fas fa-balance-scale'},
        ),
    },
    {
        'key': 'admin',
        'label': 'Admin',
        'blueprint': 'admin_dashboard',
        'endpoint': 'admin_dashboard.projects',
        'visible': _can_admin,
        'items': (
            {'endpoint': 'admin_dashboard.projects', 'label': 'Projects',
             'icon': 'fas fa-folder-open'},
            {'endpoint': 'admin_dashboard.projects_directories', 'label': 'Project Directories',
             'icon': 'fas fa-folder-tree'},
            {'endpoint': 'admin_dashboard.users_groups', 'label': 'Users & Groups',
             'icon': 'fas fa-users'},
            {'endpoint': 'admin_dashboard.resources', 'label': 'Resources',
             'icon': 'fas fa-server'},
            {'endpoint': 'admin_dashboard.organizations', 'label': 'Organizations',
             'icon': 'fas fa-sitemap'},
            {'endpoint': 'admin_dashboard.facilities', 'label': 'Facilities & Allocation Types',
             'icon': 'fas fa-building'},
            {'endpoint': 'admin_dashboard.configuration', 'label': 'Configuration',
             'icon': 'fas fa-sliders-h', 'visible': _can_view_config},
        ),
    },
)


def _item_active(item, endpoint):
    return (endpoint == item['endpoint']
            or endpoint in item.get('active_endpoints', ()))


def resolve_nav_sections():
    """Per-request view of the registry: visibility applied, active flags set.

    Returns plain dicts safe to iterate in templates. Sections and items the
    current user can't see are omitted entirely.
    """
    endpoint = request.endpoint or ''
    sections = []
    for s in NAV_SECTIONS:
        visible = s.get('visible')
        if visible is not None and not visible():
            continue
        items = [
            {
                'endpoint': i['endpoint'],
                'label': i['label'],
                'icon': i.get('icon'),
                'active': _item_active(i, endpoint),
            }
            for i in s['items']
            if i.get('visible') is None or i['visible']()
        ]
        # Resolved key is 'pages', not 'items' — in Jinja, ``section.items``
        # resolves to dict.items (the method), a classic footgun.
        sections.append({
            'key': s['key'],
            'label': s['label'],
            'endpoint': s['endpoint'],
            'active': request.blueprint == s['blueprint'],
            'pages': items,
        })
    return sections


def nav_locate(endpoint):
    """Map an endpoint to its ``(section, item)`` registry entries.

    Used by breadcrumbs: a page the user is currently viewing is by
    definition reachable, so NO visibility predicates are applied. Either
    element may be None — e.g. a detail route inside a known blueprint
    (``admin_dashboard.user_card``) locates the section but no item.
    """
    if not endpoint:
        return None, None
    blueprint = endpoint.rsplit('.', 1)[0] if '.' in endpoint else None
    for s in NAV_SECTIONS:
        for i in s['items']:
            if _item_active(i, endpoint):
                return s, i
    for s in NAV_SECTIONS:
        if s['blueprint'] == blueprint:
            return s, None
    return None, None


def nav_context_processor():
    """Template context: ``nav_sections()`` (callable) and ``nav_locate``."""
    return {
        'nav_sections': resolve_nav_sections,
        'nav_locate': nav_locate,
    }
