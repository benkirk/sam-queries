import json
from flask import make_response, render_template, request

from webapp.extensions import db
from sam.manage import management_transaction


#: The chart layouts a request may ask for. Anything else means "no override".
#: Must match the names ``charts/layout.profile()`` builds — the string is
#: passed straight through to the chart layer and into its cache key.
_LAYOUTS = frozenset({'desktop', 'tablet', 'mobile'})

#: Written by ``static/js/layout-axis.js`` from ``matchMedia``.
LAYOUT_COOKIE = 'sam_layout'


def read_layout(default: str = 'desktop') -> str:
    """Which chart layout this request wants — see ``_LAYOUTS``.

    Two sources, in precedence order — the query string, then the cookie —
    because charts reach the browser two different ways and neither channel
    covers both (the reasoning is in ``static/js/layout-axis.js``). Query
    string wins so an htmx fragment reflects the viewport *now* rather than
    whatever the cookie said when the page was served, and so ``?layout=mobile``
    works by hand for debugging.

    **Lenient, never a 400.** Matches ``jobs/routes.py:_parse_period`` and
    ``charts/layout.py:resolve_layout``: an unknown value means "no override".
    These are htmx fragments, and a stale or hand-typed value must not break a
    card. Passing an unknown name through would be equally safe — the chart
    layer falls back too — but normalizing here keeps the value that reaches
    the *cache key* to the declared spellings instead of arbitrarily many, and
    the key is shared across workers and pods.

    Returns:
        A member of ``_LAYOUTS`` — never anything else.
    """
    raw = (request.args.get('layout')
           or request.cookies.get(LAYOUT_COOKIE)
           or '').strip().lower()
    return raw if raw in _LAYOUTS else default


#: The themes a request may ask for. Must match ``charts/theme.py:THEMES`` —
#: the string is passed straight through to the chart layer and into its
#: cache key, exactly like ``_LAYOUTS``.
_THEMES = frozenset({'light', 'dark'})

#: Written by ``static/js/theme-toggle.js``. Unlike ``LAYOUT_COOKIE`` this is
#: a *persistent* cookie (Max-Age one year): a viewport is a property of the
#: visit, a theme is a preference to remember.
THEME_COOKIE = 'sam_theme'


def read_theme(default: str = 'light') -> str:
    """Which theme this request wants — see ``_THEMES``.

    Deliberately the same shape as :func:`read_layout`, because it is the same
    problem: a per-user rendering mode the server must know *before* it
    renders, since charts are matplotlib SVGs with baked-in colours that no
    stylesheet can retheme. If these two functions ever stop being readable
    side by side, something has been reasoned about wrongly.

    **One channel, not two — where this diverges from `read_layout`.**
    ``layout-axis.js`` injects ``?layout=`` into every htmx request *as well
    as* writing the cookie, because a viewport is discovered client-side after
    the server has already answered: the first page a visitor ever loads is
    rendered before the cookie exists. A theme is never discovered — it is
    declared by an explicit click that then reloads, so the cookie and the
    browser can never disagree, and a first-ever visitor has no preference to
    discover (``light`` is the answer, not a stale guess).

    The query string is still read first, but **no JavaScript ever sets it**.
    It exists so ``?theme=dark`` works by hand for debugging, and so this
    function stays literally the same function as ``read_layout`` — the
    property a reviewer should check.

    **Lenient, never a 400**, for the same reason as ``read_layout``: a stale
    or hand-typed value must not break a page, and normalizing here keeps the
    value reaching the *cache key* to the declared spellings rather than
    arbitrarily many.

    Returns:
        A member of ``_THEMES`` — never anything else.
    """
    raw = (request.args.get('theme')
           or request.cookies.get(THEME_COOKIE)
           or '').strip().lower()
    return raw if raw in _THEMES else default


#: Values a checkbox / switch may arrive as. Templates emit ``1`` (see the
#: ``active_toggle_search`` macro, the admin card toggles and the charts'
#: log-scale switches) but the set is deliberately permissive: a checkbox
#: copy-pasted with the wrong spelling should still work rather than
#: silently fail open.
_TRUTHY = frozenset({'1', 'true', 'on', 'yes'})


def is_truthy(raw):
    """Is a raw query/form value one of the affirmative spellings above?"""
    return str(raw or '').strip().lower() in _TRUTHY


