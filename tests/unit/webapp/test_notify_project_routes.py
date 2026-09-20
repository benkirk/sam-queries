"""Manual "Notify" button routes — auth/render smoke.

Per house convention the HTTP tier covers auth/validation/render; the
classify/build/send logic is exercised at the model layer in
`tests/unit/notify/test_lifecycle_notices.py`.
"""


def _form_url(projcode):
    return f'/admin/htmx/notify-project-form/{projcode}'


def test_form_renders_for_an_admin(auth_client, active_project):
    resp = auth_client.get(_form_url(active_project.projcode))
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Either the project tree or the "nothing to notify" info is a valid render.
    assert ('Send notifications' in html
            or 'nothing to notify about' in html)


def test_preview_renders_for_an_admin(auth_client, active_project):
    resp = auth_client.get(
        f'/admin/htmx/notify-project-preview/{active_project.projcode}'
        '?action=activated')
    assert resp.status_code == 200


def test_form_is_forbidden_without_permission(non_admin_client, active_project):
    resp = non_admin_client.get(_form_url(active_project.projcode))
    assert resp.status_code in (403, 302)
