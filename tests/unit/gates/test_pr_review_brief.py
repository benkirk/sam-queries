"""scripts/pr_review_brief.py: changed paths map to the rules and skills a review must read."""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_PATH = REPO / 'scripts' / 'pr_review_brief.py'
if not _PATH.exists():
    pytest.skip('scripts/ is not in this tree', allow_module_level=True)
_spec = importlib.util.spec_from_file_location('pr_review_brief', _PATH)
brief = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brief)


def _rule(root, name, *globs):
    lines = ['---', 'paths:', *[f'  - "{g}"' for g in globs], '---', '', f'# {name}', '']
    (root / name).write_text('\n'.join(lines))


class TestTheGlobs:
    @pytest.mark.parametrize('pattern, path, hit', [
        ('src/sam/**/*.py', 'src/sam/core/users.py', True),
        ('src/sam/**/*.py', 'src/sam/fmt.py', True),
        ('src/sam/**/*.py', 'src/sam/notify/templates/x.html', False),
        ('src/sam/fmt.py', 'src/sam/fmt.py', True),
        ('src/sam/fmt.py', 'src/sam/fmt.pyc', False),
        ('src/sam/queries/*_access.py', 'src/sam/queries/user_access.py', True),
        ('src/sam/queries/*_access.py', 'src/sam/queries/deep/user_access.py', False),
        ('tests/**/*', 'tests/unit/gates/test_docs.py', True),
        ('tests/unit/webapp/test_account_requests_*.py',
         'tests/unit/webapp/test_account_requests_manage.py', True),
    ])
    def test_a_glob_matches_what_the_rule_loader_would(self, pattern, path, hit):
        assert bool(brief.glob_to_regex(pattern).match(path)) is hit


class TestRulesForPaths:
    def test_each_rule_lists_the_paths_that_load_it(self, tmp_path):
        _rule(tmp_path, 'orm.md', 'src/sam/**/*.py', 'tests/factories/**/*')
        _rule(tmp_path, 'testing.md', 'tests/**/*')
        found = brief.rules_for_paths(['src/sam/core/x.py', 'docs/plans/X.md'], tmp_path)
        assert found == {'orm.md': ['src/sam/core/x.py']}

    def test_a_rule_without_paths_loads_in_every_session(self, tmp_path):
        (tmp_path / 'always.md').write_text('# Always\n')
        _rule(tmp_path, 'orm.md', 'src/sam/**/*.py')
        found = brief.rules_for_paths(['README.md'], tmp_path)
        assert found == {'always.md': [brief.EVERY_SESSION]}

    def test_the_real_tree_maps_the_reviewed_prs(self):
        rules = brief.rules_for_paths(['src/webapp/api/xras/actions.py'], REPO / '.claude/rules')
        assert 'webapp-api.md' in rules, 'the #803 route'
        rules = brief.rules_for_paths(['src/sam/manage/account_requests.py'], REPO / '.claude/rules')
        assert {'account-requests.md', 'orm.md'} <= set(rules), 'the #800 manage layer'
        assert all(v != [brief.EVERY_SESSION] for v in rules.values()), \
            'every rule is path-scoped (test_rules_are_path_scoped)'


class TestSkillsAndMemory:
    def test_dashboard_paths_add_the_wiring_checklist(self):
        assert brief.skills_for_paths(['src/webapp/templates/x.html']) == [
            brief.LENS_SKILL, brief.DASHBOARD_SKILL]
        assert brief.skills_for_paths(['src/sam/manage/x.py']) == [brief.LENS_SKILL]

    def test_memory_lines_name_the_pr_and_nothing_else(self, tmp_path):
        index = tmp_path / 'MEMORY.md'
        index.write_text('- [A](a.md) PR #803 reviewed\n- [B](b.md) #8030 open\n- [C](c.md)\n')
        assert brief.memory_lines(803, index) == ['- [A](a.md) PR #803 reviewed']
        assert brief.memory_lines(803, tmp_path / 'missing.md') == []
