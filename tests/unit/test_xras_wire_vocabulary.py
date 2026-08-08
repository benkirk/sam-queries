"""Every wire field the handlers read must be a field the schema declares.

The bug this exists for
-----------------------
``resolve_resource`` read ``resources[].key``. **No XRAS payload has ever carried that
field.** All six resource-bearing corpus fixtures send ``resourceRepositoryKey``, the
schema declares it under that name, and ``XrasActionSchema`` drops unknown keys — so
through the real pipeline the key was always ``None`` and every resource reported

    No resource found in SAM corresponding to key

with an empty key, on Supplement, Adjustment, New and Update. Roughly 36% of production
traffic, failing on day one of an abrupt cutover with an error naming no key because
there was none to name.

Why nothing caught it for a whole sprint
----------------------------------------
Every test built its own ``resources[]`` entries as ``{'key': ...}`` — five handler
modules, the error-coverage matrix, and the oracle's ``_retarget``, whose docstring
claims *"Shape untouched"* while replacing the one field that mattered. The corpus was
loaded through the schema and then had its resources overwritten with the invented
shape.

So the fix is not only the field name. It is this file: a check on the **vocabulary
itself**, which does not care what any test constructs.

Related: ``tests/unit/test_xras_error_coverage.py`` does the same thing for error
strings — declare the set, then prove the code and the declaration agree.
"""

import ast
import inspect
import json
import pathlib

import marshmallow
import pytest

from sam.schemas.forms import xras as xras_schemas

pytestmark = pytest.mark.unit

XRAS_SRC = pathlib.Path(__file__).parent.parent.parent / 'src' / 'sam' / 'xras'
FIXTURE_DIR = pathlib.Path(__file__).parent.parent / 'fixtures' / 'xras' / 'actions'

#: Field names read for reasons other than the XRAS wire contract, with the reason.
#: Anything not declared by a schema and not listed here is a bug.
_NOT_WIRE_FIELDS = {
    # `canonical_action_type` is applied to the *value*, and `select_service` reads
    # these two off the same loaded action — both are declared, so nothing lands here
    # today. The set exists so a future legitimate exception carries its reason
    # instead of quietly widening the check.
}


def declared_wire_fields():
    """Every field name any XRAS wire schema declares, across all nesting levels.

    A union rather than a per-level map. That is deliberately loose — it would not
    catch reading a *resource* field off the *action* — but it catches the class of bug
    that actually happened: a name that exists nowhere on the wire.
    """
    names = set()
    for obj in vars(xras_schemas).values():
        if (inspect.isclass(obj) and issubclass(obj, marshmallow.Schema)
                and obj is not marshmallow.Schema):
            names |= set(obj().fields)
    return names


def fields_read_by_handlers():
    """Every string literal passed to ``get_field(...)`` or ``self.get(...)``.

    An AST walk rather than a regex, so a name split across lines or built by
    concatenation is not silently missed — a non-literal argument raises below.
    """
    found = {}
    for path in sorted(XRAS_SRC.rglob('*.py')):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_get_field = isinstance(func, ast.Name) and func.id == 'get_field'
            is_self_get = (isinstance(func, ast.Attribute) and func.attr == 'get'
                           and isinstance(func.value, ast.Name)
                           and func.value.id == 'self')
            if not (is_get_field or is_self_get):
                continue
            arg = node.args[1] if is_get_field and len(node.args) > 1 else (
                node.args[0] if is_self_get and node.args else None)
            if arg is None:
                continue
            site = f'{path.name}:{node.lineno}'
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(arg.value, []).append(site)
            else:
                # One legitimate case: `_external_strategy` loops a literal tuple of
                # three field names, because it is the only strategy that tests three
                # fields. Resolve the loop's iterable so those names are still checked
                # rather than silently exempted.
                for name in _resolve_dynamic(tree, node, arg):
                    found.setdefault(name, []).append(site)
    return found


