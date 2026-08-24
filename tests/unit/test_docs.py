"""Checks that keep the prose honest.

Nothing here reads for meaning; that is a human's job. These catch the
mechanical ways prose goes stale: a doc names a path and the file moves, a
link points at a reworded heading, a British spelling lands in an
American-spelled tree, a comment describes what the code replaced.

Standard library only, and the corpus is `git ls-files` -- never a directory
walk -- so scratch files cannot fail the suite. Each carve-out is documented
where it is defined; `prose_lines` is the one to read first. Ported from
hpc-scheduling-tools; see `docs/plans/DOC_SLIMMING.md` for what was retuned.
"""
import ast
import functools
import io
import os
import re
import subprocess
import tokenize
import urllib.parse
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLANS_DIR = "docs/plans/"

# Point-in-time records. A doc here describes the tree as it was.
RECORD_PREFIXES = (PLANS_DIR, "docs/nrit-review-", "docs/presentations/")

# Records that live outside those trees. The reimplementation doc is both the
# port plan and the reference for the legacy Java system, so it names Maven
# paths that are not in this repo and template names it intended to create.
RECORD_FILES = {"docs/xras/incoming/XRAS_REIMPLEMENTATION.md"}


def is_record(rel):
    return (rel.startswith(RECORD_PREFIXES) or "/implemented/" in rel
            or rel in RECORD_FILES)


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          cwd=str(REPO_ROOT))


@functools.lru_cache(maxsize=None)
def tracked_files():
    """Every path git tracks, as repo-relative strings."""
    result = _git("ls-files", "-z")
    if result.returncode != 0:
        pytest.skip("not a git checkout; the doc checks read `git ls-files`")
    # Symlinks are skipped: GEMINI.md points at CLAUDE.md, and counting it
    # would report -- and demand a fix for -- every finding twice.
    return tuple(f for f in result.stdout.split("\0")
                 if f and not (REPO_ROOT / f).is_symlink())


def is_tracked_path(rel):
    """A tracked file, or a directory containing tracked files."""
    rel = rel.rstrip("/")
    tracked = tracked_files()
    return rel in tracked or any(t.startswith(rel + "/") for t in tracked)


@functools.lru_cache(maxsize=None)
def _ignored_prefix(prefix):
    # --no-index: answer from .gitignore alone, so a path absent from this
    # checkout is judged the same way everywhere.
    return _git("check-ignore", "-q", "--no-index", prefix).returncode == 0


def is_ignored_path(rel):
    """Git-ignored, judged component by component from the top.

    Walking down means an ignored directory answers for everything beneath
    it, so a path through a symlink is never handed to git (which refuses
    those), and `conda-env` is judged as `conda-env/`, which is what
    .gitignore names.
    """
    parts = Path(rel).parts
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if i < len(parts) or rel.endswith("/"):
            prefix += "/"
        if _ignored_prefix(prefix) or _ignored_prefix(prefix.rstrip("/")):
            return True
    return False


def doc_files():
    """The markdown files under test."""
    return [f for f in tracked_files()
            if f.endswith(".md") and not is_record(f)]


FENCE = re.compile(r"^\s*(```|~~~)")


@functools.lru_cache(maxsize=None)
def unfenced_lines(path):
    """The file's lines with fenced code blanked -- not dropped, so the index
    into this list is the line number in the file."""
    out, fenced = [], False
    for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else line)
    return tuple(out)


def numbered_lines(path):
    return enumerate(unfenced_lines(path), 1)


# ---------------------------------------------------------------------------
# Prose extraction -- what the spelling and phrasing checks are allowed to see
# ---------------------------------------------------------------------------
#
# WARNING: never widen this to whole files. Matching a string literal would
# make a spelling rule demand a source change, and matching an identifier
# would make it demand a rename. Both are out of scope for a prose rule.
# `mark_panel_authorised` in sam/xras/handlers/ is the standing example.

PROSE_SUFFIXES = (".md", ".py", ".yaml", ".yml", ".sh", ".js", ".html",
                  ".ini", ".cfg", ".toml", ".example", ".env")

_HASH_COMMENT = re.compile(r"^\s*#")
INLINE_CODE = re.compile(r"`[^`]*`")
_JS_LINE = re.compile(r"//(.*)$")
_BLOCK = re.compile(r"/\*.*?\*/|<!--.*?-->|\{#.*?#\}", re.S)


