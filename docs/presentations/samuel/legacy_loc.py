"""Legacy SAM's code lines, for the progression chart: data/legacy_loc.tsv (refresh time only).

    python3 legacy_loc.py [<legacy_sam>]      # default: <sam-queries>/legacy_sam

Text lines of code files only (no data dumps, LDIF or docs), each repo at its origin default
branch: legacy SAM itself (Java split into main and src/test), and the container zoo beside it,
less amieclient (upstream, XSEDE's) and sftp-server (retired).
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
LEGACY = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[2] / "legacy_sam"
SKIP = {"amieclient", "sftp-server"}
CODE = re.compile(r"\.(java|xhtml|xml|sql|js|css|properties|sh|jsp|html|py|go|pl|pm|rb|ts|"
                  r"yaml|yml|toml|conf|cfg|tf|j2|schema)$|(^|/)(Dockerfile|Makefile)[^/]*$")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout


def lines(repo):
    """{path: line count} for the text files at the repo's origin default branch."""
    ref = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
    out = {}
    for row in git(repo, "grep", "-I", "-c", "", ref, "--", ".").splitlines():
        _, path, n = row.rsplit(":", 2)           # <ref>:<path>:<count>
        out[path] = int(n)
    return out


legacy = {p: n for p, n in lines(LEGACY).items() if CODE.search(p)}
java_test = sum(n for p, n in legacy.items() if p.endswith(".java") and "src/test/" in p)
java = sum(n for p, n in legacy.items() if p.endswith(".java"))
zoo = sum(n for d in sorted((LEGACY / "container_zoo").iterdir())
          if d.is_dir() and d.name not in SKIP
          for p, n in lines(d).items() if CODE.search(p))
rows = {"java_main": java - java_test, "java_test": java_test, "java": java,
        "legacy_code": sum(legacy.values()), "zoo_code": zoo,
        "legacy_with_zoo": sum(legacy.values()) + zoo}
with open(HERE / "data/legacy_loc.tsv", "w") as f:
    f.write("measure\tlines\n" + "".join(f"{k}\t{v}\n" for k, v in rows.items()))
print(rows)
