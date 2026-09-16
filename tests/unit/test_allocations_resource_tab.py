"""Shareable ?tab= deep-link on the Allocations dashboard's #resourceTabs.

Auth/render smoke per house convention — gates the server-driven tab channel,
not any data path.
"""
import re


def _resource_tabs_block(html):
    m = re.search(r'<ul[^>]*id="resourceTabs".*?</ul>', html, re.DOTALL)
    return m.group(0) if m else ''


def _active_tab_id(html):
    block = _resource_tabs_block(html)
    m = re.search(r'<a class="[^"]*\bactive\b[^"]*"[^>]*id="([^"]+)-tab"', block, re.DOTALL)
    return m.group(1) if m else None


class TestResourceTabDeepLink:
    def test_tab_selects_named_resource(self, auth_client):
        resp = auth_client.get('/allocations/projects?tab=derecho')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'data-tab-url-param="tab"' in html
        assert 'data-tab-param-value="derecho"' in html
        assert _active_tab_id(html) == 'Derecho'

    def test_bogus_tab_falls_back_to_default(self, auth_client):
        # A bogus tab renders the same active tab as the no-param default,
        # and never 500s.
        bogus = auth_client.get('/allocations/projects?tab=zzz')
        default = auth_client.get('/allocations/projects')
        assert bogus.status_code == default.status_code == 200
        assert _active_tab_id(bogus.get_data(as_text=True)) \
            == _active_tab_id(default.get_data(as_text=True))

    def test_filter_form_carries_the_tab(self, auth_client):
        # The GET filter reload must preserve the tab (url-param channel opts
        # out of localStorage restore).
        html = auth_client.get('/allocations/projects?tab=derecho').get_data(as_text=True)
        assert '<input type="hidden" name="tab" value="derecho">' in html
