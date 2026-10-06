"""Jinja filters that emit markup, so they live in the webapp and not in sam.fmt."""
from markupsafe import Markup, escape


def path_breaks(value) -> Markup:
    """Escape a filesystem path, then let it wrap after each `/` (a path has no other break point)."""
    if value is None:
        return Markup('')
    return Markup(str(escape(value)).replace('/', '/<wbr>'))


def register_template_filters(app) -> None:
    app.jinja_env.filters['path_breaks'] = path_breaks
