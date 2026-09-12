"""HTTP-layer tests for the allocations projects xlsx export route.

Auth/permission gate + a render smoke that the response is a real xlsx.
Row-level correctness is covered at the query/model layer.
"""

import io
import zipfile

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class TestProjectsExport:
    def test_returns_an_xlsx_attachment(self, auth_client):
        resp = auth_client.get('/allocations/projects/export?resources=Derecho')
        assert resp.status_code == 200
        assert resp.mimetype == XLSX_MIME
        cd = resp.headers.get('Content-Disposition', '')
        assert 'attachment' in cd and cd.endswith('.xlsx')
        body = resp.get_data()
        assert body[:2] == b'PK'
        assert zipfile.is_zipfile(io.BytesIO(body))

    def test_bad_date_falls_back_and_still_downloads(self, auth_client):
        resp = auth_client.get('/allocations/projects/export?active_at=not-a-date')
        assert resp.status_code == 200
        assert resp.mimetype == XLSX_MIME

    def test_403_without_view_projects(self, non_admin_client):
        assert non_admin_client.get(
            '/allocations/projects/export').status_code == 403

    def test_the_roots_only_switch_reaches_the_detail_fetch(self, auth_client, monkeypatch):
        """The workbook must match the screen: default roots only, off lists all."""
        from unittest.mock import Mock
        from webapp.dashboards.allocations import blueprint as bp
        spy = Mock(wraps=bp.cached_allocation_usage)
        monkeypatch.setattr(bp, 'cached_allocation_usage', spy)

        assert auth_client.get('/allocations/projects/export?resources=Derecho').status_code == 200
        assert spy.call_args_list and all(c.kwargs['root_only'] is True for c in spy.call_args_list)

        spy.reset_mock()
        assert auth_client.get('/allocations/projects/export?resources=Derecho&root_only=0').status_code == 200
        assert spy.call_args_list and all(c.kwargs['root_only'] is False for c in spy.call_args_list)
