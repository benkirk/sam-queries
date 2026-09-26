"""The NWSC end-user agreement shown on the registration gate.

`eula.md` is vendored verbatim from NCAR/HPC-Docs (the same source the public
site renders) and converted here, so an acceptance records exactly the text the
visitor saw. mkdocs is built on python-markdown, so plain markdown reproduces
the site. Refresh with scripts/update_eula.py; never hand-edit eula.md.

Trusted, reviewed input: the source carries no raw HTML, and the refresh diff
is the review gate, so the converted output is injected without escaping.
"""

from __future__ import annotations

import functools
import hashlib
import re
import textwrap
from pathlib import Path

from markupsafe import Markup

_SOURCE = Path(__file__).with_name('eula.md')
#: Relative `*.md` links in the source point at sibling HPC-Docs pages; map them
#: onto the published site so they resolve from our page.
_RTD_BASE = 'https://ncar-hpc-docs.readthedocs.io/en/latest/getting-started/'
_REL_MD_LINK = re.compile(r'href="(?!https?://|/|#)([^"/]+)\.md"')
_MD_LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_WRAP_WIDTH = 76


@functools.lru_cache(maxsize=1)
def eula_html() -> Markup:
    """The rendered agreement, cached (the vendored text changes only on deploy)."""
    import markdown
    html = markdown.markdown(_SOURCE.read_text(encoding='utf-8'),
                             extensions=['sane_lists'], output_format='html5')
    html = _REL_MD_LINK.sub(lambda m: f'href="{_RTD_BASE}{m.group(1)}/"', html)
    return Markup(html)


@functools.lru_cache(maxsize=1)
def eula_sha() -> str:
    """Git blob SHA of the vendored text: what `git hash-object` and update_eula.py print."""
    data = _SOURCE.read_bytes()
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def _link_target(url: str) -> str:
    if url.startswith(('http://', 'https://', '/', '#')):
        return url
    return f'{_RTD_BASE}{url.removesuffix(".md")}/'


def _fill(text: str, **kwargs) -> str:
    # A URL must survive intact: no breaking inside words or at hyphens.
    return textwrap.fill(text, _WRAP_WIDTH, break_long_words=False,
                         break_on_hyphens=False, **kwargs)


def _plain_line(line: str) -> str:
    line = _MD_LINK.sub(lambda m: f'{m.group(1)} ({_link_target(m.group(2))})', line)
    return line.replace('**', '').replace('`', '')


@functools.lru_cache(maxsize=1)
def eula_text() -> str:
    """The agreement as wrapped plain text, for the text part of a mail."""
    out = []
    for raw in _SOURCE.read_text(encoding='utf-8').splitlines():
        line = _plain_line(raw.strip())
        if not line:
            if out and out[-1] != '':
                out.append('')
        elif line.startswith('# '):
            out.append(line[2:].upper())
        elif line.startswith('* '):
            out.append(_fill(line[2:], initial_indent='- ', subsequent_indent='  '))
        else:
            out.append(_fill(line))
    return '\n'.join(out).strip() + '\n'
