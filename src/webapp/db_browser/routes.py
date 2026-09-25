"""/database pages and fragments. Every data read goes through ``BrowseSource.connect``.

A database that cannot be reached, reflected or queried raises out of the view
and lands in ``_db_unavailable``; only the table page catches its own page
query, so a timeout keeps the filter form on screen with the advice beside it.
"""
from __future__ import annotations

from flask import abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from dbbrowse import (CELL_MAX_CHARS, DETAIL_CHARS, GRID_CHARS, MAX_FILTERS, NULL_OPS,
                      OP_LABELS, FilterError, Op, OffsetTooDeep, PageRequest, RawFilter,
                      ReadOnlyNotEngaged, UnsupportedDialect, coerce, column_kind, exact_count,
                      fetch_by_key, fetch_cell, fetch_page, is_timeout, ops_for, parse_filters,
                      render_cell, top_values, url_value)
from webapp.utils.config_inspect import classify_connection_error, format_db_url_safe
from webapp.utils.htmx import PER_PAGE_CHOICES
from webapp.utils.rbac import Permission, has_permission_any_facility

from . import bp
from .params import ViewState, eq_filter_args, key_args, read_key
from .sources import BrowsedTable, browse_sources, get_source

# SAM columns rendered with the shared entity-modal links (user_link / project_link).
ENTITY_COLUMNS = {'username': 'user', 'act_username': 'user',
                  'projcode': 'project', 'act_projcode': 'project'}

# SAM tables with a dashboard page: table -> (endpoint, query arg, column).
SAM_PAGES = {
    'users': ('admin_dashboard.users_groups', 'username', 'username'),
    'project': ('admin_dashboard.projects', 'projcode', 'projcode'),
    'adhoc_group': ('admin_dashboard.users_groups', 'groupname', 'group_name'),
}


def _db_error(exc: BaseException) -> str:
    if isinstance(exc, ReadOnlyNotEngaged):
        return f'Read-only mode could not be verified on {exc}; refusing to query it.'
    if isinstance(exc, UnsupportedDialect):
        return f'The {exc} dialect is not supported.'
    if is_timeout(exc):
        ms = current_app.config['DB_BROWSER_STATEMENT_TIMEOUT_MS']
        return (f'The query ran past the {ms / 1000:g} s limit. '
                'Filter on an indexed column, or sort by one.')
    message = str(getattr(exc, 'orig', None) or exc).splitlines()[0][:500]
    hint = (classify_connection_error(message) or {}).get('hint')
    return f'{message} {hint}' if hint else message


@bp.errorhandler(SQLAlchemyError)
@bp.errorhandler(ReadOnlyNotEngaged)
@bp.errorhandler(UnsupportedDialect)
def _db_unavailable(exc):
    message = _db_error(exc)
    if request.headers.get('HX-Request'):
        return render_template('db_browser/_error.html', error=message)   # 200: htmx swaps it in
    return render_template('db_browser/error.html', error=message, crumbs=_crumbs()), 503


def _resolve(source: str, table: str) -> BrowsedTable:
    return get_source(source).browse(table)


def _entity_context(src):
    """Entity-link columns and the two permissions their macros take (SAM only)."""
    if src.family != 'sam':
        return {'entity_cols': {}, 'can_view_users': False, 'can_view_projects': False}
    return {
        'entity_cols': ENTITY_COLUMNS,
        'can_view_users': has_permission_any_facility(current_user, Permission.VIEW_USERS),
        'can_view_projects': has_permission_any_facility(current_user, Permission.VIEW_PROJECTS),
    }


def _table_context(bt: BrowsedTable, extra_crumb=None):
    """What every table-level template reads, under the names it reads them by."""
    return {
        'src': bt.src, 't': bt.table, 'entry': bt.entry, 'pk': bt.pk, 'hidden': bt.hidden,
        'shown': bt.shown, 'can_sort': bt.sortable, 'indexed': bt.indexed,
        'orm_class': bt.orm_class, 'crumbs': _crumbs(bt.src, bt.name, extra_crumb),
        **_entity_context(bt.src),
    }


def _crumbs(src=None, table=None, extra=None):
    trail = [('Admin', url_for('admin_dashboard.projects')),
             ('Database', url_for('db_browser.index'))]
    if src is not None:
        trail.append((src.title, url_for('db_browser.source', source=src.key)))
    if table is not None:
        trail.append((table, url_for('db_browser.table', source=src.key, table=table)))
    if extra:
        trail.append((extra, None))
    trail[-1] = (trail[-1][0], None)
    return trail


