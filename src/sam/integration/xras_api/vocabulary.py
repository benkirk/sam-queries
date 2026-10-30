"""XRAS vocabularies (role, action, request and document types) and stable wire-field names.

Both the write client (:mod:`sam.integration.xras_api.admin_client`) and the
read-side report parsing (:mod:`sam.queries.xras_requests`) need the numeric
role-type ids and the id<->name<->display mapping. Before this module they were
stated twice: the write client owned the authoritative :data:`ROLE_TYPES`
table, and the read path re-hardcoded ``PI_ROLE_TYPE_ID = 13`` /
``ADMIN_ROLE_TYPE_ID = 14`` with a comment explaining it would not import from
the write client "which does not belong on the read path."

That comment is the whole reason this module exists: it is the one
authoritative home for the table, carrying **no imports of its own** beyond the
standard library — so the read path, the schemas and the CLI depend on the
vocabulary itself rather than restating the ids or reaching into the write
client's module for them.

The three-spellings design of :class:`RoleType` is unchanged; see its docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

#: The stable wire-field name for a resource's repository key, as it appears in
#: every XRAS payload (``resources[].resourceRepositoryKey``) and in the SQL
#: aliases/ORM columns that mirror it. A single symbol for the **dict-key
#: reads** in the outbound parsers; the value is pinned by
#: ``tests/unit/xras/test_xras_wire_vocabulary.py``. Definitional sites (ORM
#: ``Column`` names, ``AS resourceRepositoryKey`` in raw SQL, the marshmallow
#: field name) keep the literal, where it reads as schema rather than a lookup.
RESOURCE_REPOSITORY_KEY = 'resourceRepositoryKey'


@dataclass(frozen=True)
class RoleType:
    """One NCAR role type, in all three of the spellings the API uses.

    ``type_id`` is what the roster reports and the projcode-keyed route wants;
    ``name`` is what the ``/v1/requests/<rid>/roles/<roleType>/<username>``
    route wants; ``display`` is XRAS's own operator vocabulary, which the UI
    should render so that SAM and the XRAS admin app read alike.
    """

    type_id: int
    name: str
    display: str


#: ``GET /v1/types/roles`` for the NCAR process, read live 2026-08-21. There is
#: no co-PI in this process. PRIVILEGE(#10): three spellings are carried only
#: because the two role families disagree on the encoding.
#: Hardcoded rather than fetched: it is three rows
#: that have not changed since the process opened, a wrong value here is a
#: 400 rather than a silent mis-write, and the alternative is a network call in
#: the path of rendering a form.
ROLE_TYPES: Tuple[RoleType, ...] = (
    RoleType(13, 'PI', 'Project Lead'),
    RoleType(14, 'Allocation Manager', 'Project Admin'),
    RoleType(19, 'User', 'User'),
)

_BY_ID = {r.type_id: r for r in ROLE_TYPES}
_BY_NAME = {r.name.casefold(): r for r in ROLE_TYPES}

#: The roleTypeId that owns a request. Withdraw, re-submit and role changes are
#: all authorized against a role-holder, and probe P2 showed the PI and the
#: Allocation Manager are **not** interchangeable — the same action validated
#: for the PI and failed for the Allocation Manager. Impersonate this one.
#: PRIVILEGE(#5): an ``admin``-context key might act as SAM itself and retire
#: the whole impersonation apparatus.
PI_ROLE_TYPE_ID = _BY_NAME['pi'].type_id

#: XRAS ``Allocation Manager`` — SAM's language for it is **Project Admin**.
ADMIN_ROLE_TYPE_ID = _BY_NAME['allocation manager'].type_id

#: The plain member role.
USER_ROLE_TYPE_ID = _BY_NAME['user'].type_id


def role_type(key: Any) -> RoleType:
    """Resolve a role type from an id, a wire name, or a :class:`RoleType`.

    Raises:
        ValueError: unknown role type. Deliberately loud — the alternative is
            posting an unrecognised value into a URL path.
    """
    if isinstance(key, RoleType):
        return key
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        found = _BY_ID.get(int(key))
    else:
        found = _BY_NAME.get(str(key).strip().casefold())
    if found is None:
        raise ValueError(f'unknown XRAS role type {key!r}; '
                         f'expected one of {[r.name for r in ROLE_TYPES]}')
    return found


#: ``GET /v1/types/all`` ``actionTypes`` for the NCAR process, read 2026-10-27. The rule-book
#: route takes the id; the action routes take the name.
ACTION_TYPES = {
    'Advance': 500015, 'New': 500019, 'Renewal': 500021, 'Transfer': 500018,
    'Supplement': 500020, 'Extension': 500017, 'Appeal': 500016,
    'Adjustment': 500168, 'Final Report': 500022, 'Date Adjustment': 500334,
}

#: The action types SAM files for a user, in the order the picker offers them.
SUBMIT_ACTION_TYPES = ('Extension', 'Supplement', 'Renewal', 'New')

#: ``GET /v1/funding_agencies``: NSF, the one agency SAM looks a grant up for (XRAS_DEV_PROBES § 2).
NSF_FUNDING_AGENCY_ID = 1
#: The push spells the agency by name; ``add_grant`` sends its id.
NSF_FUNDING_AGENCY_NAME = 'National Science Foundation'

_ACTION_BY_NAME = {name.casefold(): name for name in ACTION_TYPES}


def action_type_name(key: Any) -> str:
    """The canonical action-type name for a name (any case) or an id; ``ValueError`` if unknown."""
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        found = next((n for n, i in ACTION_TYPES.items() if i == int(key)), None)
    else:
        found = _ACTION_BY_NAME.get(str(key).strip().casefold())
    if found is None:
        raise ValueError(f'unknown XRAS action type {key!r}')
    return found


def action_type_id(key: Any) -> int:
    """The id for an action-type name or id; ``ValueError`` if unknown."""
    return ACTION_TYPES[action_type_name(key)]


#: ``actions[].documents[].documentType`` codes seen on NCAR requests (2026-10-12,
#: 400 Approved requests), and their display names. ``GET /v1/types/documents``
#: is the authority; an unseen code renders humanized rather than failing.
DOCUMENT_TYPE_LABELS = {
    'Advisor_Letter': 'Advisor letter',
    'Main_Doc': 'Main document',
    'Progress_Report': 'Progress report',
    'Supp_Info': 'Supporting information',
}


def document_type_label(code: Any) -> str:
    """The display name for a document type code; humanized when unknown."""
    if not code:
        return 'Document'
    text = str(code)
    return DOCUMENT_TYPE_LABELS.get(text) or text.replace('_', ' ').capitalize()


def resource_label(resource, resource_id=None) -> str:
    """A resource row's display name, else its name, else ``resource <id>``."""
    row = resource or {}
    rid = resource_id if resource_id is not None else row.get('resourceId')
    return (row.get('displayResourceName') or row.get('resourceName')
            or (f'resource {rid}' if rid is not None else 'resource'))
