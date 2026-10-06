"""Import-graph rules that keep the ORM side of the tree free of the webapp.

`sam.queries` re-exports nothing, so importing one query module drags in only
that module's graph. The layer rule (`sam` never imports `webapp`) is checked
on the AST: a lazy import inside a function is allowed, a module-level one is
not. Both are rules a convention cannot hold, because a violation never fails
anything on its own.
"""

import ast
from pathlib import Path
from _paths import REPO_ROOT

import pytest

SRC = REPO_ROOT / 'src'

QUERIES_INIT = SRC / 'sam' / 'queries' / '__init__.py'


def test_the_queries_package_re_exports_nothing():
    tree = ast.parse(QUERIES_INIT.read_text())
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not imports, (
        f'sam/queries/__init__.py imports {len(imports)} thing(s); '
        'import the submodule at the call site instead')
