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
from pathlib import Path

from markupsafe import Markup

_SOURCE = Path(__file__).with_name('eula.md')
#: Relative `*.md` links in the source point at sibling HPC-Docs pages; map them
#: onto the published site so they resolve from our page.
_RTD_BASE = 'https://ncar-hpc-docs.readthedocs.io/en/latest/getting-started/'
_REL_MD_LINK = re.compile(r'href="(?!https?://|/|#)([^"/]+)\.md"')


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
