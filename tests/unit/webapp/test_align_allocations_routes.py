"""Align Allocations routes — auth/render smoke.

The align logic is covered at the model layer
(`tests/unit/manage/test_align_allocations.py`); this covers the HTTP tier.
"""


def _form_url(projcode):
    return f'/admin/htmx/align-allocations-form/{projcode}'


def test_form_renders_for_an_admin(auth_client, active_project):
    resp = auth_client.get(_form_url(active_project.projcode))
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Either the grid ("Make consistent") or the no-allocations warning renders.
    assert ('Make consistent' in html
            or 'No allocations are active' in html)


def test_form_is_forbidden_without_permission(non_admin_client, active_project):
    resp = non_admin_client.get(_form_url(active_project.projcode))
    assert resp.status_code in (403, 302)
