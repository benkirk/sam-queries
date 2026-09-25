"""/database pages and fragments. Every data read goes through ``sources.connect``."""
from __future__ import annotations

from flask import abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from dbbrowse import (CELL_MAX_CHARS, DETAIL_CHARS, GRID_CHARS, MAX_FILTERS, NULL_OPS,
                      OP_LABELS, FilterError, Op, OffsetTooDeep, PageRequest, RawFilter, coerce,
                      column_kind, exact_count, fetch_by_key, fetch_cell, fetch_page, is_timeout,
                      ops_for, parse_filters, render_cell, top_values)
from webapp.utils.config_inspect import classify_connection_error, format_db_url_safe
from webapp.utils.htmx import PER_PAGE_CHOICES
from webapp.utils.rbac import Permission, has_permission_any_facility

from . import bp
from .params import (args_match, canonical_args, eq_filter_args, key_args, read_key, read_view,
                     url_value)
from .sources import (CACHE, browse_sources, catalog, connect, fk_graph, get_source, get_table,
                      hidden_columns, indexed_columns, overlay, primary_key, sortable,
                      table_entry)

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
    if is_timeout(exc):
        ms = current_app.config['DB_BROWSER_STATEMENT_TIMEOUT_MS']
        return (f'The query ran past the {ms / 1000:g} s limit. '
                'Filter on an indexed column, or sort by one.')
    message = str(getattr(exc, 'orig', None) or exc).splitlines()[0][:500]
    hint = (classify_connection_error(message) or {}).get('hint')
    return f'{message} {hint}' if hint else message


def _entity_context(src):
    """Entity-link columns and the two permissions their macros take (SAM only)."""
    if src.family != 'sam':
        return {'entity_cols': {}, 'can_view_users': False, 'can_view_projects': False}
    return {
        'entity_cols': ENTITY_COLUMNS,
        'can_view_users': has_permission_any_facility(current_user, Permission.VIEW_USERS),
        'can_view_projects': has_permission_any_facility(current_user, Permission.VIEW_PROJECTS),
    }


def _crumbs(src=None, table=None, extra=None):
    trail = [('Admin', url_for('admin_dashboard.projects')),
             ('Database', url_for('db_browser.index'))]
    if src is not None:
        trail.append((src.label, url_for('db_browser.source', source=src.key)))
    if table is not None:
        trail.append((table, url_for('db_browser.table', source=src.key, table=table)))
    if extra:
        trail.append((extra, None))
    trail[-1] = (trail[-1][0], None)
    return trail


def _sam_link(src, table_name, row):
    spec = SAM_PAGES.get(table_name) if src.family == 'sam' else None
    if not spec or not has_permission_any_facility(current_user, Permission.ACCESS_ADMIN_DASHBOARD):
        return None
    endpoint, arg, col = spec
    return url_for(endpoint, **{arg: row[col]}) if row.get(col) is not None else None


def _row_url(src, table, pk, row):
    if not pk or any(row.get(c) is None for c in pk):
        return None
    return url_for('db_browser.row', source=src.key, table=table.name,
                   **key_args({c: row[c] for c in pk}))


def _fk_edges(src, table_name):
    """Outgoing FKs whose target table exists in this source."""
    names = {e.name for e in catalog(src)}
    return [e for e in fk_graph(src).outgoing.get(table_name, ()) if e.ref_table in names]


def _fk_links(src, edges, row):
    """{column: url} for each FK in ``edges`` whose values are all present in ``row``."""
    links = {}
    for edge in edges:
        if any(row.get(c) is None for c in edge.columns):
            continue
        links.setdefault(edge.columns[0], url_for(
            'db_browser.row', source=src.key, table=edge.ref_table,
            **key_args(dict(zip(edge.ref_columns, (row[c] for c in edge.columns))))))
    return links


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
        if any(e.name == source for e in catalog(srcs['sam'])):
            return redirect(url_for('db_browser.table', source='sam', table=source))
        abort(404)
    src = srcs[source]
    try:
        entries = catalog(src)
    except SQLAlchemyError as exc:
        return render_template('db_browser/source.html', src=src, entries=(), error=_db_error(exc),
                               classes={}, crumbs=_crumbs(src)), 503
    return render_template('db_browser/source.html', src=src, entries=entries, error=None,
                           classes=overlay(src).classes, crumbs=_crumbs(src))


@bp.route('/<source>/-/tables')
def tables_fragment(source):
    src = get_source(source)
    q = (request.args.get('q') or '').strip().lower()
    entries = [e for e in catalog(src) if q in e.name.lower()]
    return render_template('db_browser/_rail_tables.html', src=src, entries=entries,
                           current=request.args.get('current'))


@bp.route('/<source>/-/refresh', methods=['POST'])
def refresh(source):
    src = get_source(source)
    CACHE.invalidate((src.key,))
    return redirect(url_for('db_browser.source', source=src.key))


