#!/usr/bin/env python3
"""The brief a PR review starts from: the PR, its checks and diff, and the rules and skills to read.

    scripts/pr_review_brief.py 803                  # the brief, to stdout
    scripts/pr_review_brief.py 803 --json           # the same as one object
    scripts/pr_review_brief.py --paths a.py b.html  # the rule and skill mapping alone, no gh

Exits 0 always. Without gh (the CI image) the PR half is left out and the report says so.
The rule mapping reads each rule's ``paths:`` frontmatter, the shape test_docs.py pins.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RULES_DIR = Path(".claude/rules")
MEMORY_INDEX = Path(os.environ.get(
    "PR_REVIEW_MEMORY_INDEX",
    Path.home() / ".claude/projects/-Users-benkirk-codes-project-samuel-main/memory/MEMORY.md"))

#: Paths whose edits load the dashboard checklist; the sweep is the lens for every PR.
DASHBOARD_SKILL = "wire-dashboard-feature"
DASHBOARD_PREFIXES = ("src/webapp/dashboards/", "src/webapp/templates/", "src/webapp/static/")
LENS_SKILL = "unplanned-city-sweep"
EVERY_SESSION = "(no paths: loads in every session)"


def glob_to_regex(pattern):
    """A rule glob as a full-match regex: ``**`` crosses directories, ``*`` and ``?`` do not."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def rule_globs(rules_dir=RULES_DIR):
    """``{rule file name: [glob, ...]}``; an empty list for a rule with no ``paths:`` frontmatter."""
    found = {}
    for rule in sorted(Path(rules_dir).glob("*.md")):
        globs, in_front = [], False
        for line in rule.read_text(encoding="utf-8").splitlines():
            if line.strip() == "---":
                if in_front:
                    break
                in_front = True
                continue
            m = re.match(r'\s*-\s*"([^"]+)"', line) if in_front else None
            if m:
                globs.append(m.group(1))
        found[rule.name] = globs
    return found


def rules_for_paths(changed, rules_dir=RULES_DIR):
    """``{rule: [changed paths it loads for]}``, every-session rules included, unmatched rules left out."""
    out = {}
    for rule, globs in rule_globs(rules_dir).items():
        if not globs:
            out[rule] = [EVERY_SESSION]
            continue
        patterns = [glob_to_regex(g) for g in globs]
        hits = [p for p in changed if any(rx.match(p) for rx in patterns)]
        if hits:
            out[rule] = hits
    return out


def skills_for_paths(changed):
    skills = [LENS_SKILL]
    if any(p.startswith(DASHBOARD_PREFIXES) for p in changed):
        skills.append(DASHBOARD_SKILL)
    return skills


def memory_lines(number, index=MEMORY_INDEX):
    """Index lines naming ``#<number>``; [] when the index is not on this machine."""
    try:
        text = Path(index).read_text(encoding="utf-8")
    except OSError:
        return []
    needle = re.compile(r"#%d\b" % int(number))
    return [line.strip() for line in text.splitlines() if needle.search(line)]


def _run(cmd):
    """stdout of *cmd*, or None when the tool is missing or the call failed."""
    if shutil.which(cmd[0]) is None:
        return None
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def pr_brief(number):
    """Everything the review reads first; ``pr`` is None without gh."""
    raw = _run(["gh", "pr", "view", str(number), "--json",
                "title,body,baseRefName,headRefName,isDraft,additions,deletions,url,commits"])
    brief = {"number": int(number), "pr": None, "checks": [], "changed": [], "diff_stat": ""}
    if raw is None:
        return brief
    pr = json.loads(raw)
    pr["commits"] = [{"sha": c["oid"][:8], "title": c["messageHeadline"]}
                     for c in pr.get("commits", [])]
    brief["pr"] = pr
    checks = _run(["gh", "pr", "checks", str(number)]) or ""
    brief["checks"] = [{"name": f[0], "status": f[1]}
                       for f in (line.split("\t") for line in checks.splitlines()) if len(f) > 1]
    base, head = pr["baseRefName"], pr["headRefName"]
    _run(["git", "fetch", "-q", "origin", head, base])
    span = f"origin/{base}...origin/{head}"
    brief["changed"] = (_run(["git", "diff", "--name-only", span]) or "").split()
    brief["diff_stat"] = (_run(["git", "diff", "--stat", span]) or "").rstrip()
    return brief


def assemble(brief):
    brief["rules"] = rules_for_paths(brief["changed"])
    brief["skills"] = skills_for_paths(brief["changed"])
    brief["memory"] = memory_lines(brief["number"]) if brief["number"] else []
    return brief


def report(brief, out=sys.stdout):
    pr = brief.get("pr")
    if brief.get("number"):
        if pr is None:
            print("PR: gh is not available here; the PR half is left out.\n", file=out)
        else:
            flag = " (draft)" if pr["isDraft"] else ""
            print(f"PR #{brief['number']}{flag}: {pr['title']}", file=out)
            print(f"  {pr['headRefName']} -> {pr['baseRefName']}, +{pr['additions']} "
                  f"-{pr['deletions']}, {len(pr['commits'])} commits, {pr['url']}\n", file=out)
            print("Commits:", file=out)
            for c in pr["commits"]:
                print(f"  {c['sha']} {c['title']}", file=out)
            print("\nChecks:", file=out)
            for c in brief["checks"] or [{"name": "(none reported)", "status": ""}]:
                print(f"  {c['status']:8} {c['name']}", file=out)
            print("\nBody:\n" + (pr["body"] or "(empty)").rstrip() + "\n", file=out)
            print("Diff stat:\n" + brief["diff_stat"] + "\n", file=out)
    print("Rules to Read (.claude/rules/):", file=out)
    for rule, paths in brief["rules"].items() or {"(none)": []}.items():
        print(f"  {rule}", file=out)
        for p in paths:
            print(f"      {p}", file=out)
    print("\nSkills to load:", file=out)
    for s in brief["skills"]:
        print(f"  {s}", file=out)
    if brief["memory"]:
        print("\nMemory index lines naming this PR:", file=out)
        for line in brief["memory"]:
            print(f"  {line}", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("number", nargs="?", type=int, help="the pull request number")
    ap.add_argument("--paths", nargs="+", metavar="PATH",
                    help="map these changed paths to rules and skills; no gh, no git")
    ap.add_argument("--json", action="store_true", help="print the brief as JSON")
    args = ap.parse_args(argv)
    if args.paths:
        brief = {"number": args.number or 0, "pr": None, "checks": [],
                 "changed": args.paths, "diff_stat": ""}
    elif args.number:
        brief = pr_brief(args.number)
    else:
        ap.error("a PR number or --paths is required")
    brief = assemble(brief)
    if args.json:
        print(json.dumps(brief, indent=2))
    else:
        report(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
