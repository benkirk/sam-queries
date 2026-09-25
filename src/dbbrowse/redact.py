"""Which columns the browser never selects, filters, or sorts.

Name rules catch secrets by shape (``password``, ``*_hash``, ``*_token``,
``api_key``...); ``explicit`` catches the rest. A redacted column must be
unfilterable too, or ``LIKE '$2b$12$a%'`` would leak it one character at a time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet, Tuple

_ANY_TOKEN = frozenset({'password', 'passwd', 'pwd', 'secret', 'salt'})
_LAST_TOKEN = frozenset({'hash', 'token', 'digest'})
_KEY_PREFIX = frozenset({'api', 'private', 'access', 'secret', 'signing', 'encryption'})

_CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')


def name_tokens(column: str) -> Tuple[str, ...]:
    """``resourceRepositoryKey`` -> ('resource', 'repository', 'key')."""
    return tuple(t for t in re.split(r'[_\W]+', _CAMEL.sub('_', column).lower()) if t)


@dataclass(frozen=True)
class RedactionPolicy:
    explicit: FrozenSet[Tuple[str, str]] = field(default_factory=frozenset)   # (table, column)

    def is_redacted(self, table: str, column: str) -> bool:
        if (table, column) in self.explicit:
            return True
        toks = name_tokens(column)
        if not toks:
            return False
        return (bool(_ANY_TOKEN.intersection(toks))
                or toks[-1] in _LAST_TOKEN
                or (len(toks) >= 2 and toks[-1] == 'key' and toks[-2] in _KEY_PREFIX))


DEFAULT_POLICY = RedactionPolicy(explicit=frozenset({
    ('api_credentials', 'password'),
    ('account_request', 'verify_code_hash'),
}))
