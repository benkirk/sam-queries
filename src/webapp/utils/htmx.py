import json
from flask import flash, make_response, render_template, request
from marshmallow import ValidationError

from webapp.extensions import db
from webapp.utils.fk_validation import FKValidationError
from sam.manage import management_transaction


#: Values an "Active only" checkbox may arrive as. Templates emit ``1``
#: (see the ``active_toggle_search`` macro and the admin card toggles) but
#: the set is deliberately permissive: a checkbox copy-pasted with the
#: wrong spelling should still work rather than silently fail open.
_TRUTHY = frozenset({'1', 'true', 'on', 'yes'})


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
    raw = args.get('active_only')
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUTHY


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

    Returns: Flask response (rendered fragment or htmx_success_message).
    """
    def _render_with_errors(errs, field_errors=None):
        ctx = {}
        if extra_context:
            ctx.update(extra_context)
        if context_fn is not None:
            ctx.update(context_fn())
        ctx['errors'] = errs
        ctx['field_errors'] = field_errors or {}
        ctx['form'] = request.form
        return render_template(template, **ctx)

    try:
        data = schema_cls().load(request.form)
    except ValidationError as e:
        field_errors, form_level = schema_cls.split_errors(e.messages)
        return _render_with_errors(form_level, field_errors=field_errors)

    try:
        with management_transaction(db.session):
            result = do_action(data)
    except FKValidationError as e:
        return _render_with_errors(e.errors)
    except Exception as e:  # noqa: BLE001 — surface to the user
        return _render_with_errors([f'{error_prefix}: {e}'])

    detail = success_detail(result) if callable(success_detail) else success_detail

    if success_redirect is not None:
        # Full-page navigation instead of the in-modal success fragment:
        # HX-Redirect makes htmx set window.location, so carry the
        # confirmation as a flash for the destination page to render.
        url = success_redirect(result) if callable(success_redirect) else success_redirect
        flash(f'{success_message} {detail}' if detail else success_message,
              'success')
        resp = make_response('', 200)
        resp.headers['HX-Redirect'] = url
        return resp

    triggers = success_triggers(result) if callable(success_triggers) else success_triggers
    return htmx_success_message(triggers, success_message, detail=detail)


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
