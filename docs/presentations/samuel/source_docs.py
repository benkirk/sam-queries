"""Comment and docstring lines in the source of one revision (refresh_data.sh's LOC split).

    python3 source_docs.py <repo> <rev>

"Source" is refresh_data.sh's rule: code files outside tests and docs/. Only Python is split;
a doc line is a docstring line or a comment-only line, as scripts/doc_ratio.py counts them.
"""
import io
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

repo, rev = sys.argv[1], sys.argv[2]
sys.path.insert(0, str(Path(repo) / "scripts"))
from doc_ratio import classify  # noqa: E402

TEST = re.compile(r"(^|/)tests?/|(^|/)(test_[^/]*|conftest)\.py$")
CODE = re.compile(r"\.(py|html|js|css|sh|lua|j2|jinja|ipynb)$")

with tempfile.TemporaryDirectory() as tmp:
    archive = subprocess.run(["git", "-C", repo, "archive", rev], check=True,
                             capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tmp, filter="data")
    doc = 0
    for p in Path(tmp).rglob("*.py"):
        rel = p.relative_to(tmp).as_posix()
        if TEST.search(rel) or rel.startswith("docs/") or not CODE.search(rel):
            continue
        counts = classify(p)
        if counts:
            doc += counts[2] + counts[3]
print(doc)
