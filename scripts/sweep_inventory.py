#!/usr/bin/env python3
"""Inventory the code an unplanned-city sweep reads: helpers to lift, duplicates, CSS and JS cruft.

A report, not a gate: it always exits 0, and every detector is a heuristic to read, not a verdict.
The totals line per detector is what one sweep compares with the last (docs/plans/UNPLANNED_CITY_LEDGER.md).

    scripts/sweep_inventory.py                     # every detector, whole tree
    scripts/sweep_inventory.py --area css          # one area: py | js | css | templates | docs
    scripts/sweep_inventory.py --since origin/staging~5 --top 30
    scripts/sweep_inventory.py --json > inventory.json
    scripts/sweep_inventory.py --area js --jscpd   # also run jscpd through npx, when present
    scripts/sweep_inventory.py --area docs --gh    # plans ready to move to docs/plans/implemented/
"""
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

PY_ROOT = Path("src")
JS_ROOT = Path("src/webapp/static/js")
CSS_ROOT = Path("src/webapp/static/css")
TEMPLATE_ROOT = Path("src/webapp/templates")
VENDOR_ROOT = Path("src/webapp/static/vendor")
PLANS_ROOT = Path("docs/plans")
AREAS = {
    "py": ("private-imports", "dup-functions"),
    "css": ("css-dead", "css-shape"),
    "templates": ("inline-styles",),
    "js": ("js-dup", "js-dead"),
    "docs": ("plans-stale",),
}
PLAN_IDLE_DAYS = 14       # untouched this long, every cited PR merged: a retirement lead
MERGED_REF = "origin/staging"
_PR_REF = re.compile(r"(?<![\w/])#(\d{2,5})\b")
_STATUS = re.compile(r"^\*\*Status:?\*?\*?:?\s*(.+)$", re.M | re.I)
OPEN_STATUS = ("unbuilt", "deferred", "brainstorm", "sketch", "in progress", "not started")
MIN_FUNCTION_NODES = 40   # smaller bodies repeat by accident (getters, one-line guards)
_TOKEN = re.compile(r"[A-Za-z_][\w-]*")
# Styled on purpose though nothing names them yet; css-dead reports these apart, and flags a stale entry.
CSS_KEEP = {
    "table-danger": "dark-mode guard for Bootstrap's contextual table variant",
    "table-success": "dark-mode guard for Bootstrap's contextual table variant",
}
# A stem followed by an interpolation: Jinja {{ / ~, JS ${ / +, Python f-string { / %s.
_DYNAMIC_STEM = re.compile(r"""([A-Za-z_][\w-]*-)(?=\{|\$\{|%s|['"]\s*[+~])""")
_ATTR_OPEN = re.compile(r"""([\w:-]+)=\s*["'][^"'<>]*$""")
_NOT_CLASS_ATTRS = ("id", "for", "name", "href")
JSCPD_IGNORE_PATTERN = r"/\*\s*=+"   # section banners: every /* ==== */ pair otherwise reads as a clone


def files(root, pattern):
    """Sorted files under ``root``, skipping vendor, caches and egg-info."""
    out = []
    for path in sorted(root.rglob(pattern)):
        parts = set(path.parts)
        if "vendor" in parts or "__pycache__" in parts or any(p.endswith(".egg-info") for p in parts):
            continue
        out.append(path)
    return out


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def module_name(path, root=PY_ROOT):
    """Dotted module for a file under ``root`` (``src/sam/x.py`` -> ``sam.x``)."""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def private_imports(py_files, root=PY_ROOT):
    """``from other.module import _name``: a private helper with a consumer outside its module."""
    uses = defaultdict(list)
    for path in py_files:
        try:
            tree = ast.parse(read(path))
        except SyntaxError:
            continue
        here = module_name(path, root)
        package = here if path.name == "__init__.py" else here.rpartition(".")[0]
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                base = package.split(".")[:len(package.split(".")) - node.level + 1]
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            if source == here:
                continue
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    uses[(source, alias.name)].append(f"{path.as_posix()}:{node.lineno}")
    # A package's own ``_shared`` module is private by convention: those rank last.
    rows = [{"helper": f"{src}.{name}", "importers": len(sites), "sites": sites,
             "package_private": src.rpartition(".")[2].startswith("_")}
            for (src, name), sites in uses.items()]
    return sorted(rows, key=lambda r: (r["package_private"], -r["importers"], r["helper"]))


