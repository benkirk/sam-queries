"""Links in outgoing mail: PUBLIC_BASE_URL wins over the request host."""

import pytest

from webapp.utils.notify import public_url_for, public_url_root


def test_after_the_reply_builds_against_the_public_root(app, monkeypatch):
    """The XRAS hooks run on response close, with no request (dev proof 2026-10-31)."""
    monkeypatch.setitem(app.config, 'PUBLIC_BASE_URL', 'https://sam.hpc.ucar.edu')
    with app.app_context():
        assert public_url_for('admin_dashboard.account_requests', request=34) == (
            'https://sam.hpc.ucar.edu/admin/account-requests?request=34')


def test_after_the_reply_without_a_public_root_says_which_setting(app, monkeypatch):
    monkeypatch.setitem(app.config, 'PUBLIC_BASE_URL', '')
    with app.app_context(), pytest.raises(RuntimeError, match='PUBLIC_BASE_URL'):
        public_url_for('admin_dashboard.account_requests', request=34)


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
