import json
from flask import make_response, render_template


def htmx_success(template, triggers, **ctx):
    """Render a success fragment with HX-Trigger response headers.

    Fires custom DOM events that htmx-config.js listens for to close the
    active modal and reload the relevant card section.

    Args:
        template: Jinja2 template path
        triggers: dict mapping event names to payloads, e.g.
                  {'closeActiveModal': {}, 'reloadFacilitiesCard': {}}
        **ctx: template context variables
    """
    response = make_response(render_template(template, **ctx))
    response.headers['HX-Trigger'] = json.dumps(triggers)
    return response
