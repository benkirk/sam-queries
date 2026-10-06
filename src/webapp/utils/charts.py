"""Caller-side chart rendering: the one rule for a chart that raises."""

from flask import current_app, g, get_template_attribute, request
from flask_login import current_user

from sam.queries.projects import project_titles
from webapp.extensions import db
from webapp.utils.rbac import Permission, user_facility_scope

_BITS = 'dashboards/fragments/chart_bits.html'


def draw_chart(generator, *args, **kwargs) -> str:
    """Call a chart generator; if it raises, log it and return the shared error state.

    `BaseChart` swallows nothing, on purpose. A route must: htmx does not swap a
    500, so a chart that raises leaves its loader's spinner up forever, and on a
    full-page render it takes the whole page with it. Sets ``g.chart_failed`` so
    a cached page can decline to cache the error (`chart_failed`).
    """
    try:
        return generator(*args, **kwargs)
    except Exception:  # noqa: BLE001 - any chart failure gets the same answer
        name = getattr(getattr(generator, 'chart_class', None), 'cache_name', None) or getattr(
            generator, '__name__', 'chart')
        current_app.logger.exception('chart %s failed on %s', name, request.path)
        g.chart_failed = True
        return str(get_template_attribute(_BITS, 'chart_error')())


def hover_titles(projcodes, *, own=False) -> dict:
    """``{projcode: title}`` for a chart's hovers, for projects this viewer may open.

    A chart can show a project code to someone who cannot open the project
    (machine-wide job history, the status load chart). Its title goes only to a
    viewer whose `VIEW_PROJECTS` scope covers the project's facility. ``own``
    says the codes are the viewer's own projects, which membership admits.
    One query; ``{}`` leaves every hover as it reads without titles.
    """
    codes = {c for c in projcodes if isinstance(c, str) and c}
    if not codes or not current_user.is_authenticated:
        return {}
    scope = None if own else user_facility_scope(current_user, Permission.VIEW_PROJECTS)
    if scope is not None and not scope:
        return {}
    return project_titles(db.session, codes, facility_names=scope)


def no_chart_failed(_response=None) -> bool:
    """`@cache.cached(response_filter=...)`: do not cache a page that drew an error state."""
    return not g.get('chart_failed', False)