def _python_prose(text):
    """Line numbers of comment-only lines and docstring spans."""
    lines = text.splitlines()
    keep = set()
    try:
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                keep.update(range(body[0].lineno, body[0].end_lineno + 1))
    except SyntaxError:
        pass
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                keep.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return [(n, line) for n, line in enumerate(lines, 1) if n in keep]


def _markup_prose(text, line_comments):
    """Line numbers inside `{# #}`, `<!-- -->`, `/* */`, or after `//`.

    Block comments are blanked in place so line numbers survive, then every
    surviving line that still holds comment text is reported. `//` counts
    only in JavaScript: in HTML it is far more likely to be the middle of a
    URL, and treating that as prose would hand a spelling rule a hostname.
    """
    blanked = _BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    original = text.splitlines()
    stripped = blanked.splitlines()
    out = []
    for n, line in enumerate(original, 1):
        was_block = n > len(stripped) or stripped[n - 1] != line
        if was_block:
            out.append((n, line))
        elif line_comments:
            m = _JS_LINE.search(line)
            if m:
                out.append((n, m.group(1)))
    return out


@functools.lru_cache(maxsize=None)
def prose_lines(path):
    """(line number, prose text) for every line a prose rule may judge."""
    suffix = Path(path).suffix
    try:
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return ()
    if suffix == ".md":
        # Inline code spans are blanked, for the same reason fenced blocks
        # are: `colour` in backticks is a symbol, and a doc that states this
        # rule has to be able to quote the phrasing it bans. Blanked in
        # place, so line numbers still hold. `unfenced_lines` keeps its
        # backticks -- `test_cited_paths_exist` reads them.
        return tuple((n, INLINE_CODE.sub("", line))
                     for n, line in numbered_lines(path))
    if suffix == ".py":
        return tuple(_python_prose(text))
    if suffix in (".js", ".html"):
        return tuple(_markup_prose(text, line_comments=suffix == ".js"))
    # YAML, shell, ini, .env: `#` to end of line, and only when the `#`
    # starts the line. A trailing `# comment` on a setting is prose too, but
    # matching it means matching the value beside it, which is code.
    return tuple((n, line) for n, line in enumerate(text.splitlines(), 1)
                 if _HASH_COMMENT.match(line))


# Third-party code: not ours to edit, and the minified bundles are pinned by
# sha384 in `webapp/vendor_assets.py`, so rewriting a byte fails
# `test_vendor_assets.py` -- which is how this exclusion was found. The `.md`
# in that directory is ours and stays under the gate, which is why this
# excludes vendored *code* rather than the whole path.
VENDORED = ("src/webapp/static/vendor/",)


def is_vendored(rel):
    return rel.startswith(VENDORED) and not rel.endswith(".md")


def prose_corpus():
    for f in tracked_files():
        if is_vendored(f) or is_record(f) or f in PROSE_EXEMPT:
            continue
        if f.endswith(PROSE_SUFFIXES):
            yield f


# This file names the words it bans and the phrases it bans.
PROSE_EXEMPT = {"tests/unit/test_docs.py"}


# ---------------------------------------------------------------------------
# Markdown links
# ---------------------------------------------------------------------------

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)"   # [text](target)
                  r"|^\s*\[[^\]]+\]:\s*(\S+)", re.M)             # [ref]: target
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


def slug(heading):
    """A heading's anchor, the way GitHub derives it."""
    heading = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"\s", "-", heading.strip())


@functools.lru_cache(maxsize=None)
def headings(path):
    return frozenset(slug(m.group(1))
                     for line in unfenced_lines(path)
                     if (m := HEADING.match(line)))


def link_targets(path):
    """(line, raw target, repo-relative resolved path, anchor) per link."""
    base = os.path.dirname(path)
    for n, line in numbered_lines(path):
        for m in LINK.finditer(line):
            raw = m.group(1) or m.group(2)
            if SCHEME.match(raw):
                continue
            target, _, anchor = raw.partition("#")
            target = urllib.parse.unquote(target)
            resolved = (os.path.normpath(os.path.join(base, target))
                        if target else path)
            yield n, raw, resolved, anchor


