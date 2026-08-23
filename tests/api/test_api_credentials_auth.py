"""
Tests for DB-backed API-key authentication (legacy `api_credentials` table).

Covers the wiring added so legacy SAM API clients can authenticate against
their existing `api_credentials` rows on the new API paths:

  - ApiCredentials.as_api_key_map (enabled filter, role resolution, hash)
  - _verify_api_key precedence: config['API_KEYS'] always wins over the DB
  - _get_db_api_keys TTL cache + graceful degradation on DB error
  - Both decorators accept a DB-sourced key and stash g.api_key_source/roles

Precedence and cache logic is exercised by monkeypatching the DB-map loader,
because Flask routes read through Flask-SQLAlchemy's `db.session` (a separate
connection that only sees committed rows) — so an uncommitted factory row is
invisible to an end-to-end HTTP request. The `as_api_key_map` test therefore
uses the raw `session` fixture directly.
"""

import base64

import bcrypt
import pytest
from flask import g

from sam.security.roles import ApiCredentials
from webapp.utils import api_auth
from webapp.utils.rbac import Permission

from factories import make_api_credentials


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


@pytest.fixture(autouse=True)
def _reset_db_key_cache():
    """The api_auth DB-key cache is a process-global dict — wipe it around each
    test so cached maps never leak between tests."""
    api_auth._DB_KEY_CACHE.update(at=None, map={})
    yield
    api_auth._DB_KEY_CACHE.update(at=None, map={})


@pytest.fixture
def api_app(app):
    """App with a known config API_KEYS entry (rounds=4) for the config tier.

    Restores the original API_KEYS so the mutation doesn't leak into other tests
    sharing the session-scoped `app`.
    """
    original = app.config.get("API_KEYS")
    good_hash = bcrypt.hashpw(b"good-password", bcrypt.gensalt(rounds=4)).decode()
    app.config["API_KEYS"] = {"testuser": good_hash}
    try:
        yield app
    finally:
        app.config["API_KEYS"] = original


# ---------------------------------------------------------------------------
# ApiCredentials.as_api_key_map
# ---------------------------------------------------------------------------

class TestAsApiKeyMap:
    def test_includes_enabled_excludes_disabled(self, session):
        enabled = make_api_credentials(session, password="pw1", enabled=True, roles=["ROLEA"])
        disabled = make_api_credentials(session, password="pw2", enabled=False)

        m = ApiCredentials.as_api_key_map(session)

        assert enabled.username in m
        assert disabled.username not in m

    def test_resolves_roles_and_hash(self, session):
        cred = make_api_credentials(session, password="pw1", roles=["ROLEA", "ROLEB"])

        m = ApiCredentials.as_api_key_map(session)
        entry = m[cred.username]

        assert sorted(entry["roles"]) == ["ROLEA", "ROLEB"]
        assert bcrypt.checkpw(b"pw1", entry["hash"].encode())

    def test_no_roles_gives_empty_list(self, session):
        cred = make_api_credentials(session, password="pw1")
        m = ApiCredentials.as_api_key_map(session)
        assert m[cred.username]["roles"] == []


# ---------------------------------------------------------------------------
# _verify_api_key precedence
# ---------------------------------------------------------------------------

