"""
Query layer for the XRAS integration API (`webapp/api/xras/`).

Ports the Hibernate named queries that legacy Java SAM 2.0.3 actually serves,
from `src/main/resources/hibernate/xras/namedQuery.xml`. Naming follows the
other legacy-compat query modules (`queue_access.py`,
`wallclock_exemption_access.py`).

Why the named query and not `XrasUserView`
------------------------------------------

The ORM exposes an `xras_user` view (`sam/integration/xras_views.py`) that looks
like the obvious source for `/people`. It is the wrong one, for two reasons:

1. **It is ~560x slower for a single lookup.** Its `GROUP BY u.user_id`
   materialises all 28k rows before any filter applies, so a username predicate
   layered on top cannot push down. Measured locally: 0.409 s through the view
   versus 0.0007 s with the predicate inside the grouped query.
2. **It computes a different email.** The view uses
   `COALESCE(MIN(ea1), MIN(ea2), ...)` — a per-tier coalesce — where the named
   query uses `ANY_VALUE(COALESCE(ea1, ea2, ...))`, a per-row one. For a user
   with several addresses these select different values, so porting the view
   would ship a silent data divergence.

The named query already carries `(:username IS NULL OR username = :username)`,
which is exactly the shape needed to serve both endpoints from one statement.

See `docs/plans/XRAS_REIMPLEMENTATION.md` section 4.2.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# `PersonDTO`'s Java field-declaration order, which is the JSON key order.
# Note this differs from the SQL alias order (phone/organization are swapped),
# so callers must project through this tuple rather than trusting the row.
PERSON_FIELDS = (
    'username', 'firstName', 'middleName', 'lastName',
    'organization', 'academicStatus', 'phone', 'email',
)

#: Verbatim port of `identityServicePersons` (namedQuery.xml:7-60).
#:
#: Two faithfulness notes worth not "fixing":
#:
#: - The `phone` expression looks like a priority ranking but is not one. The
#:   `MIN(CASE ...)` is used only as a null test — `ANY_VALUE(p.phone_number)`
#:   then returns an arbitrary phone row. Legacy behaviour, reproduced.
#: - `login_type_id = 1` is the only filter. There is deliberately no
#:   active/deleted predicate, so `/people` publishes every user who ever
#:   existed (22k of 28k are inactive). See section 7 of the plan doc.
#:
#: `ORDER BY MIN(u.user_id)` is ours, not legacy's. The named query has no
#: `ORDER BY` at all, so legacy's row order is a MySQL `GROUP BY` artifact that
#: happens to be user_id-ascending; stating it reproduces the observed 3.8 MB
#: roster byte-for-byte while making the order deterministic rather than
#: incidental. `MIN()` (not a bare column) keeps it legal under
#: `ONLY_FULL_GROUP_BY`, which the dev and CI databases enable and production
#: does not.
_SQL_PEOPLE = text("""
    SELECT u.username AS username,
           IF(u.nickname IS NOT NULL, u.nickname, u.first_name) AS firstName,
           u.middle_name AS middleName,
           u.last_name  AS lastName,
           ANY_VALUE(IF((MIN((CASE pt.phone_type
                   WHEN 'Ucar Office'     THEN 0
                   WHEN 'External Office' THEN 1
                   WHEN 'Cell'            THEN 2
                   WHEN 'Other'           THEN 3
                   WHEN 'Home'            THEN 4
                   WHEN 'Fax'             THEN 5
                   ELSE NULL END)) IS NOT NULL), p.phone_number, NULL)) AS phone,
           ANY_VALUE(COALESCE(MIN(i.name),
                              CONCAT('UCAR/NCAR:', o.acronym))) AS organization,
           ANY_VALUE(COALESCE(ea1.email_address, ea2.email_address,
                              ea3.email_address, ea4.email_address)) AS email,
           ac.description AS academicStatus
      FROM users u
      LEFT JOIN phone p       ON u.user_id = p.user_id
      LEFT JOIN phone_type pt ON p.ext_phone_type_id = pt.ext_phone_type_id
      LEFT JOIN user_organization uo ON u.user_id = uo.user_id
            AND uo.start_date <= NOW()
            AND (uo.end_date >= NOW() OR uo.end_date IS NULL)
      LEFT JOIN organization o ON uo.organization_id = o.organization_id
      LEFT JOIN user_institution ui ON u.user_id = ui.user_id
            AND ui.start_date <= NOW()
            AND (ui.end_date >= NOW() OR ui.end_date IS NULL)
      LEFT JOIN institution i ON ui.institution_id = i.institution_id
      LEFT JOIN email_address ea1 ON u.user_id = ea1.user_id
            AND ea1.is_primary IS TRUE
            AND NOT (ea1.email_address LIKE '%ucar.edu%')
      LEFT JOIN email_address ea2 ON u.user_id = ea2.user_id
            AND NOT (ea2.email_address LIKE '%ucar.edu%')
      LEFT JOIN email_address ea3 ON u.user_id = ea3.user_id
            AND ea3.is_primary IS TRUE
      LEFT JOIN email_address ea4 ON u.user_id = ea4.user_id
            AND ea4.is_primary IS FALSE
      LEFT JOIN academic_status ac
            ON u.academic_status_id = ac.academic_status_id
     WHERE u.login_type_id = 1
       AND (:username IS NULL OR u.username = :username)
     GROUP BY u.username, firstName, u.middle_name, u.last_name, ac.description
     ORDER BY MIN(u.user_id)