def _sam_link(bt: BrowsedTable, row):
    spec = SAM_PAGES.get(bt.name) if bt.src.family == 'sam' else None
    if not spec or not has_permission_any_facility(current_user, Permission.ACCESS_ADMIN_DASHBOARD):
        return None
    endpoint, arg, col = spec
    return url_for(endpoint, **{arg: row[col]}) if row.get(col) is not None else None


@bp.route('/')
def index():
    groups = {}
    for src in browse_sources().values():
        groups.setdefault(src.family, []).append(
            {'src': src, 'url': format_db_url_safe(src.engine), 'dialect': src.engine.dialect.name})
    return render_template('db_browser/index.html', groups=groups, crumbs=_crumbs())


@bp.route('/<source>/')
def source(source):
    srcs = browse_sources()
    if source not in srcs:
        # A bare /database/<table>/ means the SAM database.
        if any(e.name == source for e in srcs['sam'].catalog()):
            return redirect(url_for('db_browser.table', source='sam', table=source))
        abort(404)
    src = srcs[source]
    return render_template('db_browser/source.html', src=src, entries=src.catalog(),
                           classes=src.overlay().classes, crumbs=_crumbs(src))


@bp.route('/<source>/-/tables')
def tables_fragment(source):
    src = get_source(source)
    q = (request.args.get('q') or '').strip().lower()
    entries = [e for e in src.catalog() if q in e.name.lower()]
    return render_template('db_browser/_rail_tables.html', src=src, entries=entries,
                           current=request.args.get('current'))


@bp.route('/<source>/-/refresh', methods=['POST'])
def refresh(source):
    src = get_source(source)
    src.invalidate()
    return redirect(url_for('db_browser.source', source=src.key))


@bp.route('/<source>/<table>')
def table(source, table):
    bt = _resolve(source, table)
    state = ViewState.from_args(request.args, sortable=bt.sortable)

    cols = tuple(c for c in state.cols if c in bt.shown_names)
    if set(cols) | set(bt.pk) >= set(bt.shown_names):   # PK boxes are disabled, never submitted
        cols = ()
    keyset = len(bt.pk) == 1 and state.sort is None
    state = state.with_(cols=cols, after=state.after if keyset else None)
    if not state.matches(request.args, bt.shown_names):
        return redirect(bt.url(**state.to_args(bt.shown_names)))

    filters, errors = parse_filters(state.filters, bt.table, hidden=bt.hidden)
    visible = [c for c in bt.shown if not state.cols or c.name in state.cols or c.name in bt.pk]
    after = None
    if state.after:
        try:
            after = coerce(bt.table.c[bt.pk[0]], state.after)
        except ValueError:
            errors.append(FilterError(None, 'Invalid page cursor.'))

    page = error = None
    if not errors:
        req = PageRequest(filters=filters, sort=state.sort, desc=state.desc, page=state.page,
                          per_page=state.per_page, after=after)
        try:
            with bt.src.connect() as conn:
                page = fetch_page(conn, bt.table, visible, req, bt.pk)
        except OffsetTooDeep as exc:
            error = str(exc)
        except DBAPIError as exc:
            error = _db_error(exc)

    rows = []
    for row in page.rows if page is not None else ():
        links = bt.fk_links(row)
        rows.append({
            'url': bt.row_url(row),
            'cells': [(c.name, render_cell(row[c.name], column_kind(c), chars=GRID_CHARS),
                       links.get(c.name), row[c.name]) for c in visible],
        })

    def view_url(**changes):
        return bt.url(**state.with_(**changes).to_args(bt.shown_names))

    return render_template(
        'db_browser/table.html', state=state, page=page, rows=rows, visible=visible,
        visible_names={c.name for c in visible}, error=error,
        field_errors={e.index: e.message for e in errors if e.index is not None},
        form_errors=[e.message for e in errors if e.index is None],
        can_add_filter=len(state.filters) < MAX_FILTERS,
        ops=list(Op), op_labels=OP_LABELS, null_ops=NULL_OPS, per_page_choices=PER_PAGE_CHOICES,
        view_url=view_url,
        count_args=state.with_(sort=None, page=1, after=None, cols=()).to_args(),
        values_args=state.with_(page=1, after=None).to_args(bt.shown_names),
        tab='data', **_table_context(bt))


