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


#: Packages on the ORM side of the layer rule: `webapp` imports them, never the reverse.
SAM_SIDE = ['sam', 'system_status', 'scheduling', 'querykit', 'dbbrowse']


def _module_level_webapp_imports(path: Path):
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] == 'webapp':
            yield node.lineno
        elif isinstance(node, ast.Import) and any(a.name.split('.')[0] == 'webapp' for a in node.names):
            yield node.lineno


@pytest.mark.parametrize('package', SAM_SIDE)
def test_the_sam_side_never_imports_webapp_at_module_level(package):
    offenders = [f'{p.relative_to(REPO_ROOT)}:{line}'
                 for p in sorted((SRC / package).rglob('*.py'))
                 for line in _module_level_webapp_imports(p)]
    assert not offenders, offenders


def test_importing_the_schemas_needs_no_flask():
    """`sqla_session` is a proxy resolved on first load; a dump never touches it."""
    import subprocess, sys
    body = ('import sys; sys.modules["webapp"] = None\n'
            'import sam.schemas, system_status.schemas\n'
            'print("flask" in sys.modules)')
    env = {'PYTHONPATH': str(SRC), 'PATH': ''}
    result = subprocess.run([sys.executable, '-c', body], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'False'
