"""File responses from bytes: the name quoted and encoded, the type decided by the caller or the bytes."""
import io

from flask import send_file

PDF_MAGIC = b'%PDF-'


def file_response(body: bytes, filename: str, *, mimetype: str, download: bool = True,
                  no_store: bool = False):
    """``send_file`` over *body*; ``no_store`` for content tied to the viewer."""
    response = send_file(io.BytesIO(body), mimetype=mimetype, as_attachment=download,
                         download_name=filename, max_age=0)
    if no_store:
        response.headers['Cache-Control'] = 'private, no-store'
    return response


def document_response(body: bytes, filename: str, *, download: bool = False):
    """A fetched document, never cached: a body starting ``%PDF-`` is a PDF (inline
    unless *download*); anything else is a forced ``octet-stream`` download."""
    is_pdf = body.startswith(PDF_MAGIC)
    return file_response(
        body, filename, mimetype='application/pdf' if is_pdf else 'application/octet-stream',
        download=download or not is_pdf, no_store=True)
