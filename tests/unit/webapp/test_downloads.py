"""`webapp.utils.downloads`: file responses from bytes."""

import pytest

from webapp.utils.downloads import document_response, file_response


@pytest.fixture
def ctx(app):
    with app.test_request_context():
        yield


class TestFileResponse:
    def test_the_name_is_quoted_and_the_type_is_the_callers(self, ctx):
        resp = file_response(b'a,b\n', 'ex port "q".csv', mimetype='text/csv')
        assert resp.mimetype == 'text/csv'
        cd = resp.headers['Content-Disposition']
        assert cd.startswith('attachment') and 'ex port' in cd and '\\"q\\"' in cd
        assert b''.join(resp.response) == b'a,b\n'
        assert 'no-store' not in resp.headers.get('Cache-Control', '')

    def test_no_store_marks_viewer_bound_content(self, ctx):
        resp = file_response(b'x', 'x.bin', mimetype='application/octet-stream', no_store=True)
        assert resp.headers['Cache-Control'] == 'private, no-store'


class TestDocumentResponse:
    def test_a_pdf_is_inline_unless_downloaded(self, ctx):
        inline = document_response(b'%PDF-1.7 body', 'letter.pdf')
        assert inline.mimetype == 'application/pdf'
        assert inline.headers['Content-Disposition'].startswith('inline')
        assert inline.headers['Cache-Control'] == 'private, no-store'
        saved = document_response(b'%PDF-1.7 body', 'letter.pdf', download=True)
        assert saved.headers['Content-Disposition'].startswith('attachment')

    def test_anything_else_is_a_forced_download(self, ctx):
        resp = document_response(b'<html>not a pdf</html>', 'letter.pdf')
        assert resp.mimetype == 'application/octet-stream'
        assert resp.headers['Content-Disposition'].startswith('attachment')