def test_markdown_links_resolve():
    broken = []
    for doc in doc_files():
        for n, raw, target, anchor in link_targets(doc):
            if not (REPO_ROOT / target).exists():
                broken.append("%s:%d: broken link %r -> %s does not exist"
                              % (doc, n, raw, target))
            elif anchor and target.endswith(".md") and \
                    anchor not in headings(target):
                broken.append("%s:%d: broken anchor %r -- no heading #%s in %s"
                              % (doc, n, raw, anchor, target))
    assert not broken, "\n".join(broken)


# ---------------------------------------------------------------------------
# Back-ticked paths
# ---------------------------------------------------------------------------

TICKED = re.compile(r"`([^`]+)`")
PATHISH = re.compile(r"^[\w.@+-][\w./@+-]*$")
LINE_REF = re.compile(r":\d+(?:-\d+)?$")

# Cited in prose, deliberately untracked, and not matched by .gitignore.
UNTRACKED_BUT_REAL = {".claude/"}

# The docs cite modules the way an import reads them -- `webapp/utils/rbac.py`,
# `templates/dashboards/allocations/xras.html` -- not from the repo root. So a
# citation is accepted when it matches the TAIL of a tracked path. That is a
# weaker claim than an exact match, deliberately: it still catches the failure
# this check exists for (a file that was renamed or deleted, like
# `sam/core/user.py`) without demanding that every doc spell out `src/`.

# Paths this repo documents but does not contain: sibling checkouts, the
# legacy Java tree the XRAS docs describe, and secret-store keys.
EXTERNAL_PREFIXES = ("hpc-usage-queries/", "src/main/", "csg/")

# `sam.ucar.edu/api/...`, `prod-staticweb14/15.ucar.edu` -- a hostname with a
# path is a URL somebody wrote without the scheme, not a repo path.
HOSTNAME = re.compile(r"\.(?:edu|com|org|gov|net|io|ucar)\b")
EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,12}$")


def cited_path(token):
    """The repo path a back-ticked token claims to name, or None.

    Only tokens that *look* like paths are judged: they contain a slash and
    either an extension, a trailing slash, a `Makefile`, or a tracked
    top-level directory as their first component. So `N/A`, `mm/dd`,
    `github.com/...` and `<machine>/...` stay prose, while `docs/X.md`,
    `tests/factories/` and `helm/values.yaml` are claims and get checked.
    `::name` and `:NN` suffixes are stripped first.
    """
    token = token.strip()
    if not token or any(c.isspace() for c in token):
        return None
    token = re.sub(r"::.*$", "", token)
    token = LINE_REF.sub("", token)
    if token.startswith(".../"):      # an elided prefix, written as prose
        token = token[4:]
    if token.startswith("./"):
        token = token[2:]
    if "/" not in token or token.startswith(("/", "~", "..")) \
            or SCHEME.match(token):
        return None
    if not PATHISH.match(token):
        return None
    first, _, _ = token.partition("/")
    last = token.rstrip("/").rsplit("/", 1)[-1]
    if not (token.endswith("/") or EXTENSION.search(last) or last == "Makefile"
            or is_tracked_path(first)):
        return None
    return token


@functools.lru_cache(maxsize=None)
def _matches_tracked_tail(token):
    """A tracked path is this token, or ends with it at a segment boundary."""
    bare = token.rstrip("/")
    for tracked in tracked_files():
        if tracked == bare or tracked.endswith("/" + bare):
            return True
        if token.endswith("/") and ("/" + bare + "/") in ("/" + tracked):
            return True
    return False


def path_is_accounted_for(doc, token):
    if token.startswith(EXTERNAL_PREFIXES) or HOSTNAME.search(token):
        return True
    base = os.path.dirname(doc)
    candidates = {os.path.normpath(token),
                  os.path.normpath(os.path.join(base, token))}
    for cand in candidates:
        if cand.startswith(".."):
            continue
        cand_dir = cand + "/" if token.endswith("/") else cand
        if (is_tracked_path(cand) or is_ignored_path(cand_dir)
                or cand_dir in UNTRACKED_BUT_REAL):
            return True
    return _matches_tracked_tail(token)