class TestVerifyPrecedence:
    def test_config_username_uses_config_only(self, api_app, monkeypatch):
        """A username defined in config['API_KEYS'] is verified against the
        config hash only — a same-named DB row can neither grant nor block it."""
        db_hash = bcrypt.hashpw(b"db-password", bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setattr(
            api_auth, "_get_db_api_keys",
            lambda: {"testuser": {"hash": db_hash, "roles": ["shadow"]}},
        )
        with api_app.test_request_context("/"):
            ident = api_auth._verify_api_key("testuser", "good-password")
            assert ident == {"username": "testuser", "source": "config", "roles": []}
            # DB password must NOT authenticate a config-owned username
            assert api_auth._verify_api_key("testuser", "db-password") is None

    def test_db_fallback_for_non_config_username(self, api_app, monkeypatch):
        db_hash = bcrypt.hashpw(b"legacy-pw", bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setattr(
            api_auth, "_get_db_api_keys",
            lambda: {"legacyusr": {"hash": db_hash, "roles": ["ADMIN"]}},
        )
        with api_app.test_request_context("/"):
            ident = api_auth._verify_api_key("legacyusr", "legacy-pw")
            assert ident == {"username": "legacyusr", "source": "db", "roles": ["ADMIN"]}
            assert api_auth._verify_api_key("legacyusr", "wrong") is None
            assert api_auth._verify_api_key("unknown", "x") is None

    def test_accepts_legacy_2a_hash(self, api_app, monkeypatch):
        """Legacy SAM stores $2a$-prefixed bcrypt hashes; our verify path must
        accept them. For ASCII passwords a $2a$-prefixed hash verifies identically
        to $2b$, so we rewrite the version to exercise the $2a$ code path."""
        legacy_hash = (
            bcrypt.hashpw(b"legacy-pw", bcrypt.gensalt(rounds=4))
            .replace(b"$2b$", b"$2a$", 1)
            .decode()
        )
        assert legacy_hash.startswith("$2a$")
        monkeypatch.setattr(
            api_auth, "_get_db_api_keys",
            lambda: {"legacyusr": {"hash": legacy_hash, "roles": []}},
        )
        with api_app.test_request_context("/"):
            ident = api_auth._verify_api_key("legacyusr", "legacy-pw")
            assert ident is not None and ident["source"] == "db"


# ---------------------------------------------------------------------------
# _get_db_api_keys TTL cache
# ---------------------------------------------------------------------------

class TestDbKeyCache:
    def test_ttl_zero_refreshes_every_call(self, app, monkeypatch):
        """TestingConfig sets API_KEYS_DB_TTL=0 -> no caching."""
        calls = {"n": 0}

        def fake_map(cls, session):
            calls["n"] += 1
            return {"dbonly": {"hash": "x", "roles": []}}

        monkeypatch.setattr(ApiCredentials, "as_api_key_map", classmethod(fake_map))
        with app.app_context():
            api_auth._get_db_api_keys()
            api_auth._get_db_api_keys()
        assert calls["n"] == 2

    def test_positive_ttl_serves_cache(self, app, monkeypatch):
        calls = {"n": 0}

        def fake_map(cls, session):
            calls["n"] += 1
            return {"dbonly": {"hash": "x", "roles": []}}

        monkeypatch.setattr(ApiCredentials, "as_api_key_map", classmethod(fake_map))
        monkeypatch.setitem(app.config, "API_KEYS_DB_TTL", 300)
        with app.app_context():
            api_auth._get_db_api_keys()
            api_auth._get_db_api_keys()
        assert calls["n"] == 1

    def test_disabled_returns_empty(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "API_KEYS_DB_ENABLED", False)
        with app.app_context():
            assert api_auth._get_db_api_keys() == {}

    def test_db_error_serves_last_good_map(self, app, monkeypatch):
        def boom(cls, session):
            raise RuntimeError("db down")

        monkeypatch.setattr(ApiCredentials, "as_api_key_map", classmethod(boom))
        api_auth._DB_KEY_CACHE.update(at=None, map={"cached": {"hash": "h", "roles": []}})
        with app.app_context():
            result = api_auth._get_db_api_keys()
        assert result == {"cached": {"hash": "h", "roles": []}}


# ---------------------------------------------------------------------------
# Decorator end-to-end (token path)
# ---------------------------------------------------------------------------

class TestDecoratorsAcceptDbKey:
    def _db_loader(self, monkeypatch, roles):
        db_hash = bcrypt.hashpw(b"legacy-pw", bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setattr(
            api_auth, "_get_db_api_keys",
            lambda: {"legacyusr": {"hash": db_hash, "roles": roles}},
        )

    def test_login_or_token_accepts_db_key_and_sets_identity(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=["R"])
        captured = {}

        @api_auth.login_or_token_required(Permission.VIEW_RESOURCES)
        def view():
            captured.update(
                user=g.api_key_user, source=g.api_key_source, roles=g.api_key_roles
            )
            return "ok", 200

        with api_app.test_request_context(
            "/", headers={"Authorization": _basic("legacyusr", "legacy-pw")}
        ):
            _, status = view()
        assert status == 200
        assert captured == {"user": "legacyusr", "source": "db", "roles": ["R"]}

    def test_login_or_token_rejects_bad_db_password(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=[])

        @api_auth.login_or_token_required(Permission.VIEW_RESOURCES)
        def view():
            return "ok", 200

        with api_app.test_request_context(
            "/", headers={"Authorization": _basic("legacyusr", "wrong")}
        ):
            resp = view()
        assert resp[1] == 401

    def test_api_key_required_accepts_db_key(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=[])
        captured = {}

        @api_auth.api_key_required
        def view():
            captured.update(user=g.api_key_user, source=g.api_key_source)
            return "ok", 200

        with api_app.test_request_context(
            "/", method="POST",
            headers={"Authorization": _basic("legacyusr", "legacy-pw")},
        ):
            _, status = view()
        assert status == 200
        assert captured == {"user": "legacyusr", "source": "db"}


# ---------------------------------------------------------------------------
# roles= / deny= on login_or_token_required
# ---------------------------------------------------------------------------

class TestRoleGatedTokenAuth:
    """`roles=` turns g.api_key_roles from a logging breadcrumb into a gate.

    The role names are free-text `role.name` values reached through
    `role_api_credentials`; ROLE_XRAS is the first real consumer.
    """

    def _db_loader(self, monkeypatch, roles):
        db_hash = bcrypt.hashpw(b"legacy-pw", bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setattr(
            api_auth, "_get_db_api_keys",
            lambda: {"legacyusr": {"hash": db_hash, "roles": roles}},
        )

    def _view(self, **kwargs):
        @api_auth.login_or_token_required(**kwargs)
        def view():
            return "ok", 200
        return view

    def _call(self, api_app, view, username="legacyusr", password="legacy-pw"):
        headers = {} if username is None else {"Authorization": _basic(username, password)}
        with api_app.test_request_context("/", headers=headers):
            result = view()
        return result if isinstance(result, tuple) else (result, 200)

    def test_key_holding_the_role_is_admitted(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=["ROLE_XRAS", "OTHER"])
        _, status = self._call(api_app, self._view(roles=("ROLE_XRAS",)))
        assert status == 200

    def test_key_without_the_role_is_403(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=["SOMETHING_ELSE"])
        resp = self._call(api_app, self._view(roles=("ROLE_XRAS",)))
        assert resp[1] == 403

    def test_any_one_of_several_roles_suffices(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=["ROLE_B"])
        _, status = self._call(api_app, self._view(roles=("ROLE_A", "ROLE_B")))
        assert status == 200

    def test_bad_password_is_401_not_403(self, api_app, monkeypatch):
        """Authn failure must not be reported as an authz failure."""
        self._db_loader(monkeypatch, roles=["ROLE_XRAS"])
        resp = self._call(api_app, self._view(roles=("ROLE_XRAS",)), password="wrong")
        assert resp[1] == 401

    def test_config_sourced_key_fails_closed(self, api_app, monkeypatch):
        """Config keys always resolve with roles=[] — a role gate must reject them.

        This is the documented `API_KEYS_SAMUEL` hazard: defining a key in config
        that a roles-gated route expects silently locks that route out while
        every other route keeps working.
        """
        self._db_loader(monkeypatch, roles=["ROLE_XRAS"])
        resp = self._call(
            api_app, self._view(roles=("ROLE_XRAS",)),
            username="testuser", password="good-password",
        )
        assert resp[1] == 403

    def test_session_path_is_closed_when_roles_given(self, api_app, monkeypatch):
        """A browser session carries no API-key roles, so there is nothing that
        could satisfy the gate — an unauthenticated request is a 401, and we never
        reach the Flask-Login branch."""
        self._db_loader(monkeypatch, roles=["ROLE_XRAS"])
        resp = self._call(api_app, self._view(roles=("ROLE_XRAS",)), username=None)
        assert resp[1] == 401

    def test_deny_hook_owns_the_error_bodies(self, api_app, monkeypatch):
        """`deny` lets a legacy-contract blueprint supply its own wire format."""
        self._db_loader(monkeypatch, roles=["NOPE"])
        seen = []

        def deny(status, message):
            seen.append((status, message))
            return "custom", status

        body, status = self._call(
            api_app, self._view(roles=("ROLE_XRAS",), deny=deny))
        assert (body, status) == ("custom", 403)
        assert seen == [(403, 'Forbidden - insufficient permissions')]

    def test_deny_hook_also_owns_401(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=["ROLE_XRAS"])
        body, status = self._call(
            api_app,
            self._view(roles=("ROLE_XRAS",), deny=lambda s, m: ("custom", s)),
            password="wrong",
        )
        assert (body, status) == ("custom", 401)


class TestRolesDefaultIsUnchanged:
    """The 20 existing call sites pass only the positional `permission`.

    These pin that omitting `roles`/`deny` leaves both the admit decision and the
    error bytes exactly as they were.
    """

    def _db_loader(self, monkeypatch, roles):
        db_hash = bcrypt.hashpw(b"legacy-pw", bcrypt.gensalt(rounds=4)).decode()
        monkeypatch.setattr(
            api_auth, "_get_db_api_keys",
            lambda: {"legacyusr": {"hash": db_hash, "roles": roles}},
        )

    def test_roleless_key_still_admitted_without_the_gate(self, api_app, monkeypatch):
        self._db_loader(monkeypatch, roles=[])

        @api_auth.login_or_token_required(Permission.VIEW_RESOURCES)
        def view():
            return "ok", 200

        with api_app.test_request_context(
            "/", headers={"Authorization": _basic("legacyusr", "legacy-pw")}
        ):
            _, status = view()
        assert status == 200

    def test_401_body_and_header_unchanged(self, api_app, monkeypatch):
        """Legacy-compat blueprints must keep byte-identical 401s."""
        self._db_loader(monkeypatch, roles=[])

        @api_auth.login_or_token_required(Permission.VIEW_RESOURCES)
        def view():
            return "ok", 200

        with api_app.test_request_context(
            "/", headers={"Authorization": _basic("legacyusr", "wrong")}
        ):
            body, status, headers = view()
        assert status == 401
        assert body.get_json() == {"error": "Invalid credentials"}
        assert headers == {"WWW-Authenticate": 'Basic realm="SAM API"'}
