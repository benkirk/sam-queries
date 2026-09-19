"""Tests for the shell query surface (sam.queries.shells + User.set_login_shell)."""
import pytest

from sam.core.users import UserResourceShell
from sam.resources.resources import ResourceShell
from sam.queries.shells import (
    active_login_resources,
    get_allowable_shell_names,
    get_user_current_shell,
)

from factories import make_user
from factories.resources import make_resource, make_resource_type


pytestmark = pytest.mark.unit


def _hpc_resource(session, name_prefix='test_hpc'):
    """Build an active HPC resource. ResourceType may already exist in the
    snapshot (HPC/DAV are pre-seeded)."""
    from sam.resources.resources import ResourceType
    rt = session.query(ResourceType).filter_by(resource_type='HPC').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='HPC')
    return make_resource(session, resource_type=rt, resource_name=f'{name_prefix}_{id(rt)}')


def _dav_resource(session, name_prefix='test_dav'):
    from sam.resources.resources import ResourceType
    rt = session.query(ResourceType).filter_by(resource_type='DAV').first()
    if rt is None:
        rt = make_resource_type(session, resource_type='DAV')
    return make_resource(session, resource_type=rt, resource_name=f'{name_prefix}_{id(rt)}')


def _add_shell(session, resource, shell_name, path=None):
    sh = ResourceShell(
        resource_id=resource.resource_id,
        shell_name=shell_name,
        path=path or f'/bin/{shell_name}',
    )
    session.add(sh)
    session.flush()
    return sh


class TestGetAllowableShellNames:

    def test_union_of_catalog_across_login_resources(self, session):
        r1 = _hpc_resource(session)
        r2 = _dav_resource(session)
        _add_shell(session, r1, 'zshA1')
        _add_shell(session, r1, 'bashA1')
        _add_shell(session, r2, 'fishA2')
        names = get_allowable_shell_names(session)
        for n in ('zshA1', 'bashA1', 'fishA2'):
            assert n in names, f'{n} missing from {names}'

    def test_resources_without_catalog_do_not_block_others(self, session):
        # One resource with no catalog, one with a catalog — union should still
        # expose the catalog shell (regression for the snapshot-shape bug).
        _hpc_resource(session)  # no ResourceShells
        r2 = _hpc_resource(session, name_prefix='test_hpc_b')
        _add_shell(session, r2, 'xshB7')
        assert 'xshB7' in get_allowable_shell_names(session)


class TestGetUserCurrentShell:

    def test_returns_override_when_present(self, session):
        r = _hpc_resource(session)
        sh = _add_shell(session, r, 'zshC3')
        u = make_user(session)
        session.add(UserResourceShell(user_id=u.user_id, resource_shell_id=sh.resource_shell_id))
        session.flush()
        assert get_user_current_shell(session, u) == 'zshC3'

    def test_falls_back_to_resource_default_when_no_overrides(self, session):
        r = _hpc_resource(session)
        sh = _add_shell(session, r, 'bashD4')
        r.default_resource_shell_id = sh.resource_shell_id
        session.flush()
        u = make_user(session)
        # No overrides for this user on this new resource; the default on r
        # contributes 'bashD4' to the tally.
        result = get_user_current_shell(session, u)
        assert result is not None  # resource default is present


class TestSetLoginShell:

    def test_creates_row_for_each_resource_offering_the_shell(self, session):
        r1 = _hpc_resource(session)
        r2 = _hpc_resource(session, name_prefix='test_hpc_2')
        _add_shell(session, r1, 'tcshE5')
        _add_shell(session, r2, 'tcshE5')
        u = make_user(session)
        u.set_login_shell('tcshE5')
        # One row per resource that offers the shell.
        shell_names = [urs.resource_shell.shell_name for urs in u.resource_shells]
        assert shell_names.count('tcshE5') == 2

    def test_skips_resources_without_the_shell(self, session):
        r1 = _hpc_resource(session)
        r2 = _hpc_resource(session, name_prefix='test_hpc_3')
        _add_shell(session, r1, 'zshF6')   # only on r1
        u = make_user(session)
        u.set_login_shell('zshF6')
        resource_ids = {urs.resource_shell.resource_id for urs in u.resource_shells}
        assert r1.resource_id in resource_ids
        assert r2.resource_id not in resource_ids

    def test_reuses_existing_row_on_resource(self, session):
        r = _hpc_resource(session)
        _add_shell(session, r, 'bashG7')
        _add_shell(session, r, 'zshG7')
        u = make_user(session)
        u.set_login_shell('bashG7')
        (first_id,) = {urs.user_resource_shell_id for urs in u.resource_shells}
        u.set_login_shell('zshG7')
        # Same row's resource_shell_id updated, not a new row
        row_ids = {urs.user_resource_shell_id for urs in u.resource_shells}
        assert row_ids == {first_id}
        assert next(iter(u.resource_shells)).resource_shell.shell_name == 'zshG7'

    def test_unknown_shell_raises(self, session):
        r = _hpc_resource(session)
        _add_shell(session, r, 'bashH8')
        u = make_user(session)
        with pytest.raises(ValueError):
            u.set_login_shell('nonexistent_xyz_shell')
