"""Every SQLAlchemy engine this webapp holds, in one enumeration.

The Admin Configuration card, ``/api/v1/health/db-pool`` and the ``/database``
browser all read it, so a new plugin database appears on each by being
registered on ``app.extensions``. One entry per engine: an fs_scans database
contributes one per collection, sharing a ``label`` (the card groups on it).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class EngineSource:
    key: str                 # 'sam' | 'system_status' | 'job_history.derecho' | 'fs_scans.campaign.univ'
    label: str               # the Configuration card's row name; fs_scans collections share one
    family: str              # 'sam' | 'system_status' | 'job_history' | 'fs_scans'
    engine: Engine
    schema: Optional[str] = None        # the Postgres schema the engine's tables live in
    database: Optional[str] = None      # fs_scans database name
    collection: Optional[str] = None    # fs_scans collection

    @property
    def title(self) -> str:
        """The browser's name for the source: the card row, plus the collection."""
        return f'{self.label} / {self.collection}' if self.collection else self.label


def engine_sources(app, db) -> List[EngineSource]:
    """sam, system_status, job_history per machine, fs_scans per database and collection (sorted)."""
    sources = [EngineSource('sam', 'sam', 'sam', db.engine)]
    status = db.engines.get('system_status') if hasattr(db, 'engines') else None
    if status is not None:
        sources.append(EngineSource('system_status', 'system_status', 'system_status', status))

    jh_state = app.extensions.get('hpc_usage_queries') or {}
    for machine, engine in (jh_state.get('engines') or {}).items():
        sources.append(EngineSource(f'job_history.{machine}', f'job_history ({machine})',
                                    'job_history', engine))

    fs_state = app.extensions.get('fs_scans') or {}
    for dbname, db_state in sorted((fs_state.get('databases') or {}).items(),
                                   key=lambda kv: kv[0] or ''):
        display = dbname or 'fs_scans'
        for collection, engine in sorted((db_state.get('engines') or {}).items()):
            sources.append(EngineSource(
                f'fs_scans.{display}.{collection}', f'fs_scans ({display})', 'fs_scans', engine,
                schema=collection if engine.dialect.name == 'postgresql' else None,
                database=dbname, collection=collection))
    return sources
