"""Links in outgoing mail: PUBLIC_BASE_URL wins over the request host."""

from webapp.utils.notify import public_url_for, public_url_root


def test_unset_follows_the_request_host(app):
    app.config['PUBLIC_BASE_URL'] = ''
    with app.test_request_context('/', base_url='https://samuel.k8s.ucar.edu'):
        assert public_url_root() == 'https://samuel.k8s.ucar.edu/'


def test_set_overrides_the_cluster_hostname(app, monkeypatch):
    monkeypatch.setitem(app.config, 'PUBLIC_BASE_URL', 'https://sam.hpc.ucar.edu')
    with app.test_request_context('/', base_url='https://samuel.k8s.ucar.edu'):
        assert public_url_root() == 'https://sam.hpc.ucar.edu/'
        assert public_url_for(
            'admin_dashboard.edit_project_page', projcode='SCSG0001',
            tab='allocations') == (
            'https://sam.hpc.ucar.edu/admin/project/SCSG0001/edit?tab=allocations')
