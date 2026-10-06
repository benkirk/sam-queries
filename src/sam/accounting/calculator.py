import logging
from collections import defaultdict
from typing import List, Dict, Iterator, Optional, Any, Tuple
from datetime import datetime
from sqlalchemy import func, text
from sqlalchemy.orm import Session
import sqlalchemy.exc as sa_exc

from sam.enums import ChargeType
from sam.sqlcompat import month_key, row_constructor
from sam.summaries.comp_summaries import CompChargeSummary
from sam.summaries.dav_summaries import DavChargeSummary
from sam.summaries.hpc_summaries import HPCChargeSummary
from sam.summaries.disk_summaries import DiskChargeSummary
from sam.summaries.archive_summaries import ArchiveChargeSummary

_logger = logging.getLogger(__name__)

# Map generic charge keys to their SQLAlchemy models
CHARGE_MODELS_BY_KEY = {
    ChargeType.HPC: HPCChargeSummary,
    ChargeType.COMP: CompChargeSummary,
    ChargeType.DAV: DavChargeSummary,
    ChargeType.DISK: DiskChargeSummary,
    ChargeType.ARCHIVE: ArchiveChargeSummary,
}

# A resource's charges live in exactly ONE table, named by its activity_type —
# the system of record (legacy routes by activity_type, one table per resource).
# Summing comp+dav for a resource_type double-counts a resource whose history was
# written to both tables (e.g. Casper: comp+dav mirror 2019-2022) and misses
# hpc_charge_summary for legacy HPC machines. Route by activity_type instead.
ACTIVITY_TYPE_TO_CHARGE_KEY = {
    'HPC':     ChargeType.HPC,
    'COMP':    ChargeType.COMP,
    'DAV':     ChargeType.DAV,
    'DISK':    ChargeType.DISK,
    'ARCHIVE': ChargeType.ARCHIVE,
}

# activity_type values that accrue per-job compute charges (num_jobs/core_hours)
COMPUTE_ACTIVITY_TYPES = frozenset({'HPC', 'COMP', 'DAV'})


def get_charge_models_for_activity(activity_type: Optional[str]) -> Dict[str, Any]:
    """Return {charge_key: Model} for a resource's activity_type (all models if None)."""
    if activity_type is None:
        return CHARGE_MODELS_BY_KEY.copy()
    key = ACTIVITY_TYPE_TO_CHARGE_KEY.get(activity_type)
    return {key: CHARGE_MODELS_BY_KEY[key]} if key is not None else {}


def get_compute_charge_model_for_activity(activity_type: Optional[str]):
    """The single summary model carrying job stats for a compute activity_type, else None."""
    if activity_type in COMPUTE_ACTIVITY_TYPES:
        return CHARGE_MODELS_BY_KEY[ACTIVITY_TYPE_TO_CHARGE_KEY[activity_type]]
    return None


def calculate_charges(session: Session,
                      account_ids: List[int],
                      start_date: datetime,
                      end_date: datetime,
                      activity_type: Optional[str]) -> Dict[str, float]:
    """
    Sum charges from the resource's authoritative table (by activity_type).
    Returns breakdown by charge key, only keys with non-zero values.
    """
    totals = {}
    models = get_charge_models_for_activity(activity_type)

    for key, model in models.items():
        val = session.query(func.coalesce(func.sum(model.charges), 0))\
            .filter(
                model.account_id.in_(account_ids),
                model.activity_date >= start_date,
                model.activity_date <= end_date
            ).scalar()

        # replicate projects.py behavior: only add if truthy (non-zero)
        if val:
            totals[key] = float(val)

    return totals


def calculate_total_charges(session: Session,
                            account_ids: List[int],
                            start_date: datetime,
                            end_date: datetime,
                            activity_type: Optional[str]) -> float:
    """
    Sum charges from the resource's authoritative table (by activity_type).
    Returns a single float value.
    """
    charges = calculate_charges(session, account_ids, start_date, end_date, activity_type)
    return sum(charges.values())


# Anchored sums: charges per "anchor" (an account, or a project subtree on one
# resource) inside that anchor's own dates, one statement per charge table.

#: None until probed; False where the database takes no VALUES row-constructor table.
_values_supported: Optional[bool] = None


def values_cte_supported(session) -> bool:
    """Whether the database takes a VALUES row-constructor table (probed once per process)."""
    global _values_supported
    if _values_supported is None:
        try:
            session.execute(text(f"SELECT * FROM (VALUES {row_constructor(session)}(1)) AS t(n)"))
            _values_supported = True
        except (sa_exc.OperationalError, sa_exc.ProgrammingError):
            try:
                session.rollback()
            except Exception:
                pass
            _values_supported = False
            _logger.warning(
                "Batch charge queries: VALUES row constructors are not supported by this database "
                "version (requires MariaDB >= 10.3.3 or MySQL >= 8.0.19). Using a UNION ALL anchors "
                "table instead: results are correct, statements are larger."
            )
    return _values_supported


_DATE_COLS = ('start_date', 'end_date')
_ACCOUNT_COLS = ('account_id',)
_SUBTREE_COLS = ('tree_root', 'tree_left', 'tree_right', 'resource_id')

_ACCOUNT_JOIN = """
    JOIN {anchors} ON t.account_id = a.account_id
                  AND t.{date} BETWEEN {lo} AND {hi}"""

