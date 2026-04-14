"""Foreign-key existence checks for HTMX/API form handlers.

Form schemas in `sam.schemas.forms` can validate shape and types but cannot
touch the database — per CLAUDE.md §9, FK existence checks stay in routes.
This module gives routes a canonical way to express those checks so they
integrate with `handle_htmx_form_post` error flow.

Usage inside a `do_action` closure::

    from webapp.utils.fk_validation import validate_fk_existence

    def _do_action(data):
        validate_fk_existence(db.session,
            (User,    data.get('lead_id'),  'project lead'),
            (Project, data.get('parent_id'), 'parent project'),
        )
        ...

`handle_htmx_form_post` catches `FKValidationError` and renders its
`.errors` list through the same path as marshmallow `ValidationError`.
"""


class FKValidationError(Exception):
    """Raised by :func:`validate_fk_existence` when any FK id does not
    resolve to a live row. Carries a list of user-facing error strings
    so the helper can render all failures together, not just the first.
    """

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('; '.join(self.errors))


def validate_fk_existence(session, *checks):
    """Verify that each ``(Model, id, label)`` triple resolves to a row.

    Args:
        session: SQLAlchemy session.
        *checks: iterable of ``(Model, id_value, label)`` tuples.
                 ``id_value=None`` is skipped (use for optional FKs).

    Raises:
        FKValidationError: if any non-None id fails to resolve. All
            failures are collected before raising so the form re-render
            shows every broken FK at once.
    """
    errors = []
    for model, id_value, label in checks:
        if id_value is None:
            continue
        if not session.get(model, id_value):
            errors.append(f'Selected {label} does not exist.')
    if errors:
        raise FKValidationError(errors)
