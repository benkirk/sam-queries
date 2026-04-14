import json
from flask import make_response, render_template, request
from marshmallow import ValidationError

from webapp.extensions import db
from sam.manage import management_transaction


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
        success_triggers:  HX-Trigger dict, e.g.
                           {'closeActiveModal': {}, 'reloadFacilitiesCard': {}}
        success_message:   Primary success text (default 'Saved successfully.').
        error_prefix:      Prefix for unexpected exception messages
                           (e.g. 'Error creating facility').
        extra_context:     Static dict merged into the re-render context — pass
                           the entity being edited here, e.g. {'facility': facility}.
        context_fn:        Optional callable returning a dict of additional
                           re-render context — use this when the context needs
                           a fresh DB query (e.g. dropdown options).

    Returns: Flask response (rendered fragment or htmx_success_message).
    """
    def _render_with_errors(errs):
        ctx = {}
        if extra_context:
            ctx.update(extra_context)
        if context_fn is not None:
            ctx.update(context_fn())
        ctx['errors'] = errs
        ctx['form'] = request.form
        return render_template(template, **ctx)

    try:
        data = schema_cls().load(request.form)
    except ValidationError as e:
        return _render_with_errors(schema_cls.flatten_errors(e.messages))

    try:
        with management_transaction(db.session):
            do_action(data)
    except Exception as e:  # noqa: BLE001 — surface to the user
        return _render_with_errors([f'{error_prefix}: {e}'])

    return htmx_success_message(success_triggers, success_message)


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
