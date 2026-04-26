"""Unit tests for CLI builder functions.

Builders extract data from ORM objects into plain dicts.  They are the
data layer underlying both `--format rich` and `--format json` output —
testing them in isolation guarantees both code paths see the same shape.
"""
import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from cli.core.output import _SAMEncoder, output_json
from cli.user.builders import (
    build_user_core,
    build_user_detail,
    build_user_projects,
    build_user_search_results,
    build_abandoned_users,
    build_users_with_projects,
)
from cli.project.builders import (
    build_project_core,
    build_project_detail,
    build_project_allocations,
    build_project_tree,
    build_project_users,
    build_project_search_results,
    build_expiring_projects,
)


pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# JSON encoder
# ----------------------------------------------------------------------

class TestSAMEncoder:

    def test_datetime_to_iso(self):
        out = json.dumps({'when': datetime(2026, 1, 2, 3, 4, 5)}, cls=_SAMEncoder)
        assert '"2026-01-02T03:04:05"' in out

    def test_date_to_iso(self):
        out = json.dumps({'when': date(2026, 1, 2)}, cls=_SAMEncoder)
        assert '"2026-01-02"' in out

    def test_decimal_to_float(self):
        out = json.dumps({'amount': Decimal('3.14')}, cls=_SAMEncoder)
        assert '3.14' in out

    def test_set_to_sorted_list(self):
        out = json.dumps({'tags': {'b', 'a', 'c'}}, cls=_SAMEncoder)
        assert json.loads(out) == {'tags': ['a', 'b', 'c']}

    def test_unknown_type_raises(self):
        class Weird:
            pass
        with pytest.raises(TypeError):
            json.dumps({'x': Weird()}, cls=_SAMEncoder)


# ----------------------------------------------------------------------
# User builders
# ----------------------------------------------------------------------

class TestUserBuilders:

    def test_build_user_core_keys(self, multi_project_user):
        data = build_user_core(multi_project_user)
        assert data['kind'] == 'user'
        expected = {
            'kind', 'username', 'display_name', 'user_id', 'upid', 'unix_uid',
            'active', 'locked', 'is_accessible', 'primary_email', 'emails',
            'active_project_count',
        }
        assert set(data.keys()) == expected
        assert data['username'] == multi_project_user.username
        assert isinstance(data['emails'], list)
        assert isinstance(data['active_project_count'], int)

    def test_build_user_core_emails_shape(self, multi_project_user):
        data = build_user_core(multi_project_user)
        for e in data['emails']:
            assert set(e.keys()) == {'address', 'is_primary'}

    def test_build_user_detail_keys(self, multi_project_user):
        data = build_user_detail(multi_project_user)
        assert set(data.keys()) == {'academic_status', 'institutions', 'organizations'}
        assert isinstance(data['institutions'], list)

    def test_build_user_projects_role_assignment(self, active_project):
        """Project lead's own project entry has role='Lead'."""
        lead = active_project.lead
        if lead is None:
            pytest.skip("active_project fixture has no lead")
        projects = build_user_projects(lead, inactive=False)
        match = [p for p in projects if p['projcode'] == active_project.projcode]
        assert len(match) == 1
        assert match[0]['role'] == 'Lead'

    def test_build_user_projects_keys(self, multi_project_user):
        projects = build_user_projects(multi_project_user, inactive=False)
        for p in projects:
            assert set(p.keys()) == {
                'projcode', 'title', 'role', 'active', 'latest_allocation_end',
            }
            assert p['role'] in {'Lead', 'Admin', 'Member'}

    def test_build_user_search_results(self, multi_project_user):
        data = build_user_search_results([multi_project_user], 'pattern')
        assert data['kind'] == 'user_search_results'
        assert data['count'] == 1
        assert data['pattern'] == 'pattern'
        assert data['users'][0]['username'] == multi_project_user.username

    def test_build_abandoned_users_sorted(self, multi_project_user):
        data = build_abandoned_users({multi_project_user}, total_active=10)
        assert data['kind'] == 'abandoned_users'
        assert data['total_active_users'] == 10
        assert data['count'] == 1
        usernames = [u['username'] for u in data['users']]
        assert usernames == sorted(usernames)

    def test_build_users_with_projects_includes_projects_when_asked(self, multi_project_user):
        with_p = build_users_with_projects({multi_project_user}, list_projects=True)
        without_p = build_users_with_projects({multi_project_user}, list_projects=False)
        assert 'projects' in with_p['users'][0]
        assert 'projects' not in without_p['users'][0]


# ----------------------------------------------------------------------
# Project builders
# ----------------------------------------------------------------------

