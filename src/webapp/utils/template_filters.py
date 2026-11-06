"""Jinja filters the webapp adds beyond sam.fmt: markup, and display vocabularies."""
from markupsafe import Markup, escape

from sam.integration.xras_api.vocabulary import document_type_label
from sam.queries.xras_requests import waiting_band
from sam.xras.preflight import gap_label


def path_breaks(value) -> Markup:
    """Escape a filesystem path, then let it wrap after each `/` (a path has no other break point)."""
    if value is None:
        return Markup('')
    return Markup(str(escape(value)).replace('/', '/<wbr>'))


def register_template_filters(app) -> None:
    app.jinja_env.filters['path_breaks'] = path_breaks
    app.jinja_env.filters['xras_document_type'] = document_type_label
    app.jinja_env.filters['xras_waiting'] = waiting_band
    app.jinja_env.filters['xras_gap'] = gap_label
