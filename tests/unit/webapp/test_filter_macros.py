"""The shared in-card filter atoms: search box, Clear filters, active switch."""

import re

import pytest

LAST_SEEN = '/admin/htmx/users/last-seen'
ACTIVITY = '/allocations/xras_pending_fragment'


class TestClearFilters:

    @pytest.mark.parametrize('url', [LAST_SEEN, ACTIVITY])
    def test_absent_at_rest(self, auth_client, url):
        assert 'facet-clear-all' not in auth_client.get(url).get_data(as_text=True)

    @pytest.mark.parametrize('url, form_id', [
        (f'{LAST_SEEN}?bucket=stale', 'lastSeenFilterForm'),
        (f'{LAST_SEEN}?q=ben', 'lastSeenFilterForm'),
        (f'{ACTIVITY}?tag=failed', 'xras-activity-filters'),
    ])
    def test_present_once_something_is_selected(self, auth_client, url, form_id):
        html = auth_client.get(url).get_data(as_text=True)
        button = re.search(r'<button[^>]*class="facet-clear"[^>]*>', html)
        assert button, 'no Clear filters row'
        assert f'data-form-id="{form_id}"' in button.group(0)

    def test_the_empty_state_offers_the_same_action(self, auth_client):
        html = auth_client.get(f'{ACTIVITY}?tag=failed&activity_type=nope'
                               ).get_data(as_text=True)
        assert html.count('data-action="facet-clear-all"') == 2

    def test_chip_fields_are_what_it_clears(self, auth_client):
        page = auth_client.get('/allocations/xras').get_data(as_text=True)
        form = page.split('id="xras-accounts-filters"', 1)[1].split('</form>', 1)[0]
        assert form.count('data-facet-field') == 5
        # Sort is form state but not a filter: Clear leaves it alone.
        assert not re.search(r'name="sort_by"[^>]*data-facet-field', form)


class TestFilterSearch:

    def test_in_its_own_form_it_includes_that_form(self, auth_client):
        html = auth_client.get('/admin/users/last-seen').get_data(as_text=True)
        box = re.search(r'<input type="search"[^>]*id="lastSeenSearch"[^>]*>', html, re.S)
        assert box and 'name="q"' in box.group(0)
        assert 'hx-trigger="input changed delay:300ms"' in box.group(0)
        assert 'hx-include="closest form"' in box.group(0)
        assert '<label class="visually-hidden" for="lastSeenSearch">' in html

    def test_bound_to_a_chip_form_it_also_answers_enter(self, auth_client):
        html = auth_client.get('/admin/account-requests/fragment').get_data(as_text=True)
        box = re.search(r'<input type="search"[^>]*id="account-requests-search"[^>]*>',
                        html, re.S)
        assert box and 'name="search"' in box.group(0)
        assert 'hx-trigger="input changed delay:300ms, search"' in box.group(0)
        assert re.search(r'form="[\w-]+"', box.group(0))

    @pytest.mark.parametrize('url, name', [
        ('/admin/htmx/notifications', 'search'),
        ('/admin/htmx/tasks', 'search'),
        ('/admin/htmx/rate-limits', 'actor'),
        ('/admin/htmx/mnemonic-codes-table', 'q'),
    ])
    def test_every_list_search_requests_as_you_type(self, auth_client, url, name):
        html = auth_client.get(url).get_data(as_text=True)
        box = re.search(rf'<input type="search"[^>]*name="{name}"[^>]*>', html, re.S)
        assert box, f'{url}: no search box'
        assert 'input changed delay:300ms' in box.group(0)


class TestActiveSwitch:

    @pytest.mark.parametrize('url, input_id', [
        ('/admin/facilities', 'facilitiesCardActiveOnly'),
        ('/admin/resources', 'resourcesCardActiveOnly'),
        ('/admin/organizations', 'organizationsCardActiveOnly'),
        ('/admin/contracts', 'contractsTableActiveOnly'),
    ])
    def test_card_switches_share_one_shape(self, auth_client, url, input_id):
        html = auth_client.get(url).get_data(as_text=True)
        box = re.search(rf'<input[^>]*id="{input_id}"[^>]*>', html, re.S)
        assert box, f'{url}: no {input_id}'
        tag = box.group(0)
        assert 'role="switch"' in tag and 'name="active_only"' in tag
        assert 'value="1"' in tag and 'checked' in tag
        assert 'hx-trigger="change"' in tag
