"""Caller-side chart rendering: the one rule for a chart that raises."""

from flask import current_app, g, get_template_attribute, request

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


def no_chart_failed(_response=None) -> bool:
    """`@cache.cached(response_filter=...)`: do not cache a page that drew an error state."""
    return not g.get('chart_failed', False)
