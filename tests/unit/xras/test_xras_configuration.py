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