@bp.route('/<source>/<table>')
def table(source, table):
    src = get_source(source)
    t = get_table(src, table)
    entry = table_entry(src, table)
    hidden = hidden_columns(t)
    shown_names = [c.name for c in t.c if c.name not in hidden]
    pk = primary_key(src, t)
    can_sort = sortable(t, hidden)
    state = read_view(request.args, sortable=can_sort)

    cols = tuple(c for c in state.cols if c in shown_names)
    if set(cols) | set(pk) >= set(shown_names):   # PK boxes are disabled, so never submitted
        cols = ()
    keyset = len(pk) == 1 and state.sort is None
    state = state.with_(cols=cols, after=state.after if keyset else None)
    canonical = canonical_args(state, shown_names)
    if not args_match(request.args, canonical):
        return redirect(url_for('db_browser.table', source=src.key, table=t.name, **canonical))

    filters, errors = parse_filters(state.filters, t, hidden=hidden)
    visible = [c for c in t.c if c.name in shown_names
               and (not state.cols or c.name in state.cols or c.name in pk)]
    after = None
    if state.after:
        try:
            after = coerce(t.c[pk[0]], state.after)
        except ValueError:
            errors.append(FilterError(None, 'Invalid page cursor.'))

    page = error = None
    if not errors:
        req = PageRequest(filters=filters, sort=state.sort, desc=state.desc, page=state.page,
                          per_page=state.per_page, after=after)
        try:
            with connect(src) as conn:
                page = fetch_page(conn, t, visible, req, pk)
        except OffsetTooDeep as exc:
            error = str(exc)
        except DBAPIError as exc:
            error = _db_error(exc)

    rows = []
    if page is not None:
        edges = _fk_edges(src, t.name)
        for row in page.rows:
            links = _fk_links(src, edges, row)
            rows.append({
                'url': _row_url(src, t, pk, row),
                'cells': [(render_cell(row[c.name], column_kind(c), chars=GRID_CHARS),
                           links.get(c.name), row[c.name]) for c in visible],
            })

    def view_url(**changes):
        return url_for('db_browser.table', source=src.key, table=t.name,
                       **canonical_args(state.with_(**changes), shown_names))

    return render_template(
        'db_browser/table.html', src=src, t=t, entry=entry, state=state, page=page, rows=rows,
        visible=visible, visible_names={c.name for c in visible}, pk=pk, error=error,
        field_errors={e.index: e.message for e in errors if e.index is not None},
        form_errors=[e.message for e in errors if e.index is None],
        shown=[t.c[n] for n in shown_names], hidden=hidden, can_sort=can_sort,
        can_add_filter=len(state.filters) < MAX_FILTERS,
        indexed=indexed_columns(t), orm_class=overlay(src).classes.get(t.name),
        ops=list(Op), op_labels=OP_LABELS, null_ops=NULL_OPS, per_page_choices=PER_PAGE_CHOICES,
        view_url=view_url, count_args=canonical_args(state.with_(sort=None, page=1, after=None,
                                                                 cols=())),
        values_args=canonical_args(state.with_(page=1, after=None), shown_names),
        crumbs=_crumbs(src, t.name), tab='data', **_entity_context(src))


@bp.route('/<source>/<table>/schema')
def table_schema(source, table):
    src = get_source(source)
    t = get_table(src, table)
    hidden = hidden_columns(t)
    ov = overlay(src)
    graph = fk_graph(src)
    dialect = src.engine.dialect

    def type_str(col):
        try:
            return col.type.compile(dialect=dialect)
        except Exception:
            return repr(col.type)

    columns = [{
        'col': c, 'type': type_str(c), 'kind': column_kind(c), 'hidden': c.name in hidden,
        'default': getattr(c.server_default, 'arg', None),
        'ops': [OP_LABELS[o] for o in ops_for(c)],
    } for c in t.c]
    orm_cols = ov.columns.get(t.name)
    db_cols = {c.name for c in t.c}
    return render_template(
        'db_browser/schema.html', src=src, t=t, entry=table_entry(src, table), columns=columns,
        pk=primary_key(src, t), indexed=indexed_columns(t),
        indexes=sorted(({'name': ix.name, 'columns': [c.name for c in ix.columns],
                         'unique': ix.unique} for ix in t.indexes), key=lambda i: i['name'] or ''),
        outgoing=graph.outgoing.get(t.name, ()), incoming=graph.incoming.get(t.name, ()),
        orm_class=ov.classes.get(t.name),
        orm_only=sorted(orm_cols - db_cols) if orm_cols else [],
        db_only=sorted(db_cols - orm_cols) if orm_cols else [],
        crumbs=_crumbs(src, t.name, 'Schema'), tab='schema')


def _parsed_key(t, hidden):
    raw = read_key(request.args)
    if not raw or any(c not in t.c or c in hidden for c in raw):
        abort(404)
    try:
        return {c: coerce(t.c[c], v) for c, v in raw.items()}
    except ValueError:
        abort(404)


