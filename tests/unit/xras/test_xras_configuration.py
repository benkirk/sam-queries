"""Allocations > XRAS > Configuration: the request types, editable (XRAS_SUBMISSION_SPRINT3.md)."""

import re

import pytest

from sam.xras.audiences import AUDIENCES

PAGE = '/allocations/xras/configuration'


class TestThePage:

    def test_a_viewer_gets_the_page(self, view_only_client):
        html = view_only_client.get(PAGE).get_data(as_text=True)
        assert 'What a PI sees before starting a request.' in html
        assert 'id="xrasTypeCardsSection"' in html

    def test_it_is_denied_without_view_xras(self, non_admin_client):
        assert non_admin_client.get(PAGE).status_code == 403

    def test_it_requires_login(self, client):
        assert client.get(PAGE).status_code in (302, 401)

    def test_the_shells_ship_to_manage_xras(self, auth_client):
        shells = ('createAllocationGuideModal', 'editAllocationGuideModal',
                  'createAllocationLimitModal', 'editAllocationLimitModal')
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert all(f'id="{s}"' in html for s in shells)

    def test_a_viewer_gets_no_shells(self, view_only_client):
        html = view_only_client.get(PAGE).get_data(as_text=True)
        assert 'id="editAllocationGuideModal"' not in html

    def test_admin_configuration_is_read_only_again(self, auth_client):
        html = auth_client.get('/admin/configuration').get_data(as_text=True)
        assert 'AllocationGuide' not in html and 'AllocationLimit' not in html

    def test_preview_offers_every_registry_audience_and_everyone(self, auth_client):
        html = auth_client.get(PAGE).get_data(as_text=True)
        assert 'id="typePreviewAll" value="" autocomplete="off" checked' in html
        for audience in AUDIENCES:
            assert f'value="{audience.key}"' in html and f'>{audience.label}</label>' in html


def _active_links(html):
    """The hrefs of every active nav link: navbar, mobile nav, the tabs and the strip."""
    return re.findall(r'<a class="[^"]*\bactive\b[^"]*"\s+href="([^"]+)"', html)


class TestTheStrip:

    @pytest.mark.parametrize('path', ['/allocations/xras', PAGE])
    def test_both_pages_carry_it_with_their_own_tab_active(self, auth_client, path):
        html = auth_client.get(path).get_data(as_text=True)
        assert f'href="{PAGE}"' in html and 'Work queue' in html
        active = _active_links(html)
        assert path in active
        assert (PAGE in active) == (path == PAGE)

    def test_the_xras_tab_and_navbar_item_stay_active_on_it(self, auth_client):
        active = _active_links(auth_client.get(PAGE).get_data(as_text=True))
        assert active.count('/allocations/xras') >= 3          # tab, navbar item, mobile nav


CARDS = PAGE + '/cards'


@pytest.fixture
def three(committed_guide):
    return {'ncar': committed_guide(audience='ncar'),
            'university': committed_guide(audience='university'),
            None: committed_guide()}


def _titles(client, query=''):
    return client.get(CARDS + query).get_data(as_text=True)


class TestTheCards:

    @pytest.mark.parametrize('query, shown', [('?audience=ncar', {'ncar', None}),
                                              ('?audience=university', {'university', None}),
                                              ('', {'ncar', 'university', None}),
                                              ('?audience=bogus', {'ncar', 'university', None})])
    def test_preview_as_shows_that_audiences_types(self, auth_client, three, query, shown):
        html = _titles(auth_client, query)
        assert {k for k, g in three.items() if f'>{g.title}</h2>' in html} == shown

    def test_a_viewer_sees_the_cards_without_a_button(self, view_only_client, three):
        html = _titles(view_only_client)
        assert f'>{three[None].title}</h2>' in html
        assert 'hx-get="/allocations/htmx/' not in html

    def test_a_manager_gets_edit_and_the_limit_buttons(self, auth_client, three):
        html = _titles(auth_client)
        assert f'/allocations/htmx/allocation-guide-edit-form/{three[None].id}' in html

    def test_an_outage_says_so_and_still_shows_the_cards(self, auth_client, three):
        html = _titles(auth_client)
        assert 'XRAS is not answering' in html and f'>{three["ncar"].title}</h2>' in html

    def test_a_retired_guide_is_a_card_marked_off_the_page(self, auth_client, committed_guide,
                                                           engine):
        from sqlalchemy import update
        from sam.accounting.allocation_type_guide import AllocationTypeGuide
        guide = committed_guide()
        with engine.begin() as conn:
            conn.execute(update(AllocationTypeGuide).where(
                AllocationTypeGuide.allocation_type_guide_id == guide.id).values(active=False))
        html = _titles(auth_client, '?audience=ncar')
        card = html.split(f'>{guide.title}</h2>')[1].split('</section>')[0]
        assert 'Not on the request page' in card and 'allocation-guide-edit-form' in card

    def test_an_open_type_names_its_opportunity(self, auth_client, three, monkeypatch):
        from webapp.dashboards.allocations.xras import configuration_routes
        from webapp.utils import xras as xras_utils
        opp = {'opportunity_id': 1, 'opportunity_name': 'Small (University)',
               'allocation_type_id': three[None].xras_type, 'allocation_type': 'Probe',
               'description': None, 'award_months': 12, 'can_submit': True, 'reason': None,
               'resources': []}
        reader = type('Reader', (), {'required_fields': lambda self, *a: {},
                                     'get_document_types': lambda self: []})()
        monkeypatch.setattr(configuration_routes, 'report_opportunities', lambda c: ([opp], False))
        monkeypatch.setattr(xras_utils, 'read_client', lambda c=None: reader)
        monkeypatch.setattr(xras_utils, 'resource_keys', lambda c=None: {})
        html = _titles(auth_client)
        card = html.split(f'>{three[None].title}</h2>')[1].split('</section>')[0]
        assert 'Open in XRAS: Small (University)' in card
        assert 'XRAS is not answering' not in html


