"""Every SQLAlchemy engine this webapp holds, in one enumeration.

The Admin Configuration card and the ``/database`` browser both read it, so a
new plugin database appears on both by being registered on ``app.extensions``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class EngineSource:
    key: str                 # 'sam' | 'system_status' | 'job_history.derecho' | 'fs_scans.campaign'
    label: str               # the Configuration card's row name
    family: str              # 'sam' | 'system_status' | 'job_history' | 'fs_scans'
    engines: Dict[Optional[str], Engine] = field(default_factory=dict)  # schema -> engine
    database: Optional[str] = None   # fs_scans database name

    @property
    def engine(self) -> Engine:
        """The representative engine: every fs_scans collection shares host and database."""
        return next(iter(self.engines.values()))


def engine_sources(app, db) -> List[EngineSource]:
    """sam, system_status, job_history per machine, fs_scans per database (sorted)."""
    sources = [EngineSource('sam', 'sam', 'sam', {None: db.engine})]
    status = db.engines.get('system_status') if hasattr(db, 'engines') else None
    if status is not None:
        sources.append(EngineSource('system_status', 'system_status', 'system_status',
                                    {None: status}))

    jh_state = app.extensions.get('hpc_usage_queries') or {}
    for machine, engine in (jh_state.get('engines') or {}).items():
        sources.append(EngineSource(f'job_history.{machine}', f'job_history ({machine})',
                                    'job_history', {None: engine}))

    fs_state = app.extensions.get('fs_scans') or {}
    for dbname, db_state in sorted((fs_state.get('databases') or {}).items(),
                                   key=lambda kv: kv[0] or ''):
        engines = dict(sorted((db_state.get('engines') or {}).items()))
        if not engines:
            continue
        display = dbname or 'fs_scans'
        sources.append(EngineSource(f'fs_scans.{display}', f'fs_scans ({display})',
                                    'fs_scans', engines, database=dbname))
    return sources
