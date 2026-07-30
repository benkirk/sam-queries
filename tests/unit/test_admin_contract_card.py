"""Contract detail card, Search Contracts page, and the cross-entity links.

Scope follows the house convention (docs/TESTING.md): auth, permission,
not-found behaviour, and render smoke at the HTTP layer. These routes are
all read-only, so there is no write path to cover at the model layer.

The linking assertions are deliberately here rather than left to the browser
smoke: a modal opener is five attributes that must agree with a shell
declared in a different file, and getting one wrong fails silently at
runtime (htmx:targetError, or a Bootstrap toggle that closes its host).
"""

import os

import pytest

from sam.projects.contracts import Contract, NSFProgram

pytestmark = pytest.mark.unit

CARD_URL = '/admin/contract/{}'
SEARCH_URL = '/admin/htmx/search/contracts'
PAGE_URL = '/admin/contracts'
PROGRAM_CONTRACTS_URL = '/admin/nsf-program/{}/contracts'
ORG_CARD_URL = '/admin/htmx/organizations-card'

MISSING_ID = 99999999


def _contract_with_projects(session):
    """A committed contract that has at least one linked project."""
    from sam.projects.contracts import ProjectContract
    row = (
        session.query(Contract)
        .join(ProjectContract, ProjectContract.contract_id == Contract.contract_id)
        .order_by(Contract.contract_id)
        .first()
    )
    if row is None:
        pytest.skip('snapshot has no contract with a linked project')
    return row


