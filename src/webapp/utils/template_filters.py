"""Jinja filters the webapp adds beyond sam.fmt: markup, and display vocabularies."""
import re

from markupsafe import Markup, escape

from sam.integration.xras_api.vocabulary import document_type_label
from sam.queries.xras_requests import waiting_band, waiting_note
from sam.xras.preflight import gap_label


def path_breaks(value) -> Markup:
    """Escape a filesystem path, then let it wrap after each `/` (a path has no other break point)."""
    if value is None:
        return Markup('')
    return Markup(str(escape(value)).replace('/', '/<wbr>'))


_NOT_ID_SAFE = re.compile(r'[^A-Za-z0-9_-]')


def dom_id(value) -> str:
    """A value as an id fragment that is also a plain `#id` selector (`a.b` would select a class)."""
    return _NOT_ID_SAFE.sub('_', str(value))


def register_template_filters(app) -> None:
    app.jinja_env.filters['dom_id'] = dom_id
    app.jinja_env.filters['path_breaks'] = path_breaks
    app.jinja_env.filters['xras_document_type'] = document_type_label
    app.jinja_env.filters['xras_waiting'] = waiting_band
    app.jinja_env.filters['xras_waiting_note'] = waiting_note
    app.jinja_env.filters['xras_gap'] = gap_label