@bp.route('/<source>/<table>/schema')
def table_schema(source, table):
    bt = _resolve(source, table)
    dialect = bt.src.engine.dialect

    def type_str(col):
        try:
            return col.type.compile(dialect=dialect)
        except Exception:
            return repr(col.type)

    columns = [{
        'col': c, 'type': type_str(c), 'kind': column_kind(c), 'hidden': c.name in bt.hidden,
        'default': getattr(c.server_default, 'arg', None),
        'ops': [OP_LABELS[o] for o in ops_for(c)],
    } for c in bt.table.c]
    graph = bt.src.fk_graph()
    orm_cols = bt.src.overlay().columns.get(bt.name)
    db_cols = {c.name for c in bt.table.c}
    return render_template(
        'db_browser/schema.html', columns=columns,
        indexes=sorted(({'name': ix.name, 'columns': [c.name for c in ix.columns],
                         'unique': ix.unique} for ix in bt.table.indexes),
                       key=lambda i: i['name'] or ''),
        outgoing=graph.outgoing.get(bt.name, ()), incoming=graph.incoming.get(bt.name, ()),
        orm_only=sorted(orm_cols - db_cols) if orm_cols else [],
        db_only=sorted(db_cols - orm_cols) if orm_cols else [],
        tab='schema', **_table_context(bt, 'Schema'))


def _parsed_key(bt: BrowsedTable):
    raw = read_key(request.args)
    if not raw or any(bt.column(c) is None for c in raw):
        abort(404)
    try:
        return {c: coerce(bt.table.c[c], v) for c, v in raw.items()}
    except ValueError:
        abort(404)


@bp.route('/<source>/<table>/row')
def row(source, table):
    bt = _resolve(source, table)
    key = _parsed_key(bt)
    with bt.src.connect() as conn:
        rows = fetch_by_key(conn, bt.table, bt.shown, key, chars=DETAIL_CHARS)
    if len(rows) > 1:
        return redirect(bt.url(**eq_filter_args(list(read_key(request.args).items()))))
    if not rows:
        abort(404)

    values = rows[0]
    links = bt.fk_links(values)
    fields = [{
        'col': c, 'kind': column_kind(c), 'link': links.get(c.name),
        'cell': render_cell(values.get(c.name), column_kind(c), chars=DETAIL_CHARS, pretty=True),
        'raw': values.get(c.name),
    } for c in bt.shown]

    referenced_by = []
    for edge in bt.referencing_edges:
        if any(values.get(c) is None for c in edge.ref_columns):
            continue
        args = eq_filter_args(list(zip(edge.columns, (values[c] for c in edge.ref_columns))))
        referenced_by.append({'edge': edge, 'args': args,
                              'url': url_for('db_browser.table', source=bt.src.key,
                                             table=edge.table, **args)})
    return render_template(
        'db_browser/row.html', fields=fields, key=key, key_args=key_args(key),
        referenced_by=referenced_by, sam_link=_sam_link(bt, values),
        **_table_context(bt, ', '.join(f'{k}={v}' for k, v in key.items())))


@bp.route('/<source>/<table>/count')
def count_fragment(source, table):
    bt = _resolve(source, table)
    filters, errors = parse_filters(ViewState.from_args(request.args).filters, bt.table,
                                    hidden=bt.hidden)
    if errors:
        return render_template('db_browser/_count.html', n=None, error=errors[0].message)
    with bt.src.connect() as conn:
        n = exact_count(conn, bt.table, filters)
    return render_template('db_browser/_count.html', n=n, error=None)


@bp.route('/<source>/<table>/values')
def values_fragment(source, table):
    bt = _resolve(source, table)
    col_name = request.args.get('col', '')
    if col_name not in bt.sortable:
        abort(404)
    col = bt.table.c[col_name]
    state = ViewState.from_args(request.args, sortable=bt.sortable)
    raw = state.filters
    if len(raw) >= MAX_FILTERS:
        abort(404)   # a value link would need one more filter than the cap
    filters, errors = parse_filters(raw, bt.table, hidden=bt.hidden)
    ctx = {'col': col_name, 'filtered': bool(raw), 'items': [], 'error': None}
    if errors:
        return render_template('db_browser/_values.html', **{**ctx, 'error': errors[0].message})
    with bt.src.connect() as conn:
        pairs = top_values(conn, bt.table, col, filters)
    for value, n in pairs:
        rf = (RawFilter(col_name, 'isnull') if value is None
              else RawFilter(col_name, 'eq', url_value(value)))
        ctx['items'].append({
            'cell': render_cell(value, column_kind(col), chars=GRID_CHARS), 'n': n,
            'url': bt.url(**state.with_(filters=(*raw, rf), page=1, after=None).to_args()),
        })
    return render_template('db_browser/_values.html', **ctx)


@bp.route('/<source>/<table>/cell')
def cell_fragment(source, table):
    bt = _resolve(source, table)
    col = bt.column(request.args.get('col', ''))
    if col is None or column_kind(col) == 'binary':
        abort(404)
    key = _parsed_key(bt)
    with bt.src.connect() as conn:
        value = fetch_cell(conn, bt.table, col, key)
    cell = render_cell(value, column_kind(col), chars=CELL_MAX_CHARS, pretty=True)
    return render_template('db_browser/_cell_full.html', cell=cell)
