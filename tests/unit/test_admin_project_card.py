"""The `/admin/projects` card's `data-projcode` reload hook.

After an allocation edit the card fires `HX-Trigger: allocationUpdated`;
`static/js/modals.js` reloads only `#projectCardContainer` from
`/admin/project/<projcode>`, keyed off a `[data-projcode]` element inside the
card. Without that attribute the JS falls back to `window.location.reload()`,
which lands on a bare `/admin/projects` (no `?projcode=`) and blanks the card —
a silent runtime failure no other test sees. Pin the attribute here.
"""

import pytest

pytestmark = pytest.mark.unit

CARD_URL = '/admin/project/{}'


class TestAdminProjectCardReloadHook:

    def test_card_carries_data_projcode(self, auth_client, active_project):
        """modals.js:querySelector('[data-projcode]') must find this."""
        projcode = active_project.projcode
        resp = auth_client.get(CARD_URL.format(projcode))
        assert resp.status_code == 200
        assert f'data-projcode="{projcode}"' in resp.get_data(as_text=True)
