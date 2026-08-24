"""fs-scans plugin loader and collection discovery for Flask.

The plugin (``fs_scans``) maintains its own SQLAlchemy engines — a
*different* database (CNPG/PostgreSQL) from the SAM MySQL one
Flask-SQLAlchemy binds at startup. We can't fold it under
``SQLALCHEMY_BINDS`` because ``fs_scans`` models are not part of SAM's
``db.Model`` registry.

The load / warm / tag / stash / accessor machinery is
:class:`webapp.plugins.PluginExtension`, shared with the job-history loader.
What's specific here is the two-level scope — one CNPG *database* per disk
resource, each holding several *collection* schemas — and that, unlike
``job_history``, the facade (:class:`fs_scans.FsScanQueries`) **owns its own
sessions**: it opens and closes one per query, per collection. So there is no
per-request session context manager here; callers construct
``FsScanQueries(filesystems=…)`` directly (via the service layer) and the
plugin's internal, memoized engine cache is reused — the same engines this
module warms.

The plugin is optional. If it can't be imported (developer skipped the
``[hpc]`` install extra), if the backend isn't configured, or if no
collections are reachable, we log a warning, mark the feature disabled,
and let the rest of the webapp boot — same posture as ``job_history``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from flask import Flask, current_app
from sqlalchemy import text

from sam.plugins import FS_SCANS
from webapp.plugins import PluginExtension

# app.extensions key under which we stash plugin state.
_EXT_KEY = 'fs_scans'


class FsScansExtension(PluginExtension):
    """Discovers collection schemas per CNPG database and warms their engines."""

    ext_key = _EXT_KEY
    plugin = FS_SCANS
    log_label = 'fs_scans'

    def _init_state(self, state: Dict[str, Any]) -> None:
        # database -> {'collections': [str], 'engines': {collection: Engine}}.
        # One disk resource maps to one database (see FS_SCAN_RESOURCE_DATABASES);
        # collection schemas can repeat across databases, so we key by database
        # rather than flattening to a single {collection: engine} dict.
        state['databases'] = {}

    def _should_load(self, app: Flask) -> bool:
        """``FS_SCANS_ENABLED`` master switch (TestingConfig sets it False)."""
        if not app.config.get('FS_SCANS_ENABLED', False):
            self.logger.info('fs-scans: disabled by config, plugin not loaded')
            return False
        return True

    def _warm(self, app: Flask, mod, state: Dict[str, Any]) -> None:
        """Discover collections per database and pre-warm one Engine each.

        Connection settings (backend, host, credentials) are read by the
        plugin itself from the ``FS_SCAN_*`` environment at engine-creation
        time — SAM does not pass them through.

        The set of DISTINCT databases comes from the resource->database map
        (Campaign_Store -> campaign, Destor -> desc1). An empty map falls back
        to the plugin's single default database (None -> ``FS_SCAN_PG_DB``),
        preserving the original single-database behavior.
        """
        # Read once here (main thread) and close over it — the warm pool runs in
        # worker threads where ``current_app`` isn't bound, so we can't read
        # config from inside the connect listener.
        stmt_timeout_ms = int(app.config.get('FS_SCAN_STATEMENT_TIMEOUT_MS', 0) or 0)

        resource_dbs: Dict[str, str] = app.config.get('FS_SCAN_RESOURCE_DATABASES') or {}
        db_names = sorted(set(resource_dbs.values())) or [None]

        databases: Dict[str, Any] = {}
        for database in db_names:
            # A failure here means that database is unreachable/unconfigured —
            # skip it, but keep going so one bad database (e.g. desc1 not yet
            # provisioned) can't disable the rest.
            try:
                collections = mod.list_pg_schemas(database=database)
            except Exception as exc:
                self.logger.warning(
                    'fs-scans: could not list collections for db=%s (unreachable?) — '
                    'skipping: %s', database or 'default', exc,
                )
                continue

            engines = self._warm_collections(
                mod, database, collections, stmt_timeout_ms)
            if engines:
                databases[database] = {
                    'collections': sorted(engines),
                    'engines':     engines,
                }

        state['databases'] = databases
        state['enabled'] = any(d['collections'] for d in databases.values())

    def _warm_collections(self, mod, database, collections,
                          stmt_timeout_ms: int) -> Dict[str, Any]:
        """Open + tag + health-check every collection engine in *database*.

        Warmed concurrently: each opens a fresh TLS connection to the remote
        CNPG (~1-1.5s), so serial warming of a dozen-plus collections would add
        ~20s to webapp boot. A bounded pool keeps it to a few seconds, and the
        plugin's ``get_engine`` cache is lock-guarded so this is safe.
        """
        if not collections:
            return {}

        def _warm_one(collection: str):
            try:
                engine = mod.get_engine(collection, database=database)
                # The plugin owns ``connect_args`` inside its ``get_engine``,
                # so we attach a post-creation ``connect`` listener rather than
                # threading our own connect_args through.
                if engine.url.drivername.startswith('postgresql'):
                    self.apply_connection_settings(
                        engine,
                        self.connection_tag(database or 'default', collection),
                        statement_timeout_ms=stmt_timeout_ms,
                    )
                # Health check — also forces the pool to open one connection
                # so the application_name listener fires before first query.
                with engine.connect() as conn:
                    conn.execute(text('SELECT 1'))
                self.logger.info(
                    'fs-scans engine ready: db=%s collection=%s url=%s',
                    database or 'default', collection, self.safe_url(engine),
                )
                return engine
            except Exception as exc:
                self.logger.warning(
                    'fs-scans engine init failed for db=%s collection=%s: %s',
                    database or 'default', collection, exc,
                )
                return None

        engines: Dict[str, Any] = {}
        max_workers = min(len(collections), 8)
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix='fs-scans-warm') as pool:
            for collection, engine in zip(collections,
                                          pool.map(_warm_one, collections)):
                if engine is not None:
                    engines[collection] = engine
        return engines

    # fs-scans-specific accessors

    def get_databases(self, app: Optional[Flask] = None) -> Dict[str, Any]:
        """Return ``{database: {'collections': [...], 'engines': {...}}}`` (warmed).

        The per-database warmed state. Used by the Admin -> Configuration card to
        render one health row per CNPG database, and by the resource->database
        helpers below. Empty when the plugin is disabled/unreachable.
        """
        return self._state(app).get('databases') or {}

    def get_collections(self, app: Optional[Flask] = None) -> List[str]:
        """Union of warmed/reachable collection schemas across all databases.

        A flat, deduplicated view for callers that don't care which database a
        collection lives in. Resource-scoped reachability should use
        :meth:`collections_for_resource` instead, which is database-aware.
        """
        out: set = set()
        for db in self.get_databases(app).values():
            out.update(db.get('collections') or [])
        return sorted(out)

    def get_engines(self, app: Optional[Flask] = None) -> Dict[str, Any]:
        """Return a flat ``{collection: Engine}`` merged across databases.

        Legacy/convenience view. Collection names that repeat across databases
        collide (last wins) — the Admin card uses :meth:`get_databases` instead
        so it can render each database separately.
        """
        out: Dict[str, Any] = {}
        for db in self.get_databases(app).values():
            out.update(db.get('engines') or {})
        return out

    def database_for_resource(self, resource_name: str,
                              app: Optional[Flask] = None) -> Optional[str]:
        """The CNPG database that backs a disk *resource* (or ``None``).

        Reads the ``FS_SCAN_RESOURCE_DATABASES`` map (resource NAME -> database).
        Threaded into ``FsScanQueries(database=...)`` by the service layer so each
        resource's queries hit its own database. Safe outside an app context
        (returns ``None``) so service helpers can resolve it unconditionally.
        """
        try:
            cfg = (app or current_app).config
        except RuntimeError:
            return None
        return (cfg.get('FS_SCAN_RESOURCE_DATABASES') or {}).get(resource_name)

    def collections_for_resource(self, resource_name: str,
                                 app: Optional[Flask] = None) -> List[str]:
        """Warmed collection schemas that make up a disk *resource*, unscoped.

        The single decision point for resource->collections when a query is **not**
        project-scoped (resource mode). Resolves the resource's database via
        :meth:`database_for_resource`, then returns that database's warmed
        collections. Returns ``[]`` when the plugin is off, the resource is
        unmapped, or its database warmed nothing (so callers degrade to "no
        results", same as project mode).
        """
        database = self.database_for_resource(resource_name, app)
        if database is None:
            return []
        return list(self.get_databases(app).get(database, {}).get('collections') or [])


#: Module-level singleton. The functions below are its bound methods under the
#: names every caller already imports.
extension = FsScansExtension()

init_fs_scans = extension.init_app
is_enabled = extension.is_enabled
get_module = extension.get_module
get_databases = extension.get_databases
get_collections = extension.get_collections
get_engines = extension.get_engines
database_for_resource = extension.database_for_resource
collections_for_resource = extension.collections_for_resource
