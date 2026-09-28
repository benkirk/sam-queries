"""Tests for collector spool mode (collectors/lib/commands.py, bin/run-manifest.sh).

Spool mode splits a collector in two: the host runs the manifest with bash and
writes each command's output to files; the collector (in a container) parses
them. Ssh mode must keep issuing exactly the commands it always did.
"""

import importlib
import importlib.util
import logging
import os
import subprocess
import sys
import time

import pytest

from _paths import REPO_ROOT

_LIB = os.path.join(str(REPO_ROOT), "collectors", "lib")
_RUN_MANIFEST = os.path.join(str(REPO_ROOT), "collectors", "bin", "run-manifest.sh")


def _load_collectors_lib():
    # A private package name: collectors/lib on sys.path would shadow SAM's `config`
    # module (see test_collector_queue_parser.py), and relative imports need a package.
    name = "_collectors_lib_under_test"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(_LIB, "__init__.py"), submodule_search_locations=[_LIB])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return {m: importlib.import_module(f"{name}.{m}")
            for m in ("commands", "exceptions", "pbs_client", "ssh_utils", "config",
                      "base_collector", "parsers.filesystems", "parsers.nodes")}


LIB = _load_collectors_lib()
commands = LIB["commands"]
exc = LIB["exceptions"]


def _spool(tmp_path, files, age=0):
    """Write a spool: files maps key -> (rc, stdout[, stderr])."""
    d = tmp_path / "spool"
    d.mkdir()
    (d / "scrape.meta").write_text(f"host=testhost\nfinished={int(time.time() - age)}\n")
    for key, entry in files.items():
        rc, out, err = (tuple(entry) + ("",))[:3]
        (d / f"{key}.rc").write_text(f"{rc}\n")
        (d / f"{key}.out").write_text(out)
        (d / f"{key}.err").write_text(err)
    return str(d)


DF_OK = """Filesystem     1TiB-blocks    Used Available Use% Mounted on
csfs1              4096TiB 1743TiB   2354TiB  43% /glade/work
~~~
Filesystem         Inodes      IUsed      IFree IUse% Mounted on
csfs1          7340032000 5522051295 1817980705   76% /glade/work
"""

LOGIN_OK = "23.74 20.26 20.88 44/27533 68070\n---\n58\n---\n128\n---\n0 root\n40001 alice\n40002 bob\n"


class TestSshModeCommandsAreUnchanged:
    """The bare-metal cron and off-cluster (laptop) runs depend on these exact strings."""

    @pytest.fixture
    def calls(self, monkeypatch):
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append((cmd, kwargs["timeout"]))
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        monkeypatch.setattr(commands.subprocess, "run", fake_run)
        return seen

    def test_pbs_commands(self, calls):
        pbs = LIB["pbs_client"].PBSClient("casper", timeout=30)
        pbs.get_nodes_json(); pbs.get_jobs_json(); pbs.get_queues_json(); pbs.get_reservations()
        assert calls == [
            ('ssh -o ConnectTimeout=30 casper "pbsnodes -aj -F json"', 30),
            ('ssh -o ConnectTimeout=30 casper "qstat -f -F json"', 30),
            ('ssh -o ConnectTimeout=30 casper "qstat -Q -f -F json"', 30),
            ('ssh -o ConnectTimeout=30 casper "pbs_rstat -f"', 30),
        ]

    def test_login_probe_is_the_nested_hop(self, calls):
        LIB["ssh_utils"].LoginNodeCollector("derecho", timeout=10)._collect_single_node_safe("derecho3")
        assert calls == [(
            "ssh -o ConnectTimeout=10 derecho "
            "\"ssh derecho3 'cat /proc/loadavg; echo ---; who | wc -l; echo ---; nproc --all; "
            "echo ---; ps -eo uid=,user:32= | sort -u'\" ", 20)]

    def test_df_is_one_joined_command(self, calls):
        pbs = LIB["pbs_client"].PBSClient("casper", timeout=30)
        LIB["parsers.filesystems"].FilesystemParser.collect_and_parse(pbs, ["/a", "/b"])
        assert calls == [(
            'ssh -o ConnectTimeout=30 casper "BLOCKSIZE=TiB df /a; echo "~~~"; df -i /a; '
            'echo "---"; BLOCKSIZE=TiB df /b; echo "~~~"; df -i /b"', 30)]

    def test_jhlnodes_runs_the_site_script(self, calls):
        commands.SshSource("casper").jhlnodes()
        assert calls == [('ssh -o ConnectTimeout=10 casper "/glade/u/home/csgteam/bin/jhlnodes"', 30)]


class TestManifest:
    def test_keys_and_local_commands(self):
        entries = commands.manifest(pbs=True, filesystems=["/glade/u/home", "/glade/work"],
                                    login_nodes=[{"name": "derecho1", "type": "cpu"}, "derecho2"])
        keys = [k for k, _ in entries]
        assert keys == ["pbsnodes", "qstat", "qstat_Q", "pbs_rstat", "df.0", "df.1",
                        "login.derecho1", "login.derecho2"]
        cmds = dict(entries)
        assert cmds["pbsnodes"] == "pbsnodes -aj -F json"  # local on the host: no ssh
        assert cmds["login.derecho2"].startswith("ssh -o BatchMode=yes -o ConnectTimeout=10 derecho2 '")
        assert all("\t" not in c and "\n" not in c for c in cmds.values())

    def test_jupyterhub_needs_only_jhlnodes(self):
        assert [k for k, _ in commands.manifest(jhlnodes=True)] == ["jhlnodes"]


