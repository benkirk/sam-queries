"""Pure-function gates for containers/sam-sql-dev: no MySQL, no docker, no prod.

The clone scripts are standalone (not a package), so they are imported by path.
"""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CLONE_DIR = Path(__file__).resolve().parents[2] / 'containers' / 'sam-sql-dev'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, CLONE_DIR / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def clone():
    return _load('bootstrap_clone')


@pytest.fixture(scope='module')
def leak():
    return _load('check_username_leak')


@pytest.fixture
def cfg():
    return {
        'remote': {'database': 'sam', '_defaults_file': '/tmp/x.cnf', 'password': 'S3CRET'},
        'local': {'docker_container': 'samuel-mysql', 'user': 'root', 'password': 'root',
                  'database': 'sam'},
        'settings': {'size_threshold_mb': 250, 'row_limit': 10, 'prefer_column': 'created_at',
                     'max_refill_multiplier': 2, 'table_strategies': [
                         {'pattern': '*_activity', 'mode': 'empty'}]},
    }


class TestNoSecretOnArgv:
    def test_mysqldump_uses_a_defaults_file(self, clone, cfg):
        argv = clone.mysqldump_argv(cfg, '--no-data', tables=['users'])
        assert argv[0] == 'mysqldump'
        assert argv[1] == '--defaults-extra-file=/tmp/x.cnf'
        assert 'S3CRET' not in ' '.join(argv)
        assert argv[-2:] == ['sam', 'users']

    def test_client_flags_follow_the_installed_mysqldump(self, clone, cfg):
        assert clone.client_flags('  --masking-policies  Dump masking policies') == \
            ('--skip-masking-policies',)
        assert clone.client_flags('  --no-data') == ()
        cfg['remote']['_client_flags'] = ('--skip-masking-policies',)
        argv = clone.mysqldump_argv(cfg, '--no-data', tables=['users'])
        assert '--skip-masking-policies' in argv and argv.index('--skip-masking-policies') < argv.index('--no-data')

    def test_docker_mysql_forwards_the_env_var_by_name_only(self, clone, cfg):
        argv = clone.docker_mysql_argv(cfg)
        assert argv[:5] == ['docker', 'exec', '-i', '-e', 'MYSQL_PWD']
        assert 'root' not in argv[5:6]  # container name, not the password
        assert not any('MYSQL_PWD=' in a for a in argv)


class TestFkRestrictions:
    FK_MAP = {'account': [
        {'child_col': 'project_id', 'parent_table': 'project', 'parent_col': 'project_id'},
        {'child_col': 'resource_id', 'parent_table': 'resources', 'parent_col': 'resource_id'},
    ]}

    def test_a_parent_copied_in_full_restricts_nothing(self, clone):
        assert clone.fk_restrictions('account', self.FK_MAP, {}) == []

    def test_a_sampled_parent_restricts_by_its_ids(self, clone):
        clauses = clone.fk_restrictions('account', self.FK_MAP, {'project': [3, 1, 2]})
        assert clauses == ['`project_id` IN (3,1,2)']

    def test_an_emptied_parent_restricts_nothing(self, clone):
        assert clone.fk_restrictions('account', self.FK_MAP, {'project': []}) == []

    def test_a_composite_pk_parent_is_skipped(self, clone, capsys):
        clauses = clone.fk_restrictions('account', self.FK_MAP, {'project': [(1, 'a')]})
        assert clauses == []
        assert 'composite' in capsys.readouterr().out

    def test_string_ids_are_quoted(self, clone):
        fk = {'x': [{'child_col': 'code', 'parent_table': 'p', 'parent_col': 'code'}]}
        assert clone.fk_restrictions('x', fk, {'p': ["it's"]}) == ["`code` IN ('it''s')"]


class TestTopologicalSort:
    def _order(self, clone, edges, tables):
        rows = [{'parent_table': p, 'child_table': c} for p, c in edges]
        parents, children_of = clone.build_dependency_graph(rows)
        return clone.topological_sort(parents, children_of, tables)

    def test_parents_come_before_children(self, clone):
        order = self._order(clone, [('project', 'account'), ('account', 'allocation')],
                            ['allocation', 'account', 'project'])
        assert order == ['project', 'account', 'allocation']

    def test_a_cycle_is_broken_and_its_descendants_stay_ordered(self, clone, capsys):
        # resources <-> resource_shell, with everything else hanging off resources
        edges = [('resources', 'resource_shell'), ('resource_shell', 'resources'),
                 ('resources', 'account'), ('account', 'allocation'), ('project', 'account')]
        order = self._order(clone, edges, ['allocation', 'account', 'resource_shell',
                                           'resources', 'project'])
        assert len(order) == 5 and len(set(order)) == 5
        assert order.index('account') < order.index('allocation')
        assert order.index('project') < order.index('account')
        assert {'resources', 'resource_shell'} < set(order[:3])
        assert 'FK cycles broken at' in capsys.readouterr().out


