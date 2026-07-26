"""Every page that ships the project-details modal must also ship the
allocation-edit modal it links to.

The project-details modal body renders a per-allocation edit pencil
(``user/partials/project_card.html`` → ``render_project_resources``) whose
``data-bs-target``/``hx-target`` point at ``#editAllocationModal`` /
``#editAllocationFormContainer``. Those ids live in a *separate* fragment
(``user/fragments/allocation_modals.html``). When a page included only the
first fragment the pencil was a silent no-op — Bootstrap found no modal and
htmx aborted on a target error, with nothing in the network tab.

``shared/project_details_modal.html`` now pulls the allocation fragment in
itself, so the pairing holds by construction. These tests pin that, and pin
that the shells are not double-included anywhere (duplicate DOM ids).
"""
import pytest

pytestmark = pytest.mark.unit


PROJECT_MODAL_ID = 'id="projectDetailsModal"'
EDIT_MODAL_ID = 'id="editAllocationModal"'
EDIT_CONTAINER_ID = 'id="editAllocationFormContainer"'

# Pages that render the project-details modal shell. Kept explicit rather
# than derived from the route map: the point is to catch a new page that
# includes one fragment and forgets the other.
PAGES_WITH_PROJECT_MODAL = [
    '/user/accounts',
    '/admin/projects',
    '/allocations/projects',
    '/allocations/transactions',
    '/allocations/adjustments',
    '/status/derecho',
]


@pytest.mark.parametrize('url', PAGES_WITH_PROJECT_MODAL)
def test_project_modal_pages_ship_the_allocation_modal(auth_client, url):
    """The pencil inside the project-details modal needs its target shell."""
    resp = auth_client.get(url)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert html.count(PROJECT_MODAL_ID) == 1, f'{url}: project modal shell'
    assert html.count(EDIT_MODAL_ID) == 1, f'{url}: edit-allocation shell'
    assert html.count(EDIT_CONTAINER_ID) == 1, f'{url}: htmx target container'


def test_edit_project_page_ships_one_of_each(auth_client, active_project):
    """/admin/project/<projcode>/edit assembles its own modal set (it extends
    dashboards/base, not base_admin) — it used to carry an inline copy of the
    edit-allocation modal alongside the shared include."""
    resp = auth_client.get(f'/admin/project/{active_project.projcode}/edit')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert html.count(PROJECT_MODAL_ID) == 1
    assert html.count(EDIT_MODAL_ID) == 1
    assert html.count(EDIT_CONTAINER_ID) == 1
