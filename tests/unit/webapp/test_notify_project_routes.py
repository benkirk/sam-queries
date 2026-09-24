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


def test_an_overlong_comment_rerenders_the_form_and_sends_nothing(
        auth_client, active_project):
    resp = auth_client.post(
        f'/admin/htmx/notify-project/{active_project.projcode}',
        data={'operator_comment': 'x' * 1001})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Longer than maximum length' in html
    assert 'notifyOperatorComment' in html or 'nothing to notify about' in html


def test_a_rendered_preview_refetches_itself_on_a_comment_change(
        auth_client, active_project):
    resp = auth_client.get(
        f'/admin/htmx/notify-project-preview/{active_project.projcode}'
        '?action=adjusted&active_at=2026-09-20')
    html = resp.get_data(as_text=True)
    if 'Subject' not in html:   # nobody on file for this snapshot row
        return
    assert 'from:#notifyOperatorComment' in html
    assert 'action=adjusted&amp;active_at=2026-09-20' in html
    assert 'hx-target="this"' in html


def _two_recipients(monkeypatch):
    """Stub the notice and builder so the preview has a lead and an admin."""
    from types import SimpleNamespace
    from sam.notify import Message, Recipient
    import sam.queries.lifecycle_notices as ln

    def _build(session, *, per_project, operator_comment='', **_):
        return [Message(kind='project_activation', subject=f'Active ({role})',
                        recipient=Recipient(addr, name=role.title(), role=role),
                        context={'operator_comment': operator_comment},
                        dedup_key=f'test:{addr}')
                for addr, role in (('lead@example.edu', 'lead'),
                                   ('admin@example.edu', 'admin'))]

    monkeypatch.setattr(ln, 'notice_for_project', lambda *a, **k: SimpleNamespace(
        item_for=lambda action: object()))
    monkeypatch.setattr(ln, 'build_lifecycle_messages', _build)


def test_the_recipient_picker_round_trips(auth_client, active_project, monkeypatch):
    _two_recipients(monkeypatch)
    url = f'/admin/htmx/notify-project-preview/{active_project.projcode}'
    html = auth_client.get(url + '?action=activated').get_data(as_text=True)
    assert 'id="notifyPreviewRecipient"' in html
    assert 'hx-include="#notifyOperatorComment, #notifyPreviewRecipient"' in html
    html = auth_client.get(url, query_string={
        'action': 'activated', 'preview_recipient': 'admin@example.edu',
    }).get_data(as_text=True)
    assert '<option value="admin@example.edu" selected' in html
    assert 'Active (admin)' in html


def test_skip_answers_with_an_info_panel(auth_client, active_project):
    resp = auth_client.get(
        f'/admin/htmx/notify-project-preview/{active_project.projcode}'
        '?action=skip')
    assert resp.status_code == 200
    assert 'will not be notified' in resp.get_data(as_text=True)
