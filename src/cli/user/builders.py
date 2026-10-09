"""Data extraction for user CLI output. No Rich, no I/O."""

from datetime import datetime
from typing import Optional
from sam import User
from sam.provisioning import check_user_provisioning


def build_user_core(user: User) -> dict:
    """Always-cheap user fields."""
    return {
        'kind': 'user',
        'username': user.username,
        'display_name': user.display_name,
        'user_id': user.user_id,
        'upid': user.upid,
        'unix_uid': user.unix_uid,
        'active': user.active,
        'locked': user.locked,
        'is_accessible': user.is_accessible,
        'primary_email': user.primary_email,
        'emails': [
            {'address': e.email_address, 'is_primary': e.is_primary}
            for e in user.email_addresses
        ],
        'active_project_count': len(user.active_projects()),
    }


def build_user_detail(user: User) -> dict:
    """Verbose-only fields: institutions, organizations, academic status."""
    return {
        'academic_status': (
            user.academic_status.description if user.academic_status else None
        ),
        'institutions': [
            {'name': ui.institution.name, 'acronym': ui.institution.acronym}
            for ui in user.institutions if ui.is_currently_active
        ],
        'organizations': [
            {'name': uo.organization.name, 'acronym': uo.organization.acronym}
            for uo in user.organizations if uo.is_currently_active
        ],
    }


def build_user_projects(user: User, inactive: bool) -> list:
    """List of projects for a user (active or all).

    Each row carries both the project's own active flag (``active``) and
    whether *this user's* membership window is still open
    (``membership_active``). The two diverge for someone who has been
    removed from a still-active project: the project reads Active but the
    membership has ended. Only the ``inactive`` path (``all_projects``)
    surfaces such rows, since ``active_projects()`` already filters on the
    membership window — so without ``membership_active`` the "All projects"
    listing would render an ex-member's rows as "Active" and contradict the
    "Active Projects: 0" count and the web UI.
    """
    projects = user.all_projects if inactive else user.active_projects()

    # Projcodes where this user's membership window is currently open.
    now = datetime.now()
    active_membership_codes = {
        au.account.project.projcode
        for au in user.accounts
        if au.account and au.account.project
        and (au.end_date is None or au.end_date >= now)
    }

    out = []
    for p in projects:
        if p.lead == user:
            role = 'Lead'
        elif p.admin == user:
            role = 'Admin'
        else:
            role = 'Member'
        latest_end = None
        for account in p.accounts:
            for alloc in account.allocations:
                if alloc.end_date and (latest_end is None or alloc.end_date > latest_end):
                    latest_end = alloc.end_date
        out.append({
            'projcode': p.projcode,
            'title': p.title,
            'role': role,
            'active': p.active,
            'membership_active': p.projcode in active_membership_codes,
            'latest_allocation_end': latest_end,
        })
    return out


def build_user_provisioning(user: User) -> dict:
    """Host provisioning cross-check for a user.

    Returns the dict from ``check_user_provisioning`` (recognized?, uid match,
    shell/home, project-group coverage). Callers gate on
    ``ctx.check_provisioning`` before invoking this — off-host it reads as
    ``recognized: False``, which the display renders as "unavailable here".
    """
    return check_user_provisioning(user, user.active_projects())


def build_user_search_results(users: list, pattern: str) -> dict:
    return {
        'kind': 'user_search_results',
        'pattern': pattern,
        'count': len(users),
        'users': [
            {
                'user_id': u.user_id,
                'username': u.username,
                'display_name': u.display_name,
                'primary_email': u.primary_email,
                'is_accessible': u.is_accessible,
            }
            for u in users
        ],
    }


def build_abandoned_users(abandoned: set, total_active: int) -> dict:
    return {
        'kind': 'abandoned_users',
        'total_active_users': total_active,
        'count': len(abandoned),
        'users': [
            {
                'username': u.username,
                'display_name': u.display_name,
                'primary_email': u.primary_email,
            }
            for u in sorted(abandoned, key=lambda x: x.username)
        ],
    }


def build_users_with_projects(users: set, list_projects: bool) -> dict:
    out = {
        'kind': 'users_with_active_projects',
        'count': len(users),
        'users': [],
    }
    for u in sorted(users, key=lambda x: x.username):
        entry = {
            'username': u.username,
            'display_name': u.display_name,
            'primary_email': u.primary_email,
        }
        if list_projects:
            entry['projects'] = build_user_projects(u, inactive=False)
        out['users'].append(entry)
    return out


def build_not_seen_users(rows, projects: dict, emails: dict, *, spec: str, cutoff: datetime,
                         source, abandoned: bool, total_considered: int,
                         active_only: bool) -> dict:
    """``--not-seen-since`` envelope; ``rows`` are ``ReviewRow`` (never-seen first, then oldest)."""
    return {
        'kind': 'users_not_seen',
        'since': spec,
        'cutoff': cutoff,
        'source': source,
        'abandoned': abandoned,
        'active_only': active_only,
        'total_considered': total_considered,
        'count': len(rows),
        'users': [
            {
                'username': r.username,
                'display_name': r.name,
                'status': r.status,
                'last_seen': r.last_seen,
                'source': r.kind,
                'system': r.system,
                'active_project_count': projects.get(r.username, 0),
                'primary_email': emails.get(r.username),
            }
            for r in rows
        ],
    }


def build_deactivation_restore(report, dry_run: bool) -> dict:
    """``sam.manage.lifecycle.RestoreReport`` as the deactivation-undo envelope."""
    return {
        'kind': 'deactivation_restore',
        'username': report.username,
        'closed_at': report.instant.isoformat() if report.instant else None,
        'dry_run': dry_run,
        'restored': 0 if dry_run else report.restored,
        'rows': [{
            'account_user_id': row.account_user_id,
            'projcode': row.account.project.projcode,
            'resource': row.account.resource.resource_name,
            'start_date': row.start_date.isoformat(),
            'outcome': outcome,
        } for row, outcome in report.outcomes],
    }