""")

#: Organizations whose primary name is fixed rather than derived by walking
#: parentage. Ported from `UCAROrgNameQuery.acronymToPrimaryOrgNameMap`.
_PRIMARY_ORG_NAMES = {
    'NCAR': 'NCAR',
    'UCAR': 'UCAR',
    'UCP': 'UCAR Community Programs',
}

_INTERNAL_ORG_PREFIX = 'UCAR/NCAR:'
_NCAR = 'NCAR'

#: Guard against a parentage cycle in `organization`. Real depth is ~4.
_MAX_PARENT_WALK = 32


class _OrgNameFixup:
    """Ports `DefaultOrgNameMappingQuery` + `UCAROrgNameQuery`.

    The SQL emits `UCAR/NCAR:<acronym>` for internal users; legacy rewrites that
    into a display name by walking organization parentage. Zero raw
    `UCAR/NCAR:` strings survive in production output, so the rewrite is
    unconditional.

    An **unknown acronym maps to `None`**, which drops the `organization` key
    from the response entirely (`PersonDTO` is `NON_NULL`). That is legacy
    behaviour, and it is why this returns `Optional[str]` rather than falling
    back to the input.
    """

    def __init__(self, session: Session):
        from sam.core.organizations import Organization

        rows = session.query(
            Organization.organization_id,
            Organization.acronym,
            Organization.parent_org_id,
            Organization.deleted,
        ).all()

        self._by_id = {r.organization_id: r for r in rows}
        # `CachingOrganizationQuery` lets an active organization win a colliding
        # acronym; inactive duplicates are re-keyed out of the way. Insert
        # inactive first so active overwrites.
        self._by_acronym: Dict[str, Any] = {}
        for r in sorted(rows, key=lambda r: not bool(r.deleted)):
            self._by_acronym[r.acronym] = r

    def apply(self, organization: Optional[str]) -> Optional[str]:
        """Rewrite one `organization` value; pass non-internal names through."""
        if organization is None or not organization.startswith(_INTERNAL_ORG_PREFIX):
            return organization
        return self._primary_name(organization[len(_INTERNAL_ORG_PREFIX):])

    def _primary_name(self, acronym: str) -> Optional[str]:
        org = self._by_acronym.get(acronym)
        if org is None:
            return None
        if org.acronym in _PRIMARY_ORG_NAMES:
            return _PRIMARY_ORG_NAMES[org.acronym]
        return self._derive(org)

    def _derive(self, org) -> Optional[str]:
        for _ in range(_MAX_PARENT_WALK):
            parent = self._by_id.get(org.parent_org_id)
            if parent is None:
                return 'UCAR'
            if parent.acronym == _NCAR:
                return f'{_NCAR}/{org.acronym}'
            if parent.acronym in _PRIMARY_ORG_NAMES:
                return _PRIMARY_ORG_NAMES[parent.acronym]
            org = parent
        return None


def _rows_to_people(session: Session, rows) -> List[Dict[str, Any]]:
    """Project rows into `PersonDTO` field order and apply the org fixup.

    Values stay `None` here rather than being dropped — omission is the
    serializer's job, and it is per-DTO (see `webapp/api/xras/serialize.py`).
    """
    people = [{field: getattr(row, field) for field in PERSON_FIELDS}
              for row in rows]

    if any(p['organization'] and p['organization'].startswith(_INTERNAL_ORG_PREFIX)
           for p in people):
        fixup = _OrgNameFixup(session)
        for person in people:
            person['organization'] = fixup.apply(person['organization'])

    return people


def get_people(session: Session) -> List[Dict[str, Any]]:
    """Every person on the XRAS roster, in `users.user_id` order.

    ~28k rows / ~3.8 MB of JSON. Legacy serves this from a nightly 03:00 cron in
    ~1.1 s.
    """
    rows = session.execute(_SQL_PEOPLE, {'username': None}).fetchall()
    return _rows_to_people(session, rows)


def get_person(session: Session, username: str) -> Optional[Dict[str, Any]]:
    """One person, or `None` if the username is not on the roster.

    The predicate is bound into the grouped query, not applied on top of it —
    that is the whole reason this module exists rather than reusing the view.
    """
    rows = session.execute(_SQL_PEOPLE, {'username': username}).fetchall()
    people = _rows_to_people(session, rows)
    return people[0] if people else None
