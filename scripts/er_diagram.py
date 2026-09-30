#!/usr/bin/env python3
"""Print a Graphviz ER diagram of the named SAM tables, read from the ORM metadata.

    python scripts/er_diagram.py project account allocation resources
    python scripts/er_diagram.py project:projcode,active account --rankdir TB

Each table shows its primary key, its foreign keys to the other named tables (every FK with
--all-keys), plus any columns named after a colon. Edges are those foreign keys, with a
crow's foot on the many side.
No database connection: the diagram shows what the models declare. Renders anywhere
Graphviz does, e.g. a Quarto ```{dot} cell (the SAMuel deck's refresh_data.sh).
"""

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

HEADER, VIEW_HEADER, INK, MUTED, RULE = '#0057C2', '#6B7280', '#00357A', '#6B7280', '#C9D3E0'

_SHORT_TYPES = {'integer': 'int', 'biginteger': 'bigint', 'smallinteger': 'smallint',
                'string': 'str', 'varchar': 'str', 'char': 'str', 'numeric': 'decimal',
                'boolean': 'bool', 'bit': 'bool', 'tinyint': 'bool'}


def orm_tables():
    """Every table the SAM models declare, by name."""
    import sam  # noqa: F401  (registers every model on Base.metadata)
    from sam.base import Base
    return Base.metadata.tables


def parse_spec(spec):
    """'project:projcode,active' -> ('project', ['projcode', 'active'])."""
    name, _, extra = spec.partition(':')
    return name, [c for c in extra.split(',') if c]


def _type(column):
    name = type(column.type).__name__.lower()
    return _SHORT_TYPES.get(name, name)


def _cell(text, color, port=''):
    # Graphviz rejects an empty <font></font> and drops the whole label to plain text
    attrs = f' port="{port}"' if port else ''
    body = f'<font color="{color}">{text}</font>' if text else ''
    return f'<td{attrs} align="left">{body}</td>'


def _row(column, marker):
    # two ports: edges arrive at the row's west end ("col") and leave from its east end ("col__out")
    name = html.escape(column.name)
    return (f'<tr>{_cell(name, INK, name)}{_cell(_type(column), MUTED)}'
            f'{_cell(marker, MUTED, name + "__out")}</tr>')


def table_node(table, extra, names=None):
    """A Graphviz node listing the table's keys plus `extra` columns.

    With `names`, only foreign keys into those tables are listed; None lists every FK.
    """
    missing = [c for c in extra if c not in table.c]
    if missing:
        raise SystemExit(f'{table.name}: no column(s) {", ".join(missing)}')
    pks = {c.name for c in table.primary_key.columns}
    fks = {fk.parent.name for fk in table.foreign_keys
           if names is None or fk.column.table.name in names}
    rows = []
    for column in table.columns:
        keys = [k for k, on in (('PK', column.name in pks), ('FK', column.name in fks)) if on]
        if keys or column.name in extra:
            rows.append(_row(column, ' '.join(keys)))
    is_view = table.info.get('is_view', False)
    title = html.escape(table.name) + (' (view)' if is_view else '')
    label = (f'<table border="0" cellborder="1" cellspacing="0" cellpadding="4" color="{RULE}">'
             f'<tr><td colspan="3" bgcolor="{VIEW_HEADER if is_view else HEADER}">'
             f'<font color="white"><b>{title}</b></font></td></tr>{"".join(rows)}</table>')
    return f'  "{table.name}" [label=<{label}>];'


def fk_edges(tables):
    """child:fk_column -> parent:key_column for every FK between the given tables."""
    names = {t.name for t in tables}
    edges = set()
    for table in tables:
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent in names:
                # leave the child's row east, enter the parent's west; a self-reference loops west
                tail = f'"{fk.parent.name}":w' if parent == table.name else f'"{fk.parent.name}__out":e'
                edges.add(f'  "{table.name}":{tail} -> "{parent}":"{fk.column.name}":w;')
    return sorted(edges)


def render(specs, rankdir='LR', all_keys=False):
    """The DOT source for the tables named in `specs` (see parse_spec)."""
    known = orm_tables()
    chosen = []
    for spec in specs:
        name, extra = parse_spec(spec)
        if name not in known:
            raise SystemExit(f'unknown table {name!r} (not in the SAM ORM metadata)')
        chosen.append((known[name], extra))
    tables = [t for t, _ in chosen]
    names = None if all_keys else {t.name for t in tables}
    return '\n'.join([
        'digraph ER {',
        f'  graph [rankdir={rankdir}, nodesep=0.4, ranksep=0.9, bgcolor="transparent"];',
        '  node [shape=plain, fontname="Helvetica", fontsize=11];',
        # crow's foot on the child (many) end, a bar on the parent (one) end
        f'  edge [dir=both, arrowtail=crow, arrowhead=tee, color="{MUTED}"];',
        *(table_node(t, extra, names) for t, extra in chosen),
        *fk_edges(tables),
        '}',
    ]) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('tables', nargs='+', help='table names, each optionally table:col1,col2')
    parser.add_argument('--rankdir', default='LR', choices=['LR', 'TB', 'RL', 'BT'])
    parser.add_argument('--all-keys', action='store_true',
                        help='list foreign keys to tables outside the diagram too')
    args = parser.parse_args(argv)
    sys.stdout.write(render(args.tables, rankdir=args.rankdir, all_keys=args.all_keys))


if __name__ == '__main__':
    main()
