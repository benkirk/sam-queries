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
        ('/admin/htmx/notifications/log', 'search'),
        ('/admin/htmx/tasks/log', 'search'),
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


class TestMultiselectChecklist:
    """multiselect_filter: one line tall, same wire format as a <select multiple>."""

    @staticmethod
    def _render(app, **kwargs):
        from flask import render_template_string

        template = (
            "{% from 'dashboards/fragments/form_fields.html' import multiselect_filter %}"
            "{{ multiselect_filter('Facilities', 'facilities', values, selected, **kw) }}")
        with app.test_request_context():
            return render_template_string(
                template, values=['NCAR', 'UNIV', 'WNA'],
                selected=kwargs.pop('selected', []), kw=kwargs)

    @staticmethod
    def _summary(html):
        return re.search(r'filter-checklist-summary">\s*([^<]*?)\s*</span>', html).group(1)

    @pytest.mark.parametrize('selected, expected', [
        ([], 'All'),
        (['UNIV'], 'UNIV'),
        (['UNIV', 'WNA'], '2 of 3'),
        (['NCAR', 'UNIV', 'WNA'], 'All'),
        (['GONE'], 'All'),
    ])
    def test_the_button_summarizes_the_selection(self, app, selected, expected):
        assert self._summary(self._render(app, selected=selected)) == expected

    def test_an_empty_selection_can_mean_the_routes_default(self, app):
        html = self._render(app, none_label='Default')
        assert self._summary(html) == 'Default'
        assert 'data-none-label="Default"' in html

    def test_each_value_is_a_checkbox_under_the_one_name(self, app):
        html = self._render(app, selected=['WNA'])
        boxes = re.findall(
            r'<input[^>]*type="checkbox"[^>]*name="facilities"\s+value="(\w+)"( checked)?', html)
        assert boxes == [('NCAR', ''), ('UNIV', ''), ('WNA', ' checked')]
        assert '<select' not in html and 'Hold Ctrl' not in html

    def test_no_vocabulary_renders_no_control(self, app):
        from flask import render_template_string

        template = (
            "{% from 'dashboards/fragments/form_fields.html' import multiselect_filter %}"
            "{{ multiselect_filter('Facilities', 'facilities', []) }}")
        with app.test_request_context():
            assert render_template_string(template).strip() == ''


class TestNavyPanels:

    @pytest.mark.parametrize('url', [
        '/allocations/projects', '/allocations/transactions',
        '/allocations/xras', '/admin/projects',
        '/admin/htmx/institutions-fragment',
    ])
    def test_one_primary_action_named_apply(self, auth_client, url):
        html = auth_client.get(url).get_data(as_text=True)
        panel = html.split('class="filter-sidebar', 1)[1].split('</form>', 1)[0]
        submits = re.findall(r'<button type="submit"[^>]*>(.*?)</button>', panel, re.S)
        labels = [re.sub(r'<[^>]+>', '', s).strip() for s in submits]
        assert labels[0] == 'Apply', labels
        assert 'Hold Ctrl' not in panel

    def test_the_action_log_panel_does_not_repeat_its_chips(self, auth_client):
        """Status and Action type are the chip strips under the panel; the form
        only holds the hidden fields those chips write into."""
        html = auth_client.get('/allocations/xras').get_data(as_text=True)
        form = html.split('id="xras-filters"', 1)[1].split('</form>', 1)[0]
        for name in ('status', 'action_type'):
            assert re.search(rf'<select name="{name}" multiple hidden', form)
        assert 'filter-checklist' not in form


class TestLogFilterRows:
    """Notifications and task runs: search and window are rows of the log's
    chip grid, bound to the page's hidden chip form."""

    @pytest.mark.parametrize('page, log, form_id', [
        ('/admin/htmx/notifications', '/admin/htmx/notifications/log',
         'notificationsFilterForm'),
        ('/admin/htmx/tasks', '/admin/htmx/tasks/log', 'scheduledTasksFilterForm'),
    ])
    def test_the_page_holds_only_the_hidden_chip_form(self, auth_client, page,
                                                      log, form_id):
        html = auth_client.get(page).get_data(as_text=True)
        assert f'<form id="{form_id}" class="d-none">' in html
        assert '<strong>Filter</strong>' not in html
        assert 'class="stat-strip"' in html
        assert f'submit from:#{form_id}' in html

    @pytest.mark.parametrize('log, form_id', [
        ('/admin/htmx/notifications/log', 'notificationsFilterForm'),
        ('/admin/htmx/tasks/log', 'scheduledTasksFilterForm'),
    ])
    def test_the_window_select_shows_the_window_in_force(self, auth_client, log,
                                                         form_id):
        html = auth_client.get(f'{log}?days=7&search=zz').get_data(as_text=True)
        select = re.search(r'<select[^>]*name="days"[^>]*>(.*?)</select>', html, re.S)
        assert select and f'form="{form_id}"' in select.group(0)
        assert re.search(r'<option value="7" selected>', select.group(1))
        assert select.group(1).count('selected') == 1
        box = re.search(r'<input type="search"[^>]*name="search"[^>]*>', html, re.S)
        assert 'value="zz"' in box.group(0) and f'form="{form_id}"' in box.group(0)