# Paths named precisely BECAUSE they do not exist: a location something
# will move to, an environment not yet created, a design that was rejected,
# a spelling being corrected. Keyed by (doc, token) so the exemption cannot
# quietly cover a second, real breakage in the same file.
CITED_PATH_EXEMPT = {
    ("migrations/README.md", "migrations/sam/"),            # "will land at"
    ("README.md", "migrations/sam/"),                       # "a future ..."
    ("docs/AUTHENTICATION.md", "infrastructure/production/"),  # "before it exists"
    ("src/querykit/README.md", "sam/queries/faceted.py"),   # the rejected edge
    ("docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md",
     "incoming_2026-08-11/"),                               # ~/xras_payloads_raw/
}


def test_cited_paths_exist():
    """A back-ticked path names a tracked file, a tracked directory, or a
    git-ignored path the docs legitimately talk about."""
    missing = []
    for doc in doc_files():
        for n, line in numbered_lines(doc):
            for m in TICKED.finditer(line):
                token = cited_path(m.group(1))
                if (doc, m.group(1).strip()) in CITED_PATH_EXEMPT:
                    continue
                if token and not path_is_accounted_for(doc, token):
                    missing.append("%s:%d: `%s` names nothing in the repo"
                                   % (doc, n, m.group(1)))
    assert not missing, "\n".join(missing)


# ---------------------------------------------------------------------------
# Spelling
# ---------------------------------------------------------------------------
#
# Retuned from the upstream list. `analys` is deliberately absent: "analysis"
# is correct American English, and adding the stem false-positives
# `_data_analysis_strategy` in sam/xras/extractors.py.

_ISE = r"(?:e|ed|es|ing|ation|ations|er|ers)"
BRITISH = re.compile(
    r"(?<![A-Za-z])(?:"
    r"modell(?:ed|ing)|labell(?:ed|ing)|cancell(?:ed|ing)|travell(?:ed|ing)"
    r"|signall(?:ed|ing)|totall(?:ed|ing)|fuell(?:ed|ing)|levell(?:ed|ing)"
    r"|artefacts?|behaviours?|honour(?:s|ed|ing|able)?|colours?|coloured"
    r"|favour(?:s|ed|ing|ite|able)?|labour(?:s|ed|ing)?|neighbour(?:s|ing|hood)?"
    r"|centres?|licence|catalogues?|programmes?|judgement|defence|offence"
    r"|whilst|amongst|learnt|fulfil|grey|enquiry|sceptic(?:al|ism)?|mould"
    r"|aluminium|cheque|draught|manoeuvre|counsellor|enrolment|instil|distil"
    r"|skilful|spelt|spilt|burnt|dreamt|leapt"
    r"|(?:normalis|organis|initialis|serialis|optimis|recognis|summaris|minimis"
    r"|maximis|prioritis|customis|standardis|synchronis|utilis|visualis|realis"
    r"|characteris|emphasis|capitalis|centralis|generalis|specialis|sanitis"
    r"|authoris|memoris|finalis|parametris|randomis|tokenis|categoris"
    r"|apologis|familiaris|legitimis|marginalis|neutralis|stabilis)" + _ISE +
    r")(?![A-Za-z])",
    re.IGNORECASE)


def test_prose_uses_american_spelling():
    """Prose only -- comments, docstrings, markdown. Identifiers and string
    literals are never read, so this rule can never demand a rename."""
    hits = []
    for f in prose_corpus():
        for n, line in prose_lines(f):
            for m in BRITISH.finditer(line):
                hits.append("%s:%d: %r" % (f, n, m.group(0)))
    assert not hits, ("British spellings (this repo is American-spelled):\n"
                      + "\n".join(hits))


# ---------------------------------------------------------------------------
# Changelog phrasing
# ---------------------------------------------------------------------------
#
# Retuned from the upstream list, which bans `coexist`. This repo uses that
# word 18 times and 17 are ordinary present tense -- "a wedged run cannot
# coexist with its successor", "two conventions coexist deliberately". A
# phrase that has a legitimate use gets dropped from this list, not exempted
# per line. `no longer`, `previously` and `historically` are absent for the
# same reason.

