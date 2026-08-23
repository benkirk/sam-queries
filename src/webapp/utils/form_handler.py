"""Template-method base class for HTMX form POST/PUT handlers.

`handle_htmx_form_post` (utils/htmx.py) covers the straight-line create/edit
flow. This is for handlers that outgrew it: partial loads with PUT gating,
ORM-dependent cross-field checks, domain-exception mapping, custom success
responses, post-commit hooks.

    form_input() -> load() -> clean()
        -> [management_transaction: perform()]
        -> after_commit() -> on_success()

Every error path funnels through `render_errors()`, which re-renders `template`
with ``errors`` (form-level), ``field_errors`` (per-field, for the
form_fields.html inline macros), ``form=request.form``, and `context()`.

A route stays a thin shell -- load entities, instantiate, delegate. Error
rendering is inline field errors by design; cross-field messages
(marshmallow's ``_schema`` key, or `FormError`) land in the top alert panel.
"""

from flask import flash, make_response, render_template, request
from marshmallow import ValidationError

from sam.manage import management_transaction
from webapp.extensions import db
from webapp.utils.fk_validation import FKValidationError
from webapp.utils.htmx import htmx_success_message


class FormError(Exception):
    """User-facing rejection raised from `clean()` or `perform()`.

    Carries one or more error strings destined for the form's alert
    panel. Raise it for conditions the schema can't see — a username
    that doesn't resolve, a state transition the ORM forbids, an empty
    partial update::

        raise FormError('No changes provided.')
        raise FormError(f"User '{username}' not found.", 'Check the spelling.')
    """

    def __init__(self, *errors):
        self.errors = [str(e) for e in errors]
        super().__init__('; '.join(self.errors))


class HtmxFormHandler:
    """Base class for HTMX form handlers. Subclass, configure the class
    attributes, implement `perform()`, override hooks as needed.

    Class attributes:
        schema_cls:       marshmallow schema (HtmxFormSchema subclass —
                          `split_errors` is required).
        template:         form-fragment template re-rendered on error.
        partial:          pass ``partial=True`` to schema load (PUT flows).
        error_prefix:     prefix for unmapped exception messages.
        success_message:  primary success text.
        success_redirect: destination URL for HX-Redirect success flow —
                          a string, or a ``staticmethod`` taking the
                          `perform()` result. When set, success flashes the
                          message and responds with an HX-Redirect header
                          instead of the in-modal success fragment.
        exception_map:    ``((ExcType, message_or_callable), ...)`` —
                          translate known domain exceptions raised by
                          `perform()` into friendly form-level errors.
                          Checked in order; a callable receives the
                          exception instance.

    Hooks (override as needed — every one has a sensible default):
        form_input():           mapping fed to the schema (default
                                ``request.form``); mutate/filter here
                                (e.g. strip governance fields the current
                                user may not edit).
        load(raw):              schema load; honors `partial`.
        clean(data):            post-load, pre-transaction validation —
                                PUT gating against ``request.form`` keys,
                                ORM cross-field checks. May raise
                                `FormError` or marshmallow
                                ``ValidationError``. Returns the (possibly
                                reshaped) payload for `perform()`.
        perform(data):          REQUIRED. The write. Runs inside
                                `management_transaction` (audit logging by
                                construction). Return value threads to
                                `after_commit`/`on_success`.
        after_commit(result):   post-commit side effects (cache
                                invalidation). Not for DB writes.
        context():              extra template context for error
                                re-renders (entity being edited, dropdown
                                option queries).
        triggers(result):       HX-Trigger payload dict for success.
        detail(result):         optional secondary success line.
        on_success(result):     success response; default is
                                `htmx_success_message` or the
                                HX-Redirect flow when `success_redirect`
                                is set.
        render_errors(errors, field_errors=None):
                                error re-render; override for handlers
                                whose error response isn't the form
                                fragment (e.g. a bare alert div).

    Constructor kwargs become instance attributes — pass route-loaded ORM
    entities: ``_EditQueueHandler(queue=queue).handle()``.
    """

    schema_cls = None
    template = None
    partial = False
    error_prefix = 'Error'
    success_message = 'Saved successfully.'
    success_redirect = None
    exception_map = ()

    def __init__(self, **entities):
        for key, value in entities.items():
            setattr(self, key, value)

    # ------------------------------------------------------------------ #
    # lifecycle hooks
    # ------------------------------------------------------------------ #

    def form_input(self):
        """Raw mapping handed to the schema. Default: ``request.form``."""
        return request.form

    def load(self, raw):
        """Validate/coerce via `schema_cls`; honors `partial`."""
        return self.schema_cls().load(raw, partial=self.partial or None)

    def clean(self, data):
        """Post-load validation / payload reshaping. Default: pass-through."""
        return data

    def perform(self, data):
        """The write. Runs inside ``management_transaction``."""
        raise NotImplementedError(
            f'{type(self).__name__} must implement perform()')

    def after_commit(self, result):
        """Post-commit side effects (cache invalidation). Default: no-op."""

    def context(self):
        """Extra template context for error re-renders. Default: empty."""
        return {}

    def triggers(self, result):
        """HX-Trigger payload for the success response. Default: none."""
        return {}

    def detail(self, result):
        """Optional secondary success line. Default: none."""
        return None

    # ------------------------------------------------------------------ #
    # responses
    # ------------------------------------------------------------------ #

    def render_errors(self, errors, field_errors=None):
        """Re-render the form fragment with error context.

        ``form`` defaults to the raw ``request.form`` so the operator's input
        survives the round-trip, but ``context()`` **wins** if it supplies its
        own. A handler whose template needs an augmented form (FK pickers
        carry ``*_display`` labels that ``request.form`` has no way to hold)
        can therefore just return one from ``context()`` — building the
        context as a dict rather than passing ``form=`` as a sibling keyword
        is what makes that possible without a ``TypeError``.
        """
        ctx = {'errors': errors,
               'field_errors': field_errors or {},
               'form': request.form}
        ctx.update(self.context())
        return render_template(self.template, **ctx)

    def on_success(self, result):
        """Success response: HX-Redirect when `success_redirect` is set,
        otherwise the generic in-modal success fragment."""
        detail = self.detail(result)
        if self.success_redirect is not None:
            url = (self.success_redirect(result)
                   if callable(self.success_redirect) else self.success_redirect)
            flash(f'{self.success_message} {detail}' if detail
                  else self.success_message, 'success')
            resp = make_response('', 200)
            resp.headers['HX-Redirect'] = url
            return resp
        return htmx_success_message(
            self.triggers(result), self.success_message, detail=detail)

    # ------------------------------------------------------------------ #
    # driver
    # ------------------------------------------------------------------ #

    def handle(self):
        """Run the full lifecycle; always returns a Flask response."""
        try:
            payload = self.clean(self.load(self.form_input()))
        except ValidationError as e:
            if isinstance(e.messages, dict):
                field_errors, form_level = self.schema_cls.split_errors(e.messages)
            else:  # ValidationError('msg') from clean() — form-level
                field_errors, form_level = {}, list(e.messages)
            return self.render_errors(form_level, field_errors)
        except (FormError, FKValidationError) as e:
            return self.render_errors(e.errors)

        try:
            with management_transaction(db.session):
                result = self.perform(payload)
        except (FormError, FKValidationError) as e:
            return self.render_errors(e.errors)
        except Exception as e:  # noqa: BLE001 — surface to the user
            for exc_type, msg in self.exception_map:
                if isinstance(e, exc_type):
                    return self.render_errors([msg(e) if callable(msg) else msg])
            return self.render_errors([f'{self.error_prefix}: {e}'])

        self.after_commit(result)
        return self.on_success(result)


