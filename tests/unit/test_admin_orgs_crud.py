"""HTTP-layer characterization tests for the admin Organizations CRUD routes.

Scope (mirrors tests/unit/test_htmx_queue_admin.py): auth, permission,
validation-error re-render, not-found behavior, and render smoke for the
edit/create/delete quintet of every entity on the Organizations card.
Happy-path DB writes are deliberately not exercised here — route handlers
go through Flask-SQLAlchemy's `db.session`, which only sees committed
snapshot rows; write behavior is covered at the model layer.

Written BEFORE the CrudSpec registrar migration (WEBAPP_OO_REFACTOR
commit 2.2) to pin current behavior, including the deliberate not-found
asymmetry: edit-form GET returns a warning div at 200 (htmx swaps it into
the modal), while POST/DELETE return 404.
"""

import os

import pytest
from sqlalchemy import func, inspect as sa_inspect

from sam.core.organizations import Institution, InstitutionType, Organization
from sam.projects.areas import AreaOfInterest, AreaOfInterestGroup
from sam.projects.contracts import Contract, ContractSource, NSFProgram

pytestmark = pytest.mark.unit

MISSING_ID = 99999999

#: (slug, model, display name, has_delete_route)
CASES = [
    ('organization', Organization, 'Organization', True),
    ('institution-type', InstitutionType, 'Institution type', False),
    ('institution', Institution, 'Institution', False),
    ('aoi-group', AreaOfInterestGroup, 'AOI group', True),
    ('aoi', AreaOfInterest, 'Area of interest', True),
    ('contract-source', ContractSource, 'Contract source', True),
    ('contract', Contract, 'Contract', True),   # delete is bespoke (end_date)
    ('nsf-program', NSFProgram, 'NSF program', True),
]

SLUGS = [c[0] for c in CASES]


def _snapshot_id(session, model):
    """Smallest committed primary-key value — visible to route handlers."""
    pk_col = sa_inspect(model).primary_key[0]
    value = session.query(func.min(pk_col)).scalar()
    assert value is not None, f'snapshot has no {model.__name__} rows'
    return value


@pytest.mark.parametrize('slug,model,name,has_delete', CASES, ids=SLUGS)
class TestOrgsCrudQuintet:

    def test_edit_form_renders(self, auth_client, session, slug, model, name,
                               has_delete):
        entity_id = _snapshot_id(session, model)
        resp = auth_client.get(f'/admin/htmx/{slug}-edit-form/{entity_id}')
        assert resp.status_code == 200
        assert 'form' in resp.get_data(as_text=True)

    def test_edit_form_missing_id_warns_at_200(self, auth_client, slug, model,
                                               name, has_delete):
        resp = auth_client.get(f'/admin/htmx/{slug}-edit-form/{MISSING_ID}')
        assert resp.status_code == 200
        assert f'{name} not found' in resp.get_data(as_text=True)

    def test_edit_post_missing_id_404s(self, auth_client, slug, model, name,
                                       has_delete):
        resp = auth_client.post(f'/admin/htmx/{slug}-edit/{MISSING_ID}', data={})
        assert resp.status_code == 404
        assert f'{name} not found' in resp.get_data(as_text=True)

    def test_edit_post_invalid_rerenders(self, auth_client, session, slug,
                                         model, name, has_delete):
        entity_id = _snapshot_id(session, model)
        resp = auth_client.post(f'/admin/htmx/{slug}-edit/{entity_id}', data={})
        assert resp.status_code == 200          # form re-render, not a 500
        assert 'HX-Trigger' not in resp.headers  # and not a success

    def test_create_form_renders(self, auth_client, slug, model, name,
                                 has_delete):
        resp = auth_client.get(f'/admin/htmx/{slug}-create-form')
        assert resp.status_code == 200

    def test_create_post_invalid_rerenders(self, auth_client, slug, model,
                                           name, has_delete):
        resp = auth_client.post(f'/admin/htmx/{slug}-create', data={})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers

    def test_delete_missing_id_404s(self, auth_client, slug, model, name,
                                    has_delete):
        if not has_delete:
            pytest.skip('entity has no delete route')
        resp = auth_client.delete(f'/admin/htmx/{slug}-delete/{MISSING_ID}')
        assert resp.status_code == 404

    def test_non_admin_forbidden(self, non_admin_client, slug, model, name,
                                 has_delete):
        assert non_admin_client.get(
            f'/admin/htmx/{slug}-edit-form/1').status_code == 403
        assert non_admin_client.post(
            f'/admin/htmx/{slug}-edit/1', data={}).status_code == 403
        assert non_admin_client.post(
            f'/admin/htmx/{slug}-create', data={}).status_code == 403
        if has_delete:
            assert non_admin_client.delete(
                f'/admin/htmx/{slug}-delete/1').status_code == 403

    def test_unauthenticated_rejected(self, client, slug, model, name,
                                      has_delete):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        resp = client.post(f'/admin/htmx/{slug}-edit/1', data={})
        assert resp.status_code in (302, 401)


class TestOrganizationMnemonicLinker:
    """The Organizations card gains the create-mnemonic linker Institutions has.

    Scoped to the Organizations tab pane (`orgs-pane`) because the card fragment
    also carries the Institutions tab, whose cell renders the same linker.
    """

    URL = '/admin/htmx/organizations-card'

    def _orgs_pane(self, client):
        body = client.get(self.URL).get_data(as_text=True)
        assert 'id="orgs-pane"' in body, 'the organizations card did not render'
        return body.split('id="orgs-pane"')[1].split('id="institutions-pane"')[0]

    def test_an_unmapped_org_offers_the_prefilled_create_linker(self, auth_client,
                                                                session):
        from sam.core.organizations import MnemonicCode, Organization
        # Confirm the premise: the snapshot has an org with no mnemonic match.
        lookup = MnemonicCode.build_lookup(session)
        unmapped = next((o for o in session.query(Organization).limit(400)
                         if MnemonicCode.resolve_for_organization(o, lookup) is None),
                        None)
        if unmapped is None:
            pytest.skip('snapshot has every organization mapped')
        pane = self._orgs_pane(auth_client)
        assert 'createMnemonicCodeModal' in pane
        # Prefilled from the org name (name-only, no "Name, City").
        assert 'mnemonic-code-create-form?description=' in pane


class TestMnemonicCodeStaysBespoke:
    """The mnemonic-code create route has DB-uniqueness checks and stays a
    hand-written handler — smoke it to make sure the migration around it
    doesn't disturb it."""

    def test_create_form_renders(self, auth_client):
        resp = auth_client.get('/admin/htmx/mnemonic-code-create-form')
        assert resp.status_code == 200

    def test_invalid_code_rerenders_with_error(self, auth_client):
        resp = auth_client.post('/admin/htmx/mnemonic-code-create',
                                data={'code': 'nope!', 'description': 'x'})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers   # not treated as success
        assert 'Code must be exactly 3 uppercase letters' in resp.text

    def test_duplicate_code_rerenders_with_error(self, auth_client, session):
        from sam.core.organizations import MnemonicCode
        existing = session.query(MnemonicCode).first()
        if existing is None:
            pytest.skip('snapshot has no mnemonic codes')
        resp = auth_client.post('/admin/htmx/mnemonic-code-create',
                                data={'code': existing.code,
                                      'description': 'brand new description'})
        assert resp.status_code == 200
        assert 'HX-Trigger' not in resp.headers
        assert f'Code &#34;{existing.code}&#34; already exists.' in resp.text