def _resolve_dynamic(tree, call, arg):
    """Field names behind a non-literal read, or ``[]`` for a pass-through accessor.

    Two shapes are legitimate and everything else raises — a wire field read by a name
    this check cannot see is the exact hole the check exists to close, so an unhandled
    one must fail loudly rather than be skipped.

    1. **A loop over a literal tuple.** ``_external_strategy`` is the only strategy
       testing three fields, and it iterates them. Those names are still checked.
    2. **A pass-through accessor.** ``ActionHandler.get(self, key)`` forwards its own
       parameter; the real reads are at its call sites, which are literals.
    """
    if not isinstance(arg, ast.Name):
        raise AssertionError(
            f'line {call.lineno} reads a wire field by an expression this check '
            f'cannot resolve. Use a literal.')

    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == arg.id
                and isinstance(node.iter, (ast.Tuple, ast.List))
                and all(isinstance(el, ast.Constant) and isinstance(el.value, str)
                        for el in node.iter.elts)):
            return [el.value for el in node.iter.elts]

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and any(
                a.arg == arg.id for a in node.args.args) and any(
                inner is call for inner in ast.walk(node)):
            return []

    raise AssertionError(
        f'line {call.lineno} reads a wire field by a name this check cannot resolve. '
        f'Use a literal, or a literal tuple in the enclosing for-loop.')


class TestTheHandlersReadFieldsThatExist:

    def test_every_field_read_is_declared_by_a_schema(self):
        declared = declared_wire_fields()
        read = fields_read_by_handlers()
        undeclared = {name: sites for name, sites in read.items()
                      if name not in declared and name not in _NOT_WIRE_FIELDS}
        assert not undeclared, (
            'these wire fields are read but declared by no XRAS schema, so they load '
            f'as None on every real payload: {undeclared}')

    def test_the_check_sees_a_meaningful_number_of_fields(self):
        """A guard on the guard. If the AST walk stopped matching — a rename of
        ``get_field``, a move to a different accessor — this file would pass
        vacuously, which is the failure mode a vocabulary check is most prone to."""
        read = fields_read_by_handlers()
        assert len(read) >= 15, f'only found {len(read)} field reads: {sorted(read)}'
        assert 'requestNumber' in read
        assert 'resources' in read

    def test_the_resource_key_field_is_the_one_the_wire_sends(self):
        """The specific regression, named, so its fix cannot be quietly reverted."""
        read = fields_read_by_handlers()
        assert 'resourceRepositoryKey' in read
        assert 'key' not in read, (
            "'key' is not an XRAS wire field — the payload sends "
            f"'resourceRepositoryKey'. Read at: {read.get('key')}")


class TestTheCorpusAgrees:
    """The measurement the fix rests on, pinned so it cannot rot."""

    RESOURCE_BEARING = [
        'supplement_ucub0182_ok.json',
        'supplement_ubrn0027_ok.json',
        'adjustment_uwis0064_manual.json',
        'new_ncar4253_ok.json',
        'new_uwis0071_existing_ok.json',
        'new_ncar4232_failed.json',
    ]

    @pytest.mark.parametrize('name', RESOURCE_BEARING)
    def test_resources_carry_resource_repository_key_and_not_key(self, name):
        payload = json.loads((FIXTURE_DIR / name).read_text())
        assert payload['resources'], f'{name} was chosen for its resources'
        for entry in payload['resources']:
            assert 'resourceRepositoryKey' in entry
            assert 'key' not in entry


class TestASchemaLoadedPayloadResolvesItsResources:
    """The behavioural half — the vocabulary check proves the name, this proves the
    plumbing.

    Loads a **real** payload through the **real** schema, rather than hand-building
    the dict. That single difference is what every existing handler test skipped.
    """

    def test_a_real_supplement_resolves_every_resource(self, session):
        from factories import make_resource
        from sam.integration.xras import XrasResourceRepositoryKeyResource
        from sam.schemas.forms import XrasActionSchema
        from sam.xras.errors import ActionErrors
        from sam.xras.handlers._fields import resolve_resource

        raw = json.loads(
            (FIXTURE_DIR / 'supplement_ucub0182_ok.json').read_text())
        loaded = XrasActionSchema().load(raw)

        for entry in loaded['resources']:
            key = entry['resourceRepositoryKey']
            row = (session.query(XrasResourceRepositoryKeyResource)
                   .filter_by(resource_repository_key=key).first())
            if row is None:
                session.add(XrasResourceRepositoryKeyResource(
                    resource_repository_key=key,
                    resource_id=make_resource(session).resource_id))
        session.flush()

        errs = ActionErrors()
        resolved = [resolve_resource(session, entry, errs)
                    for entry in loaded['resources']]

        assert list(errs) == [], f'a real payload reported: {list(errs)}'
        assert all(r is not None for r in resolved)