class TestContractCardAuth:

    def test_unauthenticated_rejected(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        assert client.get(CARD_URL.format(1)).status_code in (302, 401)

    def test_non_admin_forbidden(self, non_admin_client):
        assert non_admin_client.get(CARD_URL.format(1)).status_code == 403


class TestContractCard:

    def test_renders_the_contract(self, auth_client, session, any_contract):
        resp = auth_client.get(CARD_URL.format(any_contract.contract_id))
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert any_contract.contract_number in body
        assert 'Linked Projects' in body

    def test_shows_people_and_program(self, auth_client, session):
        """Pick a row that actually has all three, so the assertion means something."""
        contract = (
            session.query(Contract)
            .filter(Contract.contract_monitor_user_id.isnot(None),
                    Contract.nsf_program_id.isnot(None))
            .order_by(Contract.contract_id)
            .first()
        )
        if contract is None:
            pytest.skip('snapshot has no contract with both a monitor and a program')

        body = auth_client.get(
            CARD_URL.format(contract.contract_id)).get_data(as_text=True)
        assert contract.principal_investigator.display_name in body
        assert contract.contract_monitor.display_name in body
        assert contract.nsf_program.nsf_program_name in body

    def test_missing_id_warns_at_200(self, auth_client):
        """Matches the user_card / project_card / group_card convention —
        htmx swaps the warning into the card region rather than erroring."""
        resp = auth_client.get(CARD_URL.format(MISSING_ID))
        assert resp.status_code == 200
        assert 'Contract not found' in resp.get_data(as_text=True)

    def test_linked_projects_table_lists_them(self, auth_client, session):
        contract = _contract_with_projects(session)
        body = auth_client.get(
            CARD_URL.format(contract.contract_id)).get_data(as_text=True)
        for pc in contract.projects:
            assert pc.project.projcode in body

    def test_contract_without_projects_shows_empty_state(self, auth_client,
                                                          session):
        from sam.projects.contracts import ProjectContract
        contract = (
            session.query(Contract)
            .outerjoin(ProjectContract,
                       ProjectContract.contract_id == Contract.contract_id)
            .filter(ProjectContract.contract_id.is_(None))
            .first()
        )
        if contract is None:
            pytest.skip('every snapshot contract has a linked project')
        body = auth_client.get(
            CARD_URL.format(contract.contract_id)).get_data(as_text=True)
        assert 'No projects are linked' in body

    def test_cross_entity_links_use_the_stacking_action(self, auth_client,
                                                        session):
        """Bootstrap's toggle would close a hosting modal instead of stacking."""
        contract = _contract_with_projects(session)
        body = auth_client.get(
            CARD_URL.format(contract.contract_id)).get_data(as_text=True)
        assert 'data-action="show-detail-modal"' in body
        assert 'data-modal-id="projectDetailsModal"' in body
        assert 'projectDetailsModalBody' in body


class TestContractSearch:

    def test_non_admin_forbidden(self, non_admin_client):
        assert non_admin_client.get(SEARCH_URL).status_code == 403

    def test_short_query_returns_nothing(self, auth_client):
        assert auth_client.get(SEARCH_URL,
                               query_string={'q': 'a'}).get_data(as_text=True) == ''

    def test_match_links_to_the_card(self, auth_client, any_contract):
        resp = auth_client.get(SEARCH_URL,
                               query_string={'q': any_contract.contract_number})
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert any_contract.contract_number in body
        assert CARD_URL.format(any_contract.contract_id) in body
        assert 'contractCardContainer' in body
        # form-helpers.js clears the box and scrolls the card into view.
        assert 'data-clear-results="contractSearchResults"' in body

    def test_title_is_searchable(self, auth_client, session):
        contract = session.query(Contract).filter(
            Contract.title.isnot(None)).order_by(Contract.contract_id).first()
        token = contract.title.split()[0]
        if len(token) < 2:
            pytest.skip('first title token too short to search')
        body = auth_client.get(SEARCH_URL,
                               query_string={'q': token}).get_data(as_text=True)
        assert 'No contracts found' not in body

    def test_active_only_filters_and_its_absence_does_not(self, auth_client,
                                                          session):
        """Absent means OFF (§10) — expired grants stay searchable."""
        expired = (
            session.query(Contract)
            .filter(~Contract.is_active)
            .order_by(Contract.contract_id)
            .first()
        )
        if expired is None:
            pytest.skip('snapshot has no inactive contract')

        q = {'q': expired.contract_number}
        assert expired.contract_number in auth_client.get(
            SEARCH_URL, query_string=q).get_data(as_text=True)
        assert expired.contract_number not in auth_client.get(
            SEARCH_URL, query_string=dict(q, active_only='1')).get_data(as_text=True)

    def test_no_match_renders_empty_state(self, auth_client):
        body = auth_client.get(
            SEARCH_URL, query_string={'q': 'zzzz-no-such-contract'}
        ).get_data(as_text=True)
        assert 'No contracts found' in body


class TestContractsPage:

    def test_unauthenticated_rejected(self, client):
        if os.getenv('DISABLE_AUTH') == '1':
            pytest.skip('Auth disabled in dev environment')
        assert client.get(PAGE_URL).status_code in (302, 401)

    def test_renders_search_box_and_card_region(self, auth_client):
        body = auth_client.get(PAGE_URL).get_data(as_text=True)
        assert 'contractSearchInput' in body
        assert 'contractCardContainer' in body

    def test_reload_url_has_no_placeholder_id(self, auth_client):
        """The idiom concatenates an id onto the base; an <int:> converter
        rejects the empty string the other pages use, so it is stripped."""
        body = auth_client.get(PAGE_URL).get_data(as_text=True)
        assert 'data-reload-url="/admin/contract/"' in body

    def test_tab_and_nav_both_list_it(self, auth_client):
        """Two registries — page_tabs and NAV_SECTIONS. Missing either
        leaves the page reachable only by typing the URL."""
        body = auth_client.get(PAGE_URL).get_data(as_text=True)
        assert body.count('href="/admin/contracts"') >= 2

    def test_active_only_toggle_defaults_off(self, auth_client):
        body = auth_client.get(PAGE_URL).get_data(as_text=True)
        toggle = body.split('id="activeContractsOnly"')[1][:200]
        assert 'checked' not in toggle


class TestTableLinking:
    """A modal opener is five attributes that must agree with a shell in a
    different file; getting one wrong fails silently at runtime."""

    def test_org_card_links_contract_numbers(self, auth_client):
        body = auth_client.get(ORG_CARD_URL).get_data(as_text=True)
        assert 'data-modal-id="contractDetailsModal"' in body
        assert 'contractDetailsModalBody' in body

    def test_org_card_links_pi_and_monitor_to_the_user_modal(self, auth_client):
        """These were the one place in the app a username was not clickable."""
        body = auth_client.get(ORG_CARD_URL).get_data(as_text=True)
        assert 'data-action="show-user-details"' in body
        assert 'userDetailsModalBody' in body

    def test_org_card_links_nsf_programs_and_their_counts(self, auth_client):
        body = auth_client.get(ORG_CARD_URL).get_data(as_text=True)
        assert 'data-modal-id="nsfProgramContractsModal"' in body
        assert 'nsfProgramContractsModalBody' in body

    def test_contract_shell_rides_along_with_the_project_shell(self, auth_client):
        """One include covers base_admin / base_user / base_allocations /
        base_status and the one-off pages — the same trick
        project_details_modal.html already uses for allocation_modals."""
        body = auth_client.get(PAGE_URL).get_data(as_text=True)
        assert 'id="contractDetailsModal"' in body
        assert 'id="nsfProgramContractsModal"' in body

    def test_project_card_link_needs_view_org_metadata(self, auth_client,
                                                       session, monkeypatch):
        """The project card renders on the user dashboard, where a normal
        user has no VIEW_ORG_METADATA and contract_card would 403. With the
        permission the number is a link; without it, plain text.

        Every admin-ish Quick Login user happens to hold VIEW_ORG_METADATA,
        so the negative branch is only reachable by stubbing it.
        """
        from sam.projects.contracts import ProjectContract
        pc = (session.query(ProjectContract)
              .order_by(ProjectContract.project_contract_id).first())
        if pc is None:
            pytest.skip('snapshot has no project-contract link')
        url = f'/user/project-details-modal/{pc.project.projcode}'

        granted = auth_client.get(url)
        if granted.status_code != 200:
            pytest.skip('test user cannot view that project')
        assert 'data-modal-id="contractDetailsModal"' in granted.get_data(as_text=True)

        import webapp.utils.rbac as rbac
        real = rbac.has_permission_any_facility

        def _deny(user, permission, *a, **kw):
            if permission == rbac.Permission.VIEW_ORG_METADATA:
                return False
            return real(user, permission, *a, **kw)

        monkeypatch.setattr(rbac, 'has_permission_any_facility', _deny)
        body = auth_client.get(url).get_data(as_text=True)
        assert 'data-modal-id="contractDetailsModal"' not in body
        # …and the number is still shown, just not clickable.
        assert pc.contract.contract_number in body

    def test_shells_reach_pages_that_never_mention_contracts(self, auth_client):
        """/admin/users-groups has no contract content of its own, but it
        renders project cards, so it must carry the shell."""
        body = auth_client.get('/admin/users-groups').get_data(as_text=True)
        assert 'id="contractDetailsModal"' in body


class TestNsfProgramContracts:

    def _program_with_contracts(self, session):
        program = (
            session.query(NSFProgram)
            .join(Contract, Contract.nsf_program_id == NSFProgram.nsf_program_id)
            .order_by(NSFProgram.nsf_program_id)
            .first()
        )
        if program is None:
            pytest.skip('snapshot has no NSF program with contracts')
        return program

    def test_non_admin_forbidden(self, non_admin_client):
        assert non_admin_client.get(
            PROGRAM_CONTRACTS_URL.format(1)).status_code == 403

    def test_lists_the_programs_contracts(self, auth_client, session):
        program = self._program_with_contracts(session)
        resp = auth_client.get(
            PROGRAM_CONTRACTS_URL.format(program.nsf_program_id))
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert program.nsf_program_name in body
        assert 'data-modal-id="contractDetailsModal"' in body

    def test_sets_the_title_out_of_band(self, auth_client, session):
        """One shell serves every program, so the title must arrive with the body."""
        program = self._program_with_contracts(session)
        body = auth_client.get(
            PROGRAM_CONTRACTS_URL.format(program.nsf_program_id)
        ).get_data(as_text=True)
        assert 'hx-swap-oob="true"' in body
        assert 'nsfProgramContractsModalTitle' in body

    def test_program_without_contracts_shows_empty_state(self, auth_client,
                                                          session):
        orphan = (
            session.query(NSFProgram)
            .outerjoin(Contract,
                       Contract.nsf_program_id == NSFProgram.nsf_program_id)
            .filter(Contract.nsf_program_id.is_(None))
            .first()
        )
        if orphan is None:
            pytest.skip('every snapshot NSF program has contracts')
        body = auth_client.get(
            PROGRAM_CONTRACTS_URL.format(orphan.nsf_program_id)
        ).get_data(as_text=True)
        assert 'No contracts reference this program' in body

    def test_missing_id_warns_at_200(self, auth_client):
        resp = auth_client.get(PROGRAM_CONTRACTS_URL.format(MISSING_ID))
        assert resp.status_code == 200
        assert 'NSF program not found' in resp.get_data(as_text=True)
