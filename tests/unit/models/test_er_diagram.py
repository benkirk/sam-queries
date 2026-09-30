"""scripts/er_diagram.py renders the ORM's keys and foreign keys as Graphviz."""
import importlib.util
import re
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / 'scripts' / 'er_diagram.py'
if not _PATH.exists():  # .dockerignore drops scripts/, and CI runs pytest inside the image
    pytest.skip('scripts/ is not in this tree', allow_module_level=True)
_spec = importlib.util.spec_from_file_location('er_diagram', _PATH)
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)


def test_keys_and_edges_among_named_tables():
    dot = er.render(['project', 'account', 'allocation'])
    assert dot.startswith('digraph ER {') and dot.rstrip().endswith('}')
    for table in ('project', 'account', 'allocation'):
        assert f'"{table}" [label=<' in dot
    assert '"account":"project_id__out":e -> "project":"project_id":w;' in dot
    assert '"allocation":"account_id__out":e -> "account":"account_id":w;' in dot
    assert '"allocation":"parent_allocation_id":w -> "allocation":"allocation_id":w;' in dot


def test_foreign_keys_to_unnamed_tables_are_left_out():
    dot = er.render(['account', 'project'])
    assert '"account":"project_id__out":e -> "project":"project_id":w;' in dot
    assert 'port="resource_id"' not in dot  # resources was not asked for
    assert 'port="resource_id"' in er.render(['account', 'project'], all_keys=True)
    assert '"resources"' not in er.render(['account'], all_keys=True)  # listed, never drawn


def test_extra_columns_are_listed():
    assert 'port="projcode"' not in er.render(['project'])
    dot = er.render(['project:projcode,active'])
    assert 'port="projcode"' in dot
    assert '"></font>' not in dot  # an empty font element makes Graphviz drop the label


def test_views_are_marked():
    view = next(t.name for t in er.orm_tables().values() if t.info.get('is_view'))
    assert '(view)' in er.render([view])


@pytest.mark.parametrize('spec, message', [
    ('no_such_table', 'unknown table'),
    ('project:no_such_column', 'no column(s) no_such_column'),
])
def test_bad_names_fail_clearly(spec, message):
    with pytest.raises(SystemExit, match=re.escape(message)):
        er.render([spec])