def read_flag(args, name, default=False):
    """Read a boolean toggle off a request args/form mapping.

    The general form of :func:`read_active_only` — same absent-means-off
    reasoning (htmx omits an unchecked box entirely), and the same escape
    hatch for switches with no checkbox behind them.
    """
    raw = args.get(name)
    return default if raw is None else is_truthy(raw)


def read_active_only(args, default=False):
    """Read an ``active_only`` filter flag off a request args/form mapping.

    **Absent means OFF.** htmx omits an unchecked checkbox from the request
    entirely, so a missing value is how "show me inactive rows too" arrives
    over the wire — there is no distinct "unchecked" value to look for. A
    route that defaults this to ON makes its checkbox inert in one
    direction, which is exactly how inactive users/projects went missing
    from the admin search boxes.

    Pass ``default=True`` only for endpoints with no checkbox behind them
    (the FK pickers), where active-only is the intended fixed behaviour.

    Args:
        args:    ``request.args`` or ``request.form`` (anything with .get).
        default: value to use when the key is absent entirely.

    Returns:
        bool
    """
    return read_flag(args, 'active_only', default)


def htmx_success(template, triggers, *, toast=None, toast_variant='success', **ctx):
    """Render a success fragment with HX-Trigger response headers.

    Fires custom DOM events that htmx-config.js listens for to close the
    active modal and reload the relevant card section.

    Args:
        template: Jinja2 template path
        triggers: dict mapping event names to payloads, e.g.
                  {'closeActiveModal': {}, 'reloadFacilitiesCard': {}}
        toast: optional text for an auto-dismissing toast. When set, a
               `showToast` trigger is added to the HX-Trigger payload so
               htmx-config.js surfaces ephemeral feedback bottom-right.
        toast_variant: Bootstrap color variant for the toast
                       ('success', 'info', 'warning', 'danger').
        **ctx: template context variables
    """
    response = make_response(render_template(template, **ctx))
    payload = dict(triggers)
    if toast:
        payload['showToast'] = {'message': toast, 'variant': toast_variant}
    response.headers['HX-Trigger'] = json.dumps(payload)
    return response


def htmx_success_message(triggers, message, detail=None):
    """Render the generic success fragment with HX-Trigger response headers.

    Convenience wrapper around htmx_success() for the common case where
    no custom template is needed — just a checkmark and a message.

    Args:
        triggers: dict mapping event names to payloads
        message:  Primary success text shown in bold
        detail:   Optional secondary line (e.g. project code + title)
    """
    return htmx_success(
        'dashboards/fragments/htmx_success.html',
        triggers,
        toast=message,
        message=message,
        detail=detail,
    )


def modal_triggers(*reload_events):
    """HX-Trigger payload that closes the active modal and fires reload
    events for the affected card(s). Returns a fresh dict per call —
    safe to mutate at the call site.

    Example:
        modal_triggers('reloadFacilitiesCard')
        → {'closeActiveModal': {}, 'reloadFacilitiesCard': {}}
    """
    triggers = {'closeActiveModal': {}}
    for event in reload_events:
        triggers[event] = {}
    return triggers