class TestTheUnguidedCard:

    @pytest.fixture
    def unguided(self, engine):
        """A committed limit on an active SAM type no guide row names."""
        import os
        from sqlalchemy import delete
        from sqlalchemy.orm import Session
        from sam.accounting.allocation_type_guide import AllocationTypeGuide
        from sam.accounting.allocation_type_limits import AllocationTypeResourceLimit
        from sam.accounting.allocations import AllocationType
        from sam.resources.resources import Resource
        worker = int(os.environ.get('PYTEST_XDIST_WORKER', 'gw0')[2:] or 0)
        with Session(engine) as s:
            named = {n for (n,) in s.query(AllocationTypeGuide.allocation_type)}
            sam_type = next((t for t in s.query(AllocationType).filter(AllocationType.is_active)
                             .order_by(AllocationType.allocation_type_id)
                             if t.allocation_type not in named
                             and t.allocation_type != 'Education'), None)
            resources = s.query(Resource).order_by(Resource.resource_id.desc()).all()
            if sam_type is None or len(resources) <= worker:
                pytest.skip('no unguided active type, or too few resources for this worker')
            taken = {r for (r,) in s.query(AllocationTypeResourceLimit.resource_id).filter_by(
                allocation_type_id=sam_type.allocation_type_id)}
            free = [r for r in resources if r.resource_id not in taken]
            if len(free) <= worker:
                pytest.skip('too few free resources for this worker')
            row = AllocationTypeResourceLimit.create(
                s, allocation_type_id=sam_type.allocation_type_id,
                resource_id=free[worker].resource_id, created_by='t', max_amount=7)
            s.commit()
            found = (row.allocation_type_resource_limit_id, sam_type.allocation_type)
        yield found[1]
        with engine.begin() as conn:
            conn.execute(delete(AllocationTypeResourceLimit).where(
                AllocationTypeResourceLimit.allocation_type_resource_limit_id == found[0]))

    def test_it_shows_with_publish_preset_to_its_type(self, auth_client, unguided):
        from urllib.parse import quote_plus
        html = _titles(auth_client)
        card = html.split(f'>{unguided}</h2>')[1].split('</section>')[0]
        assert 'Not on the request page' in card and 'Publish on the request page' in card
        assert f'allocation_type={quote_plus(unguided)}' in card.replace('&amp;', '&')

    def test_an_xras_type_no_guide_names_shows_with_publish_preset_to_its_id(
            self, auth_client, monkeypatch):
        from webapp.dashboards.allocations.xras import configuration_routes
        from webapp.utils import xras as xras_utils
        opp = {'opportunity_id': 1, 'opportunity_name': 'New program (University)',
               'allocation_type_id': 99_999_990, 'allocation_type': 'New program',
               'description': None, 'award_months': 12, 'can_submit': True, 'reason': None,
               'resources': []}
        reader = type('Reader', (), {'required_fields': lambda self, *a: {},
                                     'get_document_types': lambda self: []})()
        monkeypatch.setattr(configuration_routes, 'report_opportunities', lambda c: ([opp], False))
        monkeypatch.setattr(xras_utils, 'read_client', lambda c=None: reader)
        monkeypatch.setattr(xras_utils, 'resource_keys', lambda c=None: {})
        html = _titles(auth_client)
        card = html.split('>New program</h2>')[1].split('</section>')[0]
        assert 'Not on the request page' in card and 'Publish on the request page' in card
        assert 'xras_allocation_type_id=99999990' in card.replace('&amp;', '&')
        assert 'Open in XRAS: New program (University)' in card

    def test_the_guide_form_opens_preset(self, auth_client, unguided):
        from urllib.parse import urlencode
        html = auth_client.get('/allocations/htmx/allocation-guide-create-form?' + urlencode(
            {'allocation_type': unguided, 'title': unguided,
             'xras_allocation_type_id': 99_999_990})).get_data(as_text=True)
        title = html.split('id="createAllocationGuideTitle"')[1].split('>')[0]
        assert f'value="{unguided}"' in title
        xras = html.split('id="createAllocationGuideXrasType"')[1].split('>')[0]
        assert 'value="99999990"' in xras
        select = html.split('name="allocation_type"')[1].split('</select>')[0]
        assert f'<option value="{unguided}" selected' in select

    def test_the_limit_form_opens_preset_to_the_types_first_id(self, auth_client, unguided):
        from urllib.parse import urlencode
        html = auth_client.get('/allocations/htmx/allocation-limit-create-form?' + urlencode(
            {'allocation_type': unguided})).get_data(as_text=True)
        select = html.split('name="allocation_type_id"')[1].split('</select>')[0]
        assert ' selected' in select