@bp.route('/<source>/<table>/row')
def row(source, table):
    src = get_source(source)
    t = get_table(src, table)
    hidden = hidden_columns(t)
    key = _parsed_key(t, hidden)
    shown = [c for c in t.c if c.name not in hidden]
    error, rows = None, []
    try:
        with connect(src) as conn:
            rows = fetch_by_key(conn, t, shown, key, chars=DETAIL_CHARS)
    except DBAPIError as exc:
        error = _db_error(exc)
    if len(rows) > 1:
        return redirect(url_for('db_browser.table', source=src.key, table=t.name,
                                **eq_filter_args(list(read_key(request.args).items()))))
    if not rows and error is None:
        abort(404)

    values = rows[0] if rows else {}
    links = _fk_links(src, _fk_edges(src, t.name), values) if values else {}
    fields = [{
        'col': c, 'kind': column_kind(c), 'link': links.get(c.name),
        'cell': render_cell(values.get(c.name), column_kind(c), chars=DETAIL_CHARS, pretty=True),
        'raw': values.get(c.name),
    } for c in shown] if values else []

    names = {e.name for e in catalog(src)}
    referenced_by = []
    for edge in fk_graph(src).incoming.get(t.name, ()):
        if edge.table not in names or any(values.get(c) is None for c in edge.ref_columns):
            continue
        args = eq_filter_args(list(zip(edge.columns, (values[c] for c in edge.ref_columns))))
        referenced_by.append({'edge': edge, 'args': args,
                              'url': url_for('db_browser.table', source=src.key,
                                             table=edge.table, **args)})
    return render_template(
        'db_browser/row.html', src=src, t=t, fields=fields, key=key, key_args=key_args(key),
        referenced_by=referenced_by, error=error, hidden=sorted(hidden),
        sam_link=_sam_link(src, t.name, values) if values else None,
        orm_class=overlay(src).classes.get(t.name),
        crumbs=_crumbs(src, t.name, ', '.join(f'{k}={v}' for k, v in key.items())),
        **_entity_context(src))


@bp.route('/<source>/<table>/count')
def count_fragment(source, table):
    src = get_source(source)
    t = get_table(src, table)
    filters, errors = parse_filters(read_view(request.args).filters, t, hidden=hidden_columns(t))
    if errors:
        return render_template('db_browser/_count.html', n=None, error=errors[0].message)
    try:
        with connect(src) as conn:
            n = exact_count(conn, t, filters)
    except DBAPIError as exc:
        return render_template('db_browser/_count.html', n=None, error=_db_error(exc))
    return render_template('db_browser/_count.html', n=n, error=None)


@bp.route('/<source>/<table>/values')
def values_fragment(source, table):
    src = get_source(source)
    t = get_table(src, table)
    hidden = hidden_columns(t)
    col_name = request.args.get('col', '')
    if col_name not in sortable(t, hidden):
        abort(404)
    col = t.c[col_name]
    state = read_view(request.args, sortable=sortable(t, hidden))
    raw = state.filters
    if len(raw) >= MAX_FILTERS:
        abort(404)   # a value link would need one more filter than the cap
    filters, errors = parse_filters(raw, t, hidden=hidden)
    ctx = {'col': col_name, 'filtered': bool(raw), 'items': [], 'error': None}
    if errors:
        return render_template('db_browser/_values.html', **{**ctx, 'error': errors[0].message})
    try:
        with connect(src) as conn:
            pairs = top_values(conn, t, col, filters)
    except DBAPIError as exc:
        return render_template('db_browser/_values.html', **{**ctx, 'error': _db_error(exc)})
    for value, n in pairs:
        rf = (RawFilter(col_name, 'isnull') if value is None
              else RawFilter(col_name, 'eq', url_value(value)))
        ctx['items'].append({
            'cell': render_cell(value, column_kind(col), chars=GRID_CHARS), 'n': n,
            'url': url_for('db_browser.table', source=src.key, table=t.name,
                           **canonical_args(state.with_(filters=(*raw, rf), page=1, after=None))),
        })
    return render_template('db_browser/_values.html', **ctx)


@bp.route('/<source>/<table>/cell')
def cell_fragment(source, table):
    src = get_source(source)
    t = get_table(src, table)
    hidden = hidden_columns(t)
    col_name = request.args.get('col', '')
    if col_name not in t.c or col_name in hidden or column_kind(t.c[col_name]) == 'binary':
        abort(404)
    key = _parsed_key(t, hidden)
    try:
        with connect(src) as conn:
            value = fetch_cell(conn, t, t.c[col_name], key)
    except DBAPIError as exc:
        return render_template('db_browser/_cell_full.html', cell=None, error=_db_error(exc))
    cell = render_cell(value, column_kind(t.c[col_name]), chars=CELL_MAX_CHARS, pretty=True)
    return render_template('db_browser/_cell_full.html', cell=cell, error=None)
