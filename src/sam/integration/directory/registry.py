"""Reader name -> class, and the ``DIRECTORY_PROVIDER`` / ``DIRECTORY_URL`` selector.

A registered reader is constructed from a ``DirectoryConfig``.

Imports ``ldap3`` via ``ucar``, so callers import this module by path, never through
``sam.integration.directory``.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Type

from sam.integration.directory.base import (DirectoryNotConfigured, DirectoryReader,
                                            NullDirectory)
from sam.integration.directory.config import DirectoryConfig
from sam.integration.directory.ucar import UCARDirectory

READERS: Dict[str, Type[DirectoryReader]] = {UCARDirectory.name: UCARDirectory}

OFF_NAMES: FrozenSet[str] = frozenset({'', 'none', 'off'})


def reader_names() -> list:
    return sorted(READERS)


def build_reader(name: str, config: Optional[DirectoryConfig] = None) -> DirectoryReader:
    """The named reader, or the null reader for an off name or an empty URL.

    Raises:
        DirectoryNotConfigured: an unknown name, listing the valid ones.
    """
    key = (name or '').strip().lower()
    if key in OFF_NAMES:
        return NullDirectory()
    cls = READERS.get(key)
    if cls is None:
        raise DirectoryNotConfigured(
            f'unknown DIRECTORY_PROVIDER {name!r}; expected one of '
            f'{", ".join(sorted(OFF_NAMES | set(READERS)))}')
    config = config or DirectoryConfig.from_environment()
    if not config.url:
        return NullDirectory()
    return cls(config)


def reader_from_environment() -> DirectoryReader:
    """``DIRECTORY_PROVIDER`` built against the environment's settings."""
    config = DirectoryConfig.from_environment()
    return build_reader(config.provider, config)
