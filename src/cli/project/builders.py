"""Data extraction for project CLI output. No Rich, no I/O."""

from sam import Project
from sam.queries.rolling_usage import get_project_rolling_usage


def _user_brief(u) -> dict:
    if u is None:
        return None
    return {
        'username': u.username,
        'display_name': u.display_name,
        'primary_email': u.primary_email,
    }


def build_project_core(project: Project) -> dict:
    """Always-cheap project fields."""
    panel = project.allocation_type.panel if project.allocation_type else None
    return {
        'kind': 'project',
        'projcode': project.projcode,
        'title': project.title,
        'unix_gid': project.unix_gid,
        'active': project.active,
        'charging_exempt': project.charging_exempt,
        'allocation_type': (project.allocation_type.allocation_type
                            if project.allocation_type else None),
        'panel': panel.panel_name if panel else None,
        'facility': panel.facility.facility_name if panel and panel.facility else None,
        'lead': _user_brief(project.lead),
        'admin': _user_brief(project.admin) if project.admin else None,
        'area_of_interest': (project.area_of_interest.area_of_interest
                             if project.area_of_interest else None),
        'organizations': [
            {'name': po.organization.name, 'acronym': po.organization.acronym}
            for po in project.organizations
        ],
        'contracts': [
            {
                'source': pc.contract.contract_source.contract_source,
                'number': pc.contract.contract_number,
                'title': pc.contract.title,
            }
            for pc in project.contracts
        ],
        'active_user_count': project.get_user_count(),
        'active_directories': list(project.active_directories or []),
    }


def build_project_detail(project: Project) -> dict:
    """Verbose-only fields: IDs, timestamps, abstract, latest end date."""
    latest_end = None
    for account in project.accounts:
        for alloc in account.allocations:
            if alloc.end_date and (latest_end is None or alloc.end_date > latest_end):
                latest_end = alloc.end_date
    pi_insts = []
    if project.lead:
        for ui in project.lead.institutions:
            if ui.is_currently_active:
                pi_insts.append({
                    'name': ui.institution.name,
                    'acronym': ui.institution.acronym,
                })
    return {
        'project_id': project.project_id,
        'ext_alias': project.ext_alias,
        'creation_time': project.creation_time,
        'modified_time': project.modified_time,
        'membership_change_time': project.membership_change_time,
        'inactivate_time': project.inactivate_time,
        'latest_allocation_end': latest_end,
        'abstract': project.abstract,
        'pi_institutions': pi_insts,
    }


def build_project_allocations(project: Project) -> dict:
    """Wrap project.get_detailed_allocation_usage() (already a dict)."""
    return project.get_detailed_allocation_usage()


def build_project_rolling(session, projcode: str) -> dict:
    """30/90-day rolling usage. Expensive — verbose-only in Rich mode."""
    try:
        return get_project_rolling_usage(session, projcode)
    except Exception:
        return {}


def build_project_tree(project: Project) -> dict:
    """Recursive parent/children hierarchy with current-node marker."""
    root = project.get_root() if hasattr(project, 'get_root') else project
    current = project.projcode

    def node(p):
        return {
            'projcode': p.projcode,
            'title': p.title,
            'active': bool(getattr(p, 'active', True)),
            'is_current': p.projcode == current,
            'children': [
                node(c) for c in sorted(p.get_children(), key=lambda x: x.projcode)
            ],
        }
    return node(root)


def build_project_users(project: Project) -> list:
    out = []
    for u in sorted(project.users, key=lambda x: x.username):
        inaccessible = project.get_user_inaccessible_resources(u)
        out.append({
            'username': u.username,
            'display_name': u.display_name,
            'primary_email': u.primary_email,
            'unix_uid': u.unix_uid,
            'inaccessible_resources': sorted(inaccessible) if inaccessible else [],
        })
    return out


def build_project_search_results(projects: list, pattern: str, verbose: bool) -> dict:
    """Wrap pattern-search results."""
    out = {
        'kind': 'project_search_results',
        'pattern': pattern,
        'count': len(projects),
        'projects': [],
    }
    for p in projects:
        entry = {
            'projcode': p.projcode,
            'title': p.title,
            'active': p.active,
        }
        if verbose:
            entry['project_id'] = p.project_id
            entry['lead'] = _user_brief(p.lead) if p.lead else None
            entry['active_user_count'] = p.get_user_count()
        out['projects'].append(entry)
    return out


def build_expiring_projects(rows: list, upcoming: bool) -> dict:
    """Wrap (project, allocation, resource_name, days) tuples."""
    return {
        'kind': 'expiring_projects' if upcoming else 'recently_expired_projects',
        'count': len(rows),
        'rows': [
            {
                'projcode': p.projcode,
                'title': p.title,
                'resource': res_name,
                'allocation_end': alloc.end_date,
                'days': days,
            }
            for (p, alloc, res_name, days) in rows
        ],
    }