class TestProjectBuilders:

    def test_build_project_core_keys(self, active_project):
        data = build_project_core(active_project)
        assert data['kind'] == 'project'
        expected = {
            'kind', 'projcode', 'title', 'unix_gid', 'active', 'charging_exempt',
            'allocation_type', 'panel', 'facility', 'lead', 'admin',
            'area_of_interest', 'organizations', 'contracts',
            'active_user_count', 'active_directories',
        }
        assert set(data.keys()) == expected
        assert data['projcode'] == active_project.projcode

    def test_build_project_core_lead_brief(self, active_project):
        data = build_project_core(active_project)
        if data['lead'] is not None:
            assert set(data['lead'].keys()) == {
                'username', 'display_name', 'primary_email',
            }

    def test_build_project_detail_keys(self, active_project):
        data = build_project_detail(active_project)
        assert set(data.keys()) == {
            'project_id', 'ext_alias', 'creation_time', 'modified_time',
            'membership_change_time', 'inactivate_time',
            'latest_allocation_end', 'abstract', 'pi_institutions',
        }

    def test_build_project_allocations_resource_entries(self, active_project):
        data = build_project_allocations(active_project)
        assert isinstance(data, dict)
        for resource_name, entry in data.items():
            assert {'allocated', 'used', 'remaining', 'percent_used'} <= entry.keys()

    def test_build_project_tree_marks_current(self, active_project):
        tree = build_project_tree(active_project)
        seen = []

        def walk(n):
            if n['is_current']:
                seen.append(n['projcode'])
            for c in n['children']:
                walk(c)
        walk(tree)
        assert seen == [active_project.projcode]

    def test_build_project_tree_node_keys(self, active_project):
        tree = build_project_tree(active_project)
        assert set(tree.keys()) == {
            'projcode', 'title', 'active', 'is_current', 'children',
        }

    def test_build_project_users_keys(self, active_project):
        users = build_project_users(active_project)
        for u in users:
            assert set(u.keys()) == {
                'username', 'display_name', 'primary_email', 'unix_uid',
                'inaccessible_resources',
            }
            assert isinstance(u['inaccessible_resources'], list)

    def test_build_project_search_results_brief(self, active_project):
        data = build_project_search_results([active_project], 'pat', verbose=False)
        assert data['kind'] == 'project_search_results'
        assert data['count'] == 1
        # Brief mode: no project_id / lead / active_user_count
        assert 'project_id' not in data['projects'][0]
        assert set(data['projects'][0].keys()) == {'projcode', 'title', 'active'}

    def test_build_project_search_results_verbose(self, active_project):
        data = build_project_search_results([active_project], 'pat', verbose=True)
        entry = data['projects'][0]
        assert 'project_id' in entry
        assert 'lead' in entry
        assert 'active_user_count' in entry

    def test_build_expiring_projects_envelope(self, active_project):
        # Synthesize one tuple in the (project, allocation, resource_name, days) shape
        if not active_project.accounts:
            pytest.skip("active_project has no accounts")
        alloc = next(
            (a for acc in active_project.accounts for a in acc.allocations),
            None
        )
        if alloc is None:
            pytest.skip("active_project has no allocations")
        rows = [(active_project, alloc, 'Derecho', 7)]
        data = build_expiring_projects(rows, upcoming=True)
        assert data['kind'] == 'expiring_projects'
        assert data['count'] == 1
        assert data['rows'][0]['projcode'] == active_project.projcode
        assert data['rows'][0]['days'] == 7
        assert data['rows'][0]['resource'] == 'Derecho'

    def test_build_expiring_projects_recently_expired_kind(self, active_project):
        if not active_project.accounts:
            pytest.skip("active_project has no accounts")
        alloc = next(
            (a for acc in active_project.accounts for a in acc.allocations),
            None
        )
        if alloc is None:
            pytest.skip("active_project has no allocations")
        rows = [(active_project, alloc, 'Derecho', 30)]
        data = build_expiring_projects(rows, upcoming=False)
        assert data['kind'] == 'recently_expired_projects'


# ----------------------------------------------------------------------
# JSON-serializability of every builder payload
# ----------------------------------------------------------------------

class TestPayloadsAreJSONSerializable:
    """Every builder must produce a payload `output_json` can write."""

    def test_user_core_serializable(self, multi_project_user):
        json.dumps(build_user_core(multi_project_user), cls=_SAMEncoder)

    def test_user_detail_serializable(self, multi_project_user):
        json.dumps(build_user_detail(multi_project_user), cls=_SAMEncoder)

    def test_user_projects_serializable(self, multi_project_user):
        json.dumps(
            build_user_projects(multi_project_user, inactive=False),
            cls=_SAMEncoder,
        )

    def test_project_core_serializable(self, active_project):
        json.dumps(build_project_core(active_project), cls=_SAMEncoder)

    def test_project_detail_serializable(self, active_project):
        json.dumps(build_project_detail(active_project), cls=_SAMEncoder)

    def test_project_allocations_serializable(self, active_project):
        json.dumps(build_project_allocations(active_project), cls=_SAMEncoder)

    def test_project_tree_serializable(self, active_project):
        json.dumps(build_project_tree(active_project), cls=_SAMEncoder)

    def test_project_users_serializable(self, active_project):
        json.dumps(build_project_users(active_project), cls=_SAMEncoder)