def handle_htmx_form_post(
    *,
    schema_cls,
    template,
    do_action,
    success_triggers,
    success_message='Saved successfully.',
    success_detail=None,
    success_redirect=None,
    error_prefix='Error',
    extra_context=None,
    context_fn=None,
    after_commit=None,
):
    """Handle the standard HTMX create/edit form POST flow.

    Replaces the boilerplate that every *_routes.py file repeats:

        try:
            data = SomeForm().load(request.form)
        except ValidationError as e:
            return render_template(template, errors=..., form=request.form, ...)
        try:
            with management_transaction(db.session):
                do_thing(data)
        except Exception as e:
            return render_template(template, errors=[f'Error: {e}'], form=request.form, ...)
        return htmx_success_message(triggers, 'Saved successfully.')

    Args:
        schema_cls:        marshmallow schema class (must subclass HtmxFormSchema
                           so that .flatten_errors() is available).
        template:          Jinja2 template path for the form fragment (re-rendered
                           on validation/DB error).
        do_action:         callable taking the validated `data` dict. Should
                           perform the create/update *inside* `management_transaction`
                           — the helper handles the transaction. Raise on error.
                           May return a value (the created/updated object); if
                           `success_triggers` is callable it receives this value.
        success_triggers:  HX-Trigger payload. Either a dict, e.g.
                           {'closeActiveModal': {}, 'reloadFacilitiesCard': {}},
                           or a callable `result -> dict` for dynamic triggers
                           that need the created/updated object
                           (e.g. `lambda p: {'loadNewProject': p.projcode}`).
        success_message:   Primary success text (default 'Saved successfully.').
        success_detail:    Optional secondary line. Either a string, or a
                           callable `result -> str` for per-instance detail
                           like "SCSG0001 — My project title".
        success_redirect:  Optional destination URL — a string or a callable
                           `result -> url`. When set, success responds with
                           an HX-Redirect (full-page navigation) instead of
                           the in-modal success fragment; the message/detail
                           are flashed for the destination page to render
                           and `success_triggers` is skipped.
        error_prefix:      Prefix for unexpected exception messages
                           (e.g. 'Error creating facility').
        extra_context:     Static dict merged into the re-render context — pass
                           the entity being edited here, e.g. {'facility': facility}.
        context_fn:        Optional callable returning a dict of additional
                           re-render context — use this when the context needs
                           a fresh DB query (e.g. dropdown options).
        after_commit:      Optional callable `result -> None` run after the
                           transaction commits — post-commit side effects
                           like cache invalidation, never DB writes.

    Handlers that need more than these kwargs express — partial loads,
    PUT gating, cross-field ORM checks, exception mapping, custom success
    responses — subclass `webapp.utils.form_handler.HtmxFormHandler`
    (this function is a thin adapter over the same lifecycle).

    Returns: Flask response (rendered fragment or htmx_success_message).
    """
    # Local import: cycle — form_handler imports htmx_success_message
    # from this module.
    from webapp.utils.form_handler import _KwargFormHandler
    return _KwargFormHandler(
        schema_cls=schema_cls,
        template=template,
        do_action=do_action,
        success_triggers=success_triggers,
        success_message=success_message,
        success_detail=success_detail,
        success_redirect=success_redirect,
        error_prefix=error_prefix,
        extra_context=extra_context,
        context_fn=context_fn,
        after_commit=after_commit,
    ).handle()


def register_typeahead(bp, *, rule, endpoint, permission, search, template,
                       ctx_key, min_len=2, any_facility=False,
                       active_only_default=False):
    """Register a search-as-you-type GET endpoint on ``bp``.

    Standard shape shared by the FK pickers and admin search boxes:
    read ``q``, return '' below ``min_len``, run ``search``, render the
    result-list fragment. Endpoints whose branching is the feature (the
    multi-context user search, the facility-scoped project search) stay
    hand-written.

    Args:
        rule / endpoint: URL rule and endpoint name — passed explicitly so
                         existing template ``hx-get`` URLs stay stable.
        permission:      Permission gating the endpoint.
        any_facility:    use ``require_permission_any_facility`` instead of
                         ``require_permission`` (scoped-manager pickers).
        search:          callable ``(q, active_only) -> list`` running the
                         actual query (ignore ``active_only`` when the
                         endpoint has no toggle).
        template:        result-list fragment.
        ctx_key:         template variable receiving the result list
                         (``q`` is always passed alongside).
        min_len:         minimum query length (below → empty response).
        active_only_default: default for ``read_active_only`` when the
                         param is absent (see that helper's docstring).
    """
    # Local imports: cycle — rbac and several blueprints import from this
    # module at import time.
    from flask import request as _request
    from flask_login import login_required
    from webapp.utils.rbac import (
        require_permission, require_permission_any_facility,
    )

    def view():
        q = (_request.args.get('q') or '').strip()
        if len(q) < min_len:
            return ''
        active_only = read_active_only(_request.args,
                                       default=active_only_default)
        return render_template(template, q=q, **{ctx_key: search(q, active_only)})

    view.__name__ = endpoint
    gate = (require_permission_any_facility(permission) if any_facility
            else require_permission(permission))
    bp.add_url_rule(rule, endpoint=endpoint,
                    view_func=login_required(gate(view)))


def htmx_not_found(name='Resource', status=404):
    """Standard 404 response fragment for missing entities.

    Returns a tuple suitable as a Flask response (HTML, status).
    """
    return f'<div class="alert alert-danger">{name} not found</div>', status


def handle_htmx_soft_delete(obj, *, name='Resource'):
    """Standard soft-delete (active=False) flow for HTMX delete routes.

    Wraps the management_transaction + obj.update(active=False) + 500 fallback
    pattern. Pass the loaded object; caller is responsible for the 404 lookup.
    """
    try:
        with management_transaction(db.session):
            obj.update(active=False)
    except Exception as e:  # noqa: BLE001
        return f'<div class="alert alert-danger">Error: {e}</div>', 500
    return ''