def _fingerprint(fn):
    """The function's body and arguments with names, docstring and positions dropped."""
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return None, 0
    nodes = sum(1 for stmt in body for _ in ast.walk(stmt))
    dumped = ast.dump(fn.args, include_attributes=False) + "".join(
        ast.dump(stmt, include_attributes=False) for stmt in body)
    return dumped, nodes


def dup_functions(py_files, root=PY_ROOT, min_nodes=MIN_FUNCTION_NODES):
    """Functions whose normalized bodies are identical, at two or more locations."""
    groups = defaultdict(list)
    for path in py_files:
        try:
            tree = ast.parse(read(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key, size = _fingerprint(node)
                if key and size >= min_nodes:
                    groups[key].append((size, f"{path.as_posix()}:{node.lineno} {node.name}"))
    rows = [{"size": sites[0][0], "copies": len(sites), "sites": [s for _, s in sites]}
            for sites in groups.values() if len(sites) > 1]
    return sorted(rows, key=lambda r: (-r["size"] * (r["copies"] - 1), r["sites"]))


def _css_rules(text):
    """``(selector, body)`` pairs, comments stripped, @-rule wrappers skipped."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selector = match.group(1).strip()
        if selector and not selector.startswith("@"):
            yield selector, match.group(2)


def _selector_classes(selector):
    """Class names in a selector, ignoring anything inside attribute brackets."""
    return set(re.findall(r"\.(-?[_a-zA-Z][\w-]*)", re.sub(r"\[[^\]]*\]", "", selector)))


def _dynamic_stems(texts):
    """Stems (``burn-``) built into a class at runtime; an ``id=``/``for=``/``data-*=`` value does not count."""
    stems = set()
    for text in texts:
        for match in _DYNAMIC_STEM.finditer(text):
            attr = _ATTR_OPEN.search(text, max(0, match.start() - 200), match.start())
            if attr and (attr.group(1) in _NOT_CLASS_ATTRS or attr.group(1).startswith("data-")):
                continue
            stems.add(match.group(1))
    return stems


def css_dead(css_files, corpus_files, keep=CSS_KEEP):
    """Classes our CSS styles that no template, script or Python string names.

    ``dynamic``: the class's stem (``burn-`` for ``burn-3``) is interpolated somewhere, so it is
    likely built at runtime. ``kept``: the ``keep`` reason. ``stale_keeps``: keep entries no CSS styles.
    """
    texts = [read(path) for path in corpus_files]
    seen = set()
    for text in texts:
        seen.update(_TOKEN.findall(text))
    stems = _dynamic_stems(texts)
    rows, styled = [], set()
    for path in css_files:
        defined = Counter()
        for selector, _ in _css_rules(read(path)):
            defined.update(_selector_classes(selector))
        styled.update(defined)
        for cls in sorted(defined):
            if cls not in seen:
                rows.append({"class": cls, "file": path.as_posix(), "rules": defined[cls],
                             "dynamic": cls.rpartition("-")[0] + "-" in stems, "kept": keep.get(cls)})
    rows.sort(key=lambda r: (bool(r["kept"]), r["dynamic"], r["file"], r["class"]))
    return {"classes": rows, "stale_keeps": sorted(set(keep) - styled)}


def css_shape(css_files):
    """Per file: lines, rules, ``!important``; plus declaration blocks repeated across rules."""
    per_file, blocks = [], defaultdict(list)
    for path in css_files:
        text = read(path)
        rules = list(_css_rules(text))
        per_file.append({"file": path.as_posix(), "lines": text.count("\n"),
                         "rules": len(rules), "important": text.count("!important")})
        for selector, body in rules:
            decls = sorted(d.strip() for d in body.split(";") if d.strip())
            if len(decls) >= 2:
                blocks[";".join(decls)].append(f"{path.as_posix()}: {selector[:60]}")
    repeated = [{"declarations": key.count(";") + 1, "copies": len(sites), "sites": sites}
                for key, sites in blocks.items() if len(sites) > 1]
    per_file.sort(key=lambda r: -r["lines"])
    repeated.sort(key=lambda r: (-r["declarations"] * (r["copies"] - 1), r["sites"]))
    return {"files": per_file, "repeated_blocks": repeated}


def inline_styles(template_files):
    """``style="`` attributes per template, worst first."""
    rows = [{"file": p.as_posix(), "count": read(p).count('style="')} for p in template_files]
    return sorted((r for r in rows if r["count"]), key=lambda r: -r["count"])


_JS_DEFS = re.compile(r"(?:^|[\s;])function\s+([A-Za-z_$][\w$]*)\s*\("
                      r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>)",
                      re.M)
_JS_EVENTS = re.compile(r"""(?:addEventListener|htmx\.on)\(\s*['"](htmx:[\w-]+)['"]""")


def js_dup(js_files):
    """Function names defined in two or more files, and htmx events listened for in two or more files."""
    defs, events = defaultdict(set), defaultdict(Counter)
    for path in js_files:
        text = read(path)
        for match in _JS_DEFS.finditer(text):
            defs[match.group(1) or match.group(2)].add(path.as_posix())
        for event in _JS_EVENTS.findall(text):
            events[event][path.as_posix()] += 1
    names = [{"name": n, "files": sorted(f)} for n, f in defs.items() if len(f) > 1]
    listeners = [{"event": e, "registrations": sum(c.values()), "files": dict(sorted(c.items()))}
                 for e, c in events.items() if len(c) > 1]
    names.sort(key=lambda r: (-len(r["files"]), r["name"]))
    listeners.sort(key=lambda r: (-len(r["files"]), r["event"]))
    return {"names": names, "listeners": listeners}


_JS_GLOBALS = re.compile(r"window\.([A-Za-z_$][\w$]*)\s*=(?!=)")
_JS_ACTIONS = re.compile(r"""registerAction\(\s*['"]([\w-]+)['"]""")
_MARKUP_ACTIONS = re.compile(r"""data-action(?:-change|-input|-submit)?=\s*["']([\w-]+)["']""")


def js_dead(js_files, corpus_files):
    """``window.*`` exports no other file names, actions no markup uses, markup actions nothing registers."""
    texts = {p.as_posix(): read(p) for p in corpus_files}
    exports, registered = {}, {}
    for path in js_files:
        text = read(path)
        for name in _JS_GLOBALS.findall(text):
            exports.setdefault(name, path.as_posix())
        for name in _JS_ACTIONS.findall(text):
            registered.setdefault(name, path.as_posix())
    used = {name for text in texts.values() for name in _MARKUP_ACTIONS.findall(text)}
    markup = {name for k, text in texts.items() if not k.endswith(".js") for name in _MARKUP_ACTIONS.findall(text)}
    globals_ = [{"name": n, "file": f} for n, f in sorted(exports.items())
                if not any(re.search(rf"\b{re.escape(n)}\b", t) for k, t in texts.items() if k != f)]
    unused = [{"name": n, "file": f} for n, f in sorted(registered.items()) if n not in used]
    return {"globals": globals_, "unused_actions": unused, "unregistered_actions": sorted(markup - set(registered))}


def plans_stale(plan_files, merged, last_touched, now, idle_days=PLAN_IDLE_DAYS):
    """Top-level plans with cited PRs, idle days and open checkboxes; ``candidate`` when ready to retire."""
    rows = []
    for path in plan_files:
        text = read(path)
        prs = sorted({int(n) for n in _PR_REF.findall(text)})
        idle = int((now - last_touched.get(path.as_posix(), now)) // 86400)
        unmerged = [n for n in prs if n not in merged]
        status = _STATUS.search(text)
        status = status.group(1).split("**")[0].strip("* ") if status else ""
        still_open = any(word in status.lower() for word in OPEN_STATUS)
        rows.append({"file": path.as_posix(), "idle_days": idle, "prs": prs, "unmerged": unmerged,
                     "open_boxes": text.count("- [ ]"), "status": status,
                     "candidate": bool(prs) and not unmerged and not still_open and idle >= idle_days})
    return sorted(rows, key=lambda r: (not r["candidate"], -r["idle_days"]))


def _git_lines(*args):
    """Output lines of a git command, [] on failure, None when git is not installed (the CI image)."""
    if shutil.which("git") is None:
        return None
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return out.stdout.splitlines() if out.returncode == 0 else []


def _plan_inputs(use_gh=False):
    """Tracked top-level plans, merged PR numbers, last commit time per plan.

    Offline, a PR counts as merged when ``(#N)`` appears in a MERGED_REF commit message; that misses
    PRs squashed into a promotion with an empty body. ``use_gh`` asks ``gh pr list --state merged``.
    """
    tracked = _git_lines("ls-files", f"{PLANS_ROOT.as_posix()}/*.md")
    if tracked is None:
        return None
    plans = [Path(p) for p in tracked if Path(p).parent == PLANS_ROOT]
    ref = MERGED_REF if _git_lines("rev-parse", "--verify", "-q", MERGED_REF) else "HEAD"
    merged = None
    if use_gh and shutil.which("gh"):
        out = subprocess.run(["gh", "pr", "list", "--state", "merged", "--limit", "5000",
                              "--json", "number", "--jq", ".[].number"], capture_output=True, text=True)
        merged = {int(n) for n in out.stdout.split() if n.isdigit()} if out.returncode == 0 else None
    if merged is None:   # offline: also counts an issue cited as (#N) in a commit body
        merged = {int(m) for line in _git_lines("log", ref, "--format=%B") for m in re.findall(r"\(#(\d+)\)", line)}
    touched = {}
    for path in plans:
        stamp = _git_lines("log", "-1", "--format=%ct", "--", path.as_posix())
        if stamp:
            touched[path.as_posix()] = int(stamp[0])
    return plans, merged, touched


def changed_since(rev):
    return {Path(p).as_posix() for p in _git_lines("diff", "--name-only", rev) or []}


def _touches(sites, changed):
    """Whether any ``path:line ...`` or ``path: ...`` site names a changed file."""
    return any(re.split(r"[: ]", site, maxsplit=1)[0] in changed for site in sites)


def run(detectors, changed=None, use_gh=False):
    """``{detector: rows}`` over the whole tree; with ``changed``, only findings touching those files.

    Duplicates and shared names are found tree-wide first, so a new copy of an old helper still shows.
    """
    py = files(PY_ROOT, "*.py")
    css = files(CSS_ROOT, "*.css")
    corpus = (files(TEMPLATE_ROOT, "*.html") + files(JS_ROOT, "*.js") + py
              + sorted(VENDOR_ROOT.rglob("*.js")))
    keep = (lambda sites: True) if changed is None else (lambda sites: _touches(sites, changed))
    out = {}
    if "private-imports" in detectors:
        source_files = {module_name(p): p.as_posix() for p in py}
        out["private-imports"] = [
            r for r in private_imports(py)
            if keep(r["sites"] + [source_files.get(r["helper"].rpartition(".")[0], "")])]
    if "dup-functions" in detectors:
        out["dup-functions"] = [r for r in dup_functions(py) if keep(r["sites"])]
    if "css-dead" in detectors:
        dead = css_dead(css, corpus)
        out["css-dead"] = {"classes": [r for r in dead["classes"] if keep([r["file"]])],
                           "stale_keeps": dead["stale_keeps"] if changed is None else []}
    if "css-shape" in detectors:
        shape = css_shape(css)
        out["css-shape"] = {"files": [r for r in shape["files"] if keep([r["file"]])],
                            "repeated_blocks": [r for r in shape["repeated_blocks"] if keep(r["sites"])]}
    if "inline-styles" in detectors:
        out["inline-styles"] = [r for r in inline_styles(files(TEMPLATE_ROOT, "*.html")) if keep([r["file"]])]
    if "js-dup" in detectors:
        dup = js_dup(files(JS_ROOT, "*.js"))
        out["js-dup"] = {"names": [r for r in dup["names"] if keep(r["files"])],
                         "listeners": [r for r in dup["listeners"] if keep(list(r["files"]))]}
    if "js-dead" in detectors:
        dead = js_dead(files(JS_ROOT, "*.js"), corpus)
        out["js-dead"] = {"globals": [r for r in dead["globals"] if keep([r["file"]])],
                          "unused_actions": [r for r in dead["unused_actions"] if keep([r["file"]])],
                          "unregistered_actions": dead["unregistered_actions"] if changed is None else []}
    if "plans-stale" in detectors:
        inputs = _plan_inputs(use_gh)
        out["plans-stale"] = None if inputs is None else plans_stale(*inputs, time.time())
    return out


def report(result, top):
    """Print one table per detector, worst first, each ending in a totals line."""
    if "private-imports" in result:
        rows = result["private-imports"]
        print(f"\n== private-imports: private helpers used outside their module")
        for r in rows[:top]:
            tag = " (package-private module)" if r["package_private"] else ""
            print(f"  {r['importers']:3d}  {r['helper']}{tag}  <- {', '.join(r['sites'][:3])}")
        print(f"  total: {len(rows)} helpers, {sum(r['importers'] for r in rows)} import sites, "
              f"{sum(r['package_private'] for r in rows)} from package-private modules")
    if "dup-functions" in result:
        rows = result["dup-functions"]
        print(f"\n== dup-functions: identical bodies (>= {MIN_FUNCTION_NODES} AST nodes)")
        for r in rows[:top]:
            print(f"  {r['size']:4d} nodes x{r['copies']}  " + " | ".join(r["sites"]))
        print(f"  total: {len(rows)} groups, {sum(r['copies'] - 1 for r in rows)} extra copies")
    if "css-dead" in result:
        rows, stale = result["css-dead"]["classes"], result["css-dead"]["stale_keeps"]
        print("\n== css-dead: classes styled but never named (check dynamic names before deleting)")
        for r in rows[:top]:
            tag = f"  (kept: {r['kept']})" if r["kept"] else "  (dynamic?)" if r["dynamic"] else ""
            print(f"  .{r['class']}  ({r['rules']} rule{'s' if r['rules'] != 1 else ''})  {r['file']}{tag}")
        for cls in stale:
            print(f"  .{cls}  (stale CSS_KEEP entry: no CSS styles it)")
        dead = [r for r in rows if not r["kept"]]
        print(f"  total: {len(dead)} classes, {sum(r['dynamic'] for r in dead)} with a dynamic stem, "
              f"{len(rows) - len(dead)} kept, {len(stale)} stale keeps")
    if "css-shape" in result:
        shape = result["css-shape"]
        print("\n== css-shape: lines, rules and !important per file; repeated declaration blocks")
        for r in shape["files"][:top]:
            print(f"  {r['lines']:5d} lines {r['rules']:4d} rules {r['important']:3d} !important  {r['file']}")
        for r in shape["repeated_blocks"][:top]:
            print(f"  {r['declarations']} declarations x{r['copies']}  " + " | ".join(r["sites"][:3]))
        print(f"  total: {sum(r['lines'] for r in shape['files'])} lines, "
              f"{sum(r['important'] for r in shape['files'])} !important, "
              f"{len(shape['repeated_blocks'])} repeated blocks")
    if "inline-styles" in result:
        rows = result["inline-styles"]
        print('\n== inline-styles: style="" attributes per template')
        for r in rows[:top]:
            print(f"  {r['count']:4d}  {r['file']}")
        print(f"  total: {sum(r['count'] for r in rows)} attributes in {len(rows)} templates")
    if "js-dup" in result:
        dup = result["js-dup"]
        print("\n== js-dup: names defined in several files; htmx events listened for in several files")
        for r in dup["names"][:top]:
            print(f"  {r['name']}()  " + ", ".join(r["files"]))
        for r in dup["listeners"][:top]:
            print(f"  {r['event']} x{r['registrations']}  " + ", ".join(r["files"]))
        print(f"  total: {len(dup['names'])} shared names, {len(dup['listeners'])} shared events")
    if "js-dead" in result:
        dead = result["js-dead"]
        print("\n== js-dead: window.* exports nothing else names; registerAction names vs data-action markup")
        for r in dead["globals"][:top]:
            print(f"  window.{r['name']}  (no other file names it)  {r['file']}")
        for r in dead["unused_actions"][:top]:
            print(f"  action {r['name']}  (registered, no markup)  {r['file']}")
        for name in dead["unregistered_actions"][:top]:
            print(f"  action {name}  (in markup, never registered; may be a third-party widget's)")
        print(f"  total: {len(dead['globals'])} unreferenced globals, {len(dead['unused_actions'])} unused actions, "
              f"{len(dead['unregistered_actions'])} unregistered actions")
    if "plans-stale" in result and result["plans-stale"] is None:
        print("\n== plans-stale: skipped, git not found")
    elif "plans-stale" in result:
        rows = result["plans-stale"]
        print(f"\n== plans-stale: top-level plans; RETIRE = every cited PR merged, no open "
              f"**Status:**, idle >= {PLAN_IDLE_DAYS} days")
        for r in rows[:top]:
            tag = "RETIRE" if r["candidate"] else "      "
            why = f"unmerged {', '.join('#%d' % n for n in r['unmerged'][:4])}" if r["unmerged"] else (
                "no PR cited" if not r["prs"] else f"{len(r['prs'])} PR{'s' if len(r['prs']) != 1 else ''} merged")
            boxes = f", {r['open_boxes']} open boxes" if r["open_boxes"] else ""
            status = f"  Status: {r['status'][:50]}" if r["status"] else ""
            print(f"  {tag} {r['idle_days']:4d}d  {r['file']}  ({why}{boxes}){status}")
        print(f"  total: {len(rows)} plans, {sum(r['candidate'] for r in rows)} retirement candidates")


def jscpd_pairs(report, cwd):
    """Clone pairs from jscpd's JSON report as ``file:start-end <-> file:start-end (N lines)``."""
    def site(f):
        return f"{os.path.relpath(f['name'], cwd)}:{f['start']}-{f['end']}"
    return [f"{site(d['firstFile'])} <-> {site(d['secondFile'])}  ({d['lines']} lines)"
            for d in report.get("duplicates", [])]


def run_jscpd(area):
    """jscpd over the area's roots with the repo's .jscpd.json, when npx is installed; every pair printed."""
    if shutil.which("npx") is None:
        print("\n== jscpd: skipped, npx not found")
        return
    roots = {"py": [PY_ROOT], "js": [JS_ROOT], "css": [CSS_ROOT], "templates": [TEMPLATE_ROOT]}
    paths = roots.get(area, [PY_ROOT, JS_ROOT, CSS_ROOT, TEMPLATE_ROOT])
    cmd = ["npx", "--yes", "jscpd", "--config", ".jscpd.json", "--ignore", "**/vendor/**,**/__pycache__/**",
           "--ignore-pattern", JSCPD_IGNORE_PATTERN, *map(str, paths)]
    print("\n== jscpd: " + " ".join(cmd))
    with tempfile.TemporaryDirectory() as out_dir:
        proc = subprocess.run([*cmd, "--reporters", "json", "--output", out_dir, "--silent", "--absolute"],
                              capture_output=True, text=True)
        try:
            report = json.loads((Path(out_dir) / "jscpd-report.json").read_text())
        except (OSError, ValueError):
            print("\n".join((proc.stderr or proc.stdout).splitlines()[-20:]) or "  no report written")
            return
    for pair in jscpd_pairs(report, os.getcwd()):
        print(f"  {pair}")
    total = report["statistics"]["total"]
    print(f"  total: {total['clones']} clones, {total['duplicatedLines']} of {total['lines']} lines "
          f"({total['percentage']:.2f}%) in {total['sources']} files")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--area", choices=sorted(AREAS), help="one area's detectors (default: all)")
    ap.add_argument("--since", metavar="REV", help="only files changed since REV")
    ap.add_argument("--top", type=int, default=15, metavar="N", help="rows per table (default 15)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--jscpd", action="store_true", help="also run jscpd through npx")
    ap.add_argument("--gh", action="store_true", help="plans-stale: ask gh which PRs merged (network)")
    args = ap.parse_args(argv)
    detectors = AREAS[args.area] if args.area else tuple(d for ds in AREAS.values() for d in ds)
    result = run(detectors, changed_since(args.since) if args.since else None, use_gh=args.gh)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        report(result, args.top)
    if args.jscpd:
        run_jscpd(args.area)
    return 0


if __name__ == "__main__":
    sys.exit(main())
