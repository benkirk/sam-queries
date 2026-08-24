#!/usr/bin/env python3
"""Report the doc-line ratio of a tree, the way issue #461 measured it.

A "doc line" is a line inside a module/class/function docstring, or a line
whose only content is a `#` comment. A trailing comment on a line of code
counts as code, because the line carries code. Blank lines count toward the
total but never toward either side, so two ratios are reported: over all
lines, and over non-blank lines only.

The point is comparability over time, not precision about any one file --
so the classification is `ast` and `tokenize`, never a regex, and it has not
changed since the baseline measurement.

    scripts/doc_ratio.py                      # per-tree table for src/
    scripts/doc_ratio.py src tests            # several trees
    scripts/doc_ratio.py --top 20             # worst files by absolute doc lines
    scripts/doc_ratio.py --top 20 --by ratio  # ...or by density
    scripts/doc_ratio.py --since origin/staging   # only files changed since a rev

See docs/plans/DOC_SLIMMING.md for what the numbers are being steered toward.
"""
import argparse
import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

DEFAULT_TREES = ("src/sam", "src/webapp", "src/cli", "src/scheduling",
                 "src/querykit", "src/system_status")


def classify(path):
    """(total, code, docstring, comment) line counts for one Python file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    lines = text.splitlines()
    doc = set()
    try:
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                doc.update(range(body[0].lineno, body[0].end_lineno + 1))
    except SyntaxError:
        pass
    comment = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            # Comment-only: nothing but whitespace before the `#`.
            if tok.type == tokenize.COMMENT and \
                    not lines[tok.start[0] - 1][:tok.start[1]].strip():
                comment.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    comment -= doc
    blank = sum(1 for line in lines if not line.strip())
    total = len(lines)
    code = total - blank - len(doc) - len(comment)
    return total, code, len(doc), len(comment)


def walk(trees, since=None):
    """(path, counts) for every Python file under `trees`."""
    changed = None
    if since:
        out = subprocess.run(["git", "diff", "--name-only", since],
                             capture_output=True, text=True).stdout.split()
        changed = {Path(p).as_posix() for p in out}
    for tree in trees:
        for path in sorted(Path(tree).rglob("*.py")):
            if changed is not None and path.as_posix() not in changed:
                continue
            counts = classify(path)
            if counts:
                yield path, counts


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("trees", nargs="*", default=list(DEFAULT_TREES))
    ap.add_argument("--top", type=int, metavar="N",
                    help="also list the N worst files")
    ap.add_argument("--by", choices=("lines", "ratio"), default="lines",
                    help="rank --top by absolute doc lines (default) or density")
    ap.add_argument("--since", metavar="REV",
                    help="only files changed since REV")
    args = ap.parse_args(argv)

    rows = list(walk(args.trees, args.since))
    if not rows:
        print("no Python files matched", file=sys.stderr)
        return 1

    by_tree = {}
    for path, (total, code, doc, com) in rows:
        key = next((t for t in args.trees
                    if path.as_posix().startswith(t.rstrip("/") + "/")),
                   path.parts[0])
        acc = by_tree.setdefault(key, [0, 0, 0, 0])
        for i, v in enumerate((total, code, doc, com)):
            acc[i] += v

    head = "%-22s%8s%8s%8s%9s%8s%11s" % (
        "tree", "total", "code", "docstr", "comment", "doc%", "nonblank%")
    print(head)
    print("-" * len(head))
    grand = [0, 0, 0, 0]
    for tree, (total, code, doc, com) in sorted(by_tree.items()):
        for i, v in enumerate((total, code, doc, com)):
            grand[i] += v
        print("%-22s%8d%8d%8d%9d%7.1f%%%10.1f%%" % (
            tree, total, code, doc, com,
            pct(doc + com, total), pct(doc + com, code + doc + com)))
    total, code, doc, com = grand
    print("-" * len(head))
    print("%-22s%8d%8d%8d%9d%7.1f%%%10.1f%%" % (
        "TOTAL", total, code, doc, com,
        pct(doc + com, total), pct(doc + com, code + doc + com)))

    if args.top:
        ranked = sorted(
            rows,
            key=lambda r: (r[1][2] + r[1][3]) if args.by == "lines"
            else pct(r[1][2] + r[1][3], r[1][0]),
            reverse=True)[:args.top]
        print("\n%-6s%7s%8s  %s" % ("doc", "total", "ratio", "file"))
        for path, (total, code, doc, com) in ranked:
            print("%-6d%7d%7.0f%%  %s" % (
                doc + com, total, pct(doc + com, total), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