class TestSchemaStrip:
    DDL = (
        "CREATE TABLE `account` (\n  `account_id` int NOT NULL,\n  `project_id` int NOT NULL,\n"
        "  PRIMARY KEY (`account_id`),\n  KEY `ix` (`project_id`),\n"
        "  CONSTRAINT `fk_account_project` FOREIGN KEY (`project_id`) REFERENCES `project` "
        "(`project_id`) ON DELETE CASCADE ON UPDATE RESTRICT\n) ENGINE=InnoDB;\n")

    def test_fk_clauses_go_and_everything_else_stays(self, clone):
        out = clone.strip_foreign_keys(self.DDL)
        assert 'FOREIGN KEY' not in out and 'ON DELETE' not in out
        assert 'PRIMARY KEY (`account_id`)' in out and 'KEY `ix`' in out
        assert out.rstrip().endswith(') ENGINE=InnoDB;')


class TestAlterAddFk:
    def test_rules_other_than_restrict_are_emitted(self, clone):
        fk = {'child_table': 'account', 'constraint': 'fk_account_project',
              'child_cols': ['project_id'], 'parent_table': 'project',
              'parent_cols': ['project_id'], 'on_delete': 'CASCADE', 'on_update': 'RESTRICT'}
        stmt = clone.build_alter_add_fk(fk)
        assert stmt == ('ALTER TABLE `account` ADD CONSTRAINT `fk_account_project` '
                        'FOREIGN KEY (`project_id`) REFERENCES `project` (`project_id`) '
                        'ON DELETE CASCADE')


class TestSampleAndDump:
    """Only the sampled branch records ids; empty tables never reach the remote."""

    @pytest.fixture(autouse=True)
    def _no_remote(self, clone, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / clone.DUMP_DIR).mkdir()
        monkeypatch.setattr(clone.subprocess, 'run',
                            lambda *a, **k: pytest.fail('subprocess must not run'))
        self.calls = []
        monkeypatch.setattr(clone, 'dump_table_full',
                            lambda cfg, t: self.calls.append(('full', t)) or f'dump/{t}.sql')
        monkeypatch.setattr(clone, 'dump_table_where',
                            lambda cfg, t, w: self.calls.append(('where', t, w)) or f'dump/{t}.sql')

    def test_empty_strategy_writes_a_stub_without_a_dump(self, clone, cfg, tmp_path):
        sampled = {}
        out = clone.sample_and_dump_table(cfg, None, 'hpc_activity', 999, {}, {}, sampled)
        assert self.calls == []
        assert (tmp_path / out).read_text().startswith('-- hpc_activity: emptied')
        assert 'hpc_activity' not in sampled

    def test_small_table_is_copied_in_full_and_records_no_ids(self, clone, cfg):
        sampled = {}
        clone.sample_and_dump_table(cfg, None, 'users', 8, {'users': ['user_id']}, {}, sampled)
        assert self.calls == [('full', 'users')]
        assert sampled == {}

    def test_large_table_is_sampled_and_records_its_ids(self, clone, cfg, monkeypatch):
        monkeypatch.setattr(clone, 'detect_order_column', lambda conn, t, prefer: 'created_at')
        monkeypatch.setattr(clone, 'fetch_pk_values', lambda *a, **k: [7, 5])
        sampled = {}
        clone.sample_and_dump_table(cfg, None, 'big', 900, {'big': ['id']}, {}, sampled)
        assert self.calls == [('where', 'big', '`id` IN (7,5)')]
        assert sampled == {'big': [7, 5]}


class TestLeakCheck:
    def test_preserved_names_come_from_config(self, leak):
        cfg = {'anonymization': {'preserve_usernames': ['csgteam', 'benkirk', 'andersnb']}}
        assert leak.preserved_usernames(cfg) == ['andersnb', 'benkirk', 'csgteam']
        assert leak.preserved_usernames({}) == []

    def test_accepted_exposures_are_read_as_table_dot_column(self, leak):
        cfg = {'anonymization': {'accepted_exposures': ['adhoc_system_account_entry.username']}}
        assert leak.accepted_exposures(cfg) == {'adhoc_system_account_entry.username'}
        assert leak.accepted_exposures({}) == set()

    def test_query_excludes_anonymized_shape_and_preserved_names(self, leak):
        q = leak.leak_query('comp_charge_summary', 'act_username', ['benkirk', 'csgteam'])
        assert "NOT LIKE 'user\\_%'" in q
        assert "NOT IN ('benkirk', 'csgteam')" in q
        assert '`comp_charge_summary`' in q and '`act_username`' in q
