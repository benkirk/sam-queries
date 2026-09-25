"""What an ORM registry knows about a database that reflection may not.

Views and PK-less tables have no reflected primary key, and a few relationships
are declared only in the ORM. Built from a registry passed in, so this package
never imports ``sam`` or ``system_status``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import Table

from .catalog import FkEdge


@dataclass
class OrmOverlay:
    classes: Dict[str, str] = field(default_factory=dict)            # table -> class name
    primary_keys: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    columns: Dict[str, Set[str]] = field(default_factory=dict)
    fk_edges: List[FkEdge] = field(default_factory=list)

    @classmethod
    def from_registry(cls, registry, *, bind_key: Optional[str] = None) -> 'OrmOverlay':
        """Mappers whose ``__bind_key__`` equals ``bind_key`` (None = the default bind)."""
        overlay = cls()
        for mapper in registry.mappers:
            if getattr(mapper.class_, '__bind_key__', None) != bind_key:
                continue
            table = mapper.persist_selectable
            if not isinstance(table, Table) or table.name in overlay.classes:
                continue
            overlay.classes[table.name] = mapper.class_.__name__
            overlay.primary_keys[table.name] = tuple(c.name for c in mapper.primary_key)
            overlay.columns[table.name] = {c.name for c in table.columns}
            for fk in table.foreign_key_constraints:
                try:
                    ref = fk.referred_table.name
                except Exception:   # unresolvable target; the ORM itself would fail on it
                    continue
                overlay.fk_edges.append(FkEdge(
                    table.name, tuple(c.name for c in fk.columns), ref,
                    tuple(e.column.name for e in fk.elements), origin='orm'))
        return overlay
