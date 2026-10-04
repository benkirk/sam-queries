"""The `/admin/projects` card's `data-projcode` reload hook.

After an allocation edit the card fires `HX-Trigger: allocationUpdated`;
`static/js/modals.js` reloads only `#projectCardContainer` from
`/admin/project/<projcode>`, keyed off a `[data-projcode]` element inside the
card. Without that attribute the JS falls back to `window.location.reload()`,
which lands on a bare `/admin/projects` (no `?projcode=`) and blanks the card —
a silent runtime failure no other test sees. Pin the attribute here.
"""
import re

from flask import render_template_string


CARD_URL = '/admin/project/{}'


class TestAdminProjectCardReloadHook:

    def test_card_carries_data_projcode(self, auth_client, active_project):
        """modals.js:querySelector('[data-projcode]') must find this."""
        projcode = active_project.projcode
        resp = auth_client.get(CARD_URL.format(projcode))
        assert resp.status_code == 200
        assert f'data-projcode="{projcode}"' in resp.get_data(as_text=True)


SHARED_ROW = ("{% from 'dashboards/shared/project_tree.html' import allocation_cells with context %}"
              "{{ allocation_cells(res, true) }}")


class TestSharedRowHelpTerm:

    def test_the_from_term_does_not_run_its_rows_action(self, app):
        """The card's rows are data-action="navigate": a bare click on the term left the page."""
        res = {'is_inheriting': True, 'root_projcode': 'POOL0001', 'self_percent_used': 12.0,
               'self_used': 12.0, 'allocated': 100.0, 'used': 40.0, 'remaining': 60.0}
        with app.test_request_context():
            html = render_template_string(SHARED_ROW, res=res)
        term = re.search(r'<span class="help-term"[^>]*>from</span>', html)
        assert term and 'data-stop-propagation' in term.group(0)