class TestSpoolSource:
    def test_missing_spool_is_a_config_error(self, tmp_path):
        with pytest.raises(exc.ConfigError, match="no spool"):
            commands.SpoolSource(str(tmp_path / "absent"))

    def test_stale_spool_is_refused(self, tmp_path):
        with pytest.raises(exc.ConfigError, match="stale"):
            commands.SpoolSource(_spool(tmp_path, {}, age=3600), max_age=600)

    def test_read(self, tmp_path):
        src = commands.SpoolSource(_spool(tmp_path, {"qstat": (0, "out"), "pbs_rstat": (1, "", "boom")}))
        assert src.host == "testhost"
        assert src.read("qstat") == (0, "out", "")
        assert src.read("pbs_rstat") == (1, "", "boom")
        assert src.read("absent")[0] == 127


class TestSpoolConsumers:
    def test_pbs_client(self, tmp_path):
        src = commands.SpoolSource(_spool(tmp_path, {
            "pbsnodes": (0, '{"nodes": {}}'), "qstat": (0, "not json"), "pbs_rstat": (2, "", "no server")}))
        pbs = LIB["pbs_client"].PBSClient("casper", source=src)
        assert pbs.get_nodes_json() == {"nodes": {}}
        with pytest.raises(exc.PBSParseError):
            pbs.get_jobs_json()
        with pytest.raises(exc.PBSCommandError, match="no server"):
            pbs.get_reservations()
        with pytest.raises(exc.PBSCommandError):
            pbs.get_queues_json()  # not captured at all

    def test_one_failed_df_degrades_only_its_path(self, tmp_path):
        src = commands.SpoolSource(_spool(tmp_path, {"df.0": (0, DF_OK), "df.1": (124, "", "timeout")}))
        pbs = LIB["pbs_client"].PBSClient("casper", source=src)
        ok, bad = LIB["parsers.filesystems"].FilesystemParser.collect_and_parse(pbs, ["/glade/work", "/x"])
        assert (ok["filesystem_name"], ok["degraded"], ok["capacity_tb"]) == ("/glade/work", False, 4096)
        assert ok["capacity_inodes"] == 7340032000
        assert (bad["filesystem_name"], bad["degraded"], bad["capacity_tb"]) == ("/x", True, None)

    def test_login_nodes(self, tmp_path):
        src = commands.SpoolSource(_spool(tmp_path, {"login.n1": (0, LOGIN_OK), "login.n2": (255, "", "denied")}))
        nodes = LIB["ssh_utils"].LoginNodeCollector("derecho", source=src).collect_login_node_data(
            [{"name": "n1"}, {"name": "n2"}])
        by_name = {n["node_name"]: n for n in nodes}
        assert by_name["n1"]["available"] and by_name["n1"]["users"] == ["alice", "bob"]
        assert by_name["n1"]["load_1min"] == pytest.approx(23.74 / 128 * 100)
        assert by_name["n2"]["degraded"] and not by_name["n2"]["available"]


class TestEmptyJobListIsFlagged:
    """The bare /opt/pbs/bin/qstat lists only the caller's jobs and exits 0."""

    def test_error_when_cores_allocated_but_no_jobs(self, caplog):
        collector = object.__new__(LIB["base_collector"].BaseCollector)
        collector.logger = logging.getLogger("test")
        with caplog.at_level(logging.ERROR):
            collector._check_jobs_visible({"running_jobs": 0, "cpu_cores_allocated": 512})
            collector._check_jobs_visible({"running_jobs": 0, "cpu_cores_allocated": 0})
            collector._check_jobs_visible({"running_jobs": 3, "cpu_cores_allocated": 512})
        assert len(caplog.records) == 1 and "site wrapper" in caplog.text


class TestRunManifest:
    def _run(self, spool, manifest):
        return subprocess.run(["bash", _RUN_MANIFEST, str(spool)], input=manifest,
                              capture_output=True, text=True, timeout=60)

    def test_captures_each_command(self, tmp_path):
        spool = tmp_path / "casper"
        r = self._run(spool, "ok\techo hello\nfails\techo oops >&2; exit 3\n\n")
        assert r.returncode == 0 and "fails" in r.stderr
        assert spool.is_symlink()
        assert (spool / "ok.out").read_text() == "hello\n"
        assert (spool / "ok.rc").read_text().strip() == "0"
        assert (spool / "fails.rc").read_text().strip() == "3"
        assert (spool / "fails.err").read_text() == "oops\n"
        meta = (spool / "scrape.meta").read_text()
        assert "commands=2" in meta and "finished=" in meta
        # and the collector side accepts it
        assert commands.SpoolSource(str(spool)).read("ok") == (0, "hello\n", "")

    def test_rerun_replaces_the_previous_capture(self, tmp_path):
        spool = tmp_path / "casper"
        self._run(spool, "a\techo 1\n")
        first = os.readlink(spool)
        self._run(spool, "a\techo 2\n")
        assert os.readlink(spool) != first
        assert not (tmp_path / first).exists()
        assert (spool / "a.out").read_text() == "2\n"

    @pytest.mark.parametrize("manifest", ["../escape\techo x\n", "", "nocommand\t\n"])
    def test_rejects_bad_manifests(self, tmp_path, manifest):
        spool = tmp_path / "casper"
        assert self._run(spool, manifest).returncode == 2
        assert not spool.exists()
        assert list(tmp_path.iterdir()) == []

    def test_needs_no_python(self):
        with open(_RUN_MANIFEST) as f:
            body = f.read()
        assert "python" not in body.replace("our Python env", "")