# The verbs after "used to" are ENUMERATED, never `\\w+`. "used to <verb>" is
# also the purpose sense ("a helper used to build the query"), and the open form
# matches 77 lines here of which nearly all are that. Every verb below was added
# only after measuring zero false positives across the corpus.
CHANGELOG_PHRASES = re.compile(
    r"successor to|the old behavio(?:u)?r|\bformerly\b"
    r"|\bused to (?:be|live|call|read|pass|sit|hold|do|get"
    r"|default|return|come|point|carry|take|say|fire|match|include|mean"
    r"|import|store|accept|emit|render|raise|require|treat|walk|write|set)\b"
    r"|\bwe used to\b|\brenamed from\b|\bin the old\b"
    r"|\bbefore PR ?#?\d+|\bas of PR\b"
    r"|this (?:comment|docstring) (?:originally|previously)"
    # A comment arguing with its own earlier revision. Four sites in the
    # doc-slimming sprint carried this and none matched the rules above.
    r"|\b(?:an?|the) (?:earlier|previous|prior) "
    r"(?:version|revision|iteration|spelling|implementation)\b"
    r"|\b(?:it|this|that) was (?:originally|previously|formerly|once|``)"
    r"|\bwe (?:now|once)\b"
    r"|\bbefore (?:this|that) (?:change|commit|PR|fix|refactor)\b",
    re.IGNORECASE)

PHRASING_EXEMPT = {
    # Each documents a deliberately INVERTED assertion, where the state it is
    # inverted from is the entire point of the comment.
    "tests/stress/test_parking_is_explained.py",
    "tests/unit/test_task_runner.py",
}


def test_prose_avoids_changelog_phrasing():
    """Prose describes what the code does, not what it replaced. Repository
    history belongs in git and docs/plans/."""
    hits = []
    for f in prose_corpus():
        if f in PHRASING_EXEMPT:
            continue
        for n, line in prose_lines(f):
            for m in CHANGELOG_PHRASES.finditer(line):
                hits.append("%s:%d: %r" % (f, n, m.group(0)))
    assert not hits, "changelog phrasing:\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# Length
# ---------------------------------------------------------------------------
#
# A ratchet against sprawl, not a tape measure. Budgets are seeded at each
# doc's size when the check landed, so nothing had to be rewritten to adopt
# it and only growth fails. Lower a budget when you shorten a doc; raise one
# only deliberately, in this table, and say why.

DEFAULT_LINE_BUDGET = 250
LINE_BUDGETS = {
    "CLAUDE.md": 1060,
    "CONTRIBUTING.md": 740,
    "README.md": 1070,
    "collectors/README.md": 370,
    "containers/sam-sql-dev/ANONYMIZATION_PROCESS.md": 1070,
    "containers/sam-sql-dev/DOCKER_COMPOSE_CI.md": 380,
    "docs/AUTHENTICATION.md": 660,
    "docs/GETTING_STARTED.md": 1150,
    "docs/LOCAL_SETUP.md": 390,
    "docs/README-k8s.md": 430,
    "docs/SCRIPTS.md": 490,
    "docs/TESTING.md": 490,
    "docs/apis/CHARGING_INTEGRATION.md": 520,
    "docs/apis/HPC_DATA_COLLECTORS_GUIDE.md": 950,
    "docs/apis/SYSTEMS_INTEGRATION_APIs.md": 960,
    "docs/xras/incoming/XRAS_CUTOVER_RUNBOOK.md": 710,
    "docs/xras/incoming/XRAS_TRIAGE_PLAYBOOK.md": 490,
    "docs/xras/outgoing/XRAS_OPPORTUNITY_ALLOCATION_TYPE.md": 630,
    "docs/xras/outgoing/XRAS_OUTGOING_QUERIES.md": 1100,
    "docs/xras/outgoing/XRAS_WRITE_FIXUPS.md": 460,
    "docs/xras/outgoing/XRAS_WRITE_PROBES.md": 440,
    "scripts/repair/RESTORED-2026-07-24.md": 290,
    "scripts/repair/RUNBOOK-missing-projects.md": 310,
    "src/webapp/README.md": 710,
}


def test_docs_stay_within_length_budget():
    over = []
    for doc in doc_files():
        n = len((REPO_ROOT / doc).read_text(encoding="utf-8").splitlines())
        budget = LINE_BUDGETS.get(doc, DEFAULT_LINE_BUDGET)
        if n > budget:
            over.append("%s: %d lines, budget %d" % (doc, n, budget))
    assert not over, ("over budget -- split it, or move the detail into a "
                      "docs/plans/ record and link it, rather than raising "
                      "the number:\n" + "\n".join(over))
