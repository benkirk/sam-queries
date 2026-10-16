"""The directory settings, read per call from Flask config or the environment.

``DIRECTORY_URL`` empty means off, whatever the provider says. ``DIRECTORY_APPLY_<KIND>``
is the trust stage for that kind: with it off, that kind is reported and never written,
whichever path (sync hook, admin panel, CLI) asks for a reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Optional

from sam.integration._config import config_bool, config_float, config_str
from sam.integration.directory.base import KINDS

DEFAULT_BASE_DN = 'dc=ucar,dc=edu'
DEFAULT_TIMEOUT = 5.0


def apply_lever(kind: str) -> str:
    return f'DIRECTORY_APPLY_{kind.upper()}'


@dataclass(frozen=True)
class DirectoryConfig:
    provider: str = ''
    url: str = ''
    base_dn: str = DEFAULT_BASE_DN
    timeout: float = DEFAULT_TIMEOUT
    check_on_sync: bool = False
    apply_kinds: FrozenSet[str] = frozenset()

    @classmethod
    def from_environment(cls) -> 'DirectoryConfig':
        return cls(
            provider=config_str('DIRECTORY_PROVIDER', '').lower(),
            url=config_str('DIRECTORY_URL', ''),
            base_dn=config_str('DIRECTORY_BASE_DN', DEFAULT_BASE_DN) or DEFAULT_BASE_DN,
            timeout=config_float('DIRECTORY_TIMEOUT_SECONDS', DEFAULT_TIMEOUT),
            check_on_sync=config_bool('DIRECTORY_CHECK_ON_SYNC', False),
            apply_kinds=frozenset(k for k in KINDS if config_bool(apply_lever(k), False)),
        )

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.url)

    def apply_enabled(self, kind: str) -> bool:
        return kind in self.apply_kinds

    def summary(self) -> Dict[str, Any]:
        return {
            'provider': self.provider,
            'url': self.url,
            'base_dn': self.base_dn,
            'timeout': self.timeout,
            'configured': self.configured,
            'check_on_sync': self.check_on_sync,
            'apply': {kind: self.apply_enabled(kind) for kind in KINDS},
        }


def directory_configured(config: Optional[DirectoryConfig] = None) -> bool:
    """The cheap predicate a card or command branches on before building a reader."""
    return (config or DirectoryConfig.from_environment()).configured


def directory_check_on_sync(config: Optional[DirectoryConfig] = None) -> bool:
    return (config or DirectoryConfig.from_environment()).check_on_sync
