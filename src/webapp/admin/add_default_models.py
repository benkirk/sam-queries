"""Register a default Flask-Admin view for every SAM ORM model.

Models are auto-detected from the SAM declarative registry, so a newly
added ORM model shows up under the "Everything" category with no edit
here. Endpoint slugs derive from the class name via ``_camel_to_snake``
— the same algorithm the original generated listing used, verified to
reproduce all of its endpoints exactly, so pre-existing
``/database/default_views/*`` URLs are stable.
"""

import re

import sam  # noqa: F401 — importing sam registers every model on Base
from sam.base import Base

from .default_model_views import SAMModelView


#: Promotion path: map a Model here to a SAMModelView subclass when it
#: needs custom behavior. Everything else is served by SAMModelView
#: directly — Flask-Admin distinguishes views by endpoint, not by class.
_CUSTOM_VIEWS = {}


def _camel_to_snake(name: str) -> str:
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def sam_model_classes():
    """Every ORM class mapped on the SAM declarative Base, name-sorted
    (name order == menu order within the category).

    Excludes models carrying a ``__bind_key__`` — under Flask the
    system_status models share this registry but live in a different
    database, and "Everything" means everything in the SAM DB.
    """
    classes = {m.class_ for m in Base.registry.mappers}
    return sorted((c for c in classes
                   if getattr(c, '__bind_key__', None) is None),
                  key=lambda cls: cls.__name__)


def add_default_views(app, admin):
    """Add to Flask-Admin default model views"""

    # Import db (Flask-SQLAlchemy instance) to pass to Flask-Admin views.
    from webapp.extensions import db

    for model in sam_model_classes():
        view_cls = _CUSTOM_VIEWS.get(model, SAMModelView)
        admin.add_view(view_cls(
            model, db,
            name=model.__name__,
            endpoint=f'default_views/{_camel_to_snake(model.__name__)}',
            category='Everything',
        ))
