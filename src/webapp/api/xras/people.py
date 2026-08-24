"""
`GET /api/xras/v1/people` and `GET /api/xras/v1/people/{username}`.

94% of the traffic on this surface and the first step of the staged cutover.
Both are **unwrapped** — legacy's `IdentityServiceController` returns
`List<PersonDTO>` / `PersonDTO` directly, not a `ResponseWrapper` — which makes
them the only two endpoints here without the `{message, result}` envelope.

Production shape, measured 2026-08-05::

    GET /people             200, ~3.84 MB, 28,259 records, 1.1 s (nightly cron)
    GET /people/benkirk     200, 170 B
    GET /people/{miss}      404, len(username) + 47 B
"""

from flask import abort
from webapp.extensions import db

from sam.queries.xras_access import get_people, get_person

from . import bp, xras_api_required
from .serialize import omit_none, xras_response


@bp.route('/people', methods=['GET'])
@xras_api_required()
def list_people():
    """The full roster, as a bare JSON array.

    No active/deleted filter — reproduced bug-for-bug. XRAS's identity matching
    may depend on resolving historical usernames, and a 404 where a 200 used to
    be is a change we cannot observe from our side. See plan doc section 7.

    Legacy requests this as a literal `GET /api/xras/v1/people?` — a bare
    trailing `?` — which Werkzeug routes here with an empty query string.
    """
    people = [omit_none(person) for person in get_people(db.session)]
    return xras_response(people, envelope=False)


@bp.route('/people/<username>', methods=['GET'])
@xras_api_required()
def get_person_by_username(username):
    """One person, as a bare JSON object.

    The 404 body is `{"message":"username=<u> not found","result":null}`, whose
    length is a closed form: **len(username) + 47**. Note the `requests/*`
    family uses different wording for the same condition (`User <u> not found`),
    so the message is passed to `abort` rather than centralized.
    """
    person = get_person(db.session, username)
    if person is None:
        abort(404, f'username={username} not found')
    return xras_response(omit_none(person), envelope=False)
