"""Data extraction for user CLI output. No Rich, no I/O."""

from typing import Optional
from sam import User


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
    """List of projects for a user (active or all)."""
    projects = user.all_projects if inactive else user.active_projects()
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
            'latest_allocation_end': latest_end,
        })
    return out


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