_SUBTREE_JOIN = """
    JOIN account acc ON t.account_id = acc.account_id
    JOIN project p   ON acc.project_id = p.project_id
    JOIN {anchors} ON p.tree_root = a.tree_root
                  AND p.tree_left >= a.tree_left AND p.tree_right <= a.tree_right
                  AND acc.resource_id = a.resource_id
                  AND t.{date} BETWEEN {lo} AND {hi}"""


def _anchors_table(session, cols, n) -> str:
    """The anchors as a derived table ``a``: VALUES rows, or a UNION ALL where VALUES is unsupported."""
    names = ('anchor_key',) + cols
    if values_cte_supported(session):
        row = row_constructor(session)
        rows = ', '.join(f"{row}(:ak{i}, {', '.join(f':{c}{i}' for c in cols)})" for i in range(n))
        return f"(VALUES {rows}) AS a ({', '.join(names)})"
    selects = ' UNION ALL '.join(
        f"SELECT :ak{i} AS anchor_key, {', '.join(f':{c}{i} AS {c}' for c in cols)}" for i in range(n))
    return f'({selects}) AS a'


def anchor_sums(session, infos: List[Dict], *, subtree: bool, table: str, value: str,
                date_col: str, by_month: bool = False) -> Iterator[Tuple[Any, Optional[int], float]]:
    """Yield ``(info['key'], yyyymm or None, sum)`` of ``table.value`` per anchor, non-zero only.

    An info carries ``key``, ``start_date``, ``end_date`` and ``account_id``; with ``subtree``,
    ``tree_root`` / ``tree_left`` / ``tree_right`` / ``resource_id`` instead of ``account_id``.
    """
    if not infos:
        return
    cols, join = (_SUBTREE_COLS, _SUBTREE_JOIN) if subtree else (_ACCOUNT_COLS, _ACCOUNT_JOIN)
    # Anchors sharing one date range take it as constants, so the planner can range-scan the
    # date index instead of reading each account's history (0.5 ms vs 104 ms, MySQL). Mixed
    # ranges ride in the anchors table.
    spans = {(info['start_date'], info['end_date']) for info in infos}
    params: Dict[str, Any] = {}
    if len(spans) == 1:
        (params['lo'], params['hi']), = spans
        lo, hi = ':lo', ':hi'
    else:
        cols, lo, hi = cols + _DATE_COLS, 'a.start_date', 'a.end_date'
    for i, info in enumerate(infos):
        params[f'ak{i}'] = i
        params.update({f'{c}{i}': info[c] for c in cols})
    month = f", {month_key(f't.{date_col}')}" if by_month else ''
    anchors = _anchors_table(session, cols, len(infos))
    sql = text(f"""
        SELECT a.anchor_key{month}, SUM(COALESCE(t.{value}, 0))
        FROM {table} t {join.format(anchors=anchors, date=date_col, lo=lo, hi=hi)}
        GROUP BY a.anchor_key{month}""")
    for row in session.execute(sql, params):
        if row[-1]:
            yield infos[row[0]]['key'], int(row[1]) if by_month else None, float(row[-1])


def batch_charges(session, infos: List[Dict], *, subtree: bool,
                  include_adjustments: bool = True) -> Dict[Any, Dict]:
    """``{key: {'charges_by_type': {charge_key: float}, 'adjustment': float}}`` for every info.

    Infos are `anchor_sums` infos plus ``activity_type``, which names the one charge table.
    """
    result = {info['key']: {'charges_by_type': {}, 'adjustment': 0.0} for info in infos}
    # Subtree anchors go one date range per statement, so `anchor_sums` takes it as constants:
    # measured on MySQL, mixing ranges in one subtree statement cost 2.5x (fstree, 2026-10-03).
    groups: Dict[Any, List[Dict]] = defaultdict(list)
    for info in infos:
        dates = (info['start_date'], info['end_date']) if subtree else None
        groups[(info['activity_type'], dates)].append(info)
    for (activity_type, _dates), group in groups.items():
        for charge_key, model in get_charge_models_for_activity(activity_type).items():
            for key, _, amount in anchor_sums(session, group, subtree=subtree, table=model.__tablename__,
                                              value='charges', date_col='activity_date'):
                by_type = result[key]['charges_by_type']
                by_type[charge_key] = by_type.get(charge_key, 0.0) + amount
    if include_adjustments:
        for key, _, amount in anchor_sums(session, infos, subtree=subtree, table='charge_adjustment',
                                          value='amount', date_col='adjustment_date'):
            result[key]['adjustment'] += amount
    return result


def usage_anchor(key, project, account, activity_type, start_date, end_date,
                 resource_type=None) -> Tuple[Dict[str, Any], bool]:
    """A `batch_charges` anchor for one (project, account), and whether it takes the subtree path."""
    info = {
        'key': key,
        'resource_type': resource_type,
        'activity_type': activity_type,
        'resource_id': account.resource_id,
        'account_id': account.account_id,
        'tree_root': project.tree_root,
        'tree_left': project.tree_left,
        'tree_right': project.tree_right,
        'start_date': start_date,
        'end_date': end_date,
    }
    return info, project.sums_as_subtree()


def anchored_charges(session, anchors: List[Tuple[Dict, bool]], *,
                     include_adjustments: bool = True) -> Dict[Any, Dict]:
    """`batch_charges` over `usage_anchor` pairs, the subtree and account partitions merged."""
    out: Dict[Any, Dict] = {}
    for subtree in (True, False):
        infos = [info for info, takes_subtree in anchors if takes_subtree is subtree]
        if infos:
            out.update(batch_charges(session, infos, subtree=subtree,
                                     include_adjustments=include_adjustments))
    return out