class FlattenedFieldErrors:
    """Mixin for handlers whose template has no per-field form_fields.html
    macros: fold field errors into the top alert panel (labeled, matching
    the legacy ``flatten_errors`` presentation) so they stay visible.
    """

    def render_errors(self, errors, field_errors=None):
        flat = [f'{field.replace("_", " ").title()}: {msg}'
                for field, msgs in (field_errors or {}).items()
                for msg in msgs]
        return super().render_errors(list(errors) + flat, {})


class _KwargFormHandler(HtmxFormHandler):
    """Adapter behind `handle_htmx_form_post` (utils/htmx.py).

    Configures the lifecycle from that function's kwargs so its existing
    call sites keep their exact signature — the straight-line flow stays
    a one-call function; only handlers that need the extra hooks subclass
    `HtmxFormHandler` directly.
    """

    def __init__(self, *, schema_cls, template, do_action, success_triggers,
                 success_message, success_detail, success_redirect,
                 error_prefix, extra_context, context_fn, after_commit):
        super().__init__()
        self.schema_cls = schema_cls
        self.template = template
        self.error_prefix = error_prefix
        self.success_message = success_message
        self.success_redirect = success_redirect
        self._do_action = do_action
        self._success_triggers = success_triggers
        self._success_detail = success_detail
        self._extra_context = extra_context
        self._context_fn = context_fn
        self._after_commit = after_commit

    def perform(self, data):
        return self._do_action(data)

    def after_commit(self, result):
        if self._after_commit is not None:
            self._after_commit(result)

    def context(self):
        ctx = {}
        if self._extra_context:
            ctx.update(self._extra_context)
        if self._context_fn is not None:
            ctx.update(self._context_fn())
        return ctx

    def triggers(self, result):
        t = self._success_triggers
        return t(result) if callable(t) else t

    def detail(self, result):
        d = self._success_detail
        return d(result) if callable(d) else d
