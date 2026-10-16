"""The directory contract: typed records, exceptions, the reader ABC and the null reader.

Three outcomes, as for tickets and XRAS: a record (the directory has it), ``None``
(it does not), an exception (we could not ask). Datetimes are naive Mountain, so
they compare with ORM columns directly. No consumer sees an attribute name: the
adapter owns the schema and returns these records.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, Iterator, List, Mapping, Optional, Tuple

#: The record kinds a directory holds and SAM mirrors. ``DIRECTORY_APPLY_<KIND>``
#: (upper-cased) is each kind's write lever.
KINDS: Tuple[str, ...] = ('person', 'position', 'collaboration', 'organization',
                          'institution', 'group')


class DirectoryUnavailable(Exception):
    """The directory could not be reached, or did not answer usefully."""


class DirectoryNotConfigured(DirectoryUnavailable):
    """No directory is configured; nothing was attempted."""


@dataclass(frozen=True)
class EmailRecord:
    address: str
    primary: bool = False


@dataclass(frozen=True)
class PhoneRecord:
    label: str
    number: str


@dataclass(frozen=True)
class PositionRecord:
    """A job at an internal organization; ``position_id`` is SAM's ``idms_unique_name``."""
    position_id: str
    organization_id: Optional[int] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    active: bool = False
    source: str = ''
    modifier: str = ''


@dataclass(frozen=True)
class CollaborationRecord:
    """One entry per (person, institution), ever; it has no id of its own."""
    institution_id: int
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    active: bool = False
    admin_org: str = ''
    source: str = ''
    modifier: str = ''


@dataclass(frozen=True)
class PersonRecord:
    upid: int
    username: str = ''
    unix_uid: Optional[int] = None
    active: bool = False
    first_name: str = ''
    last_name: str = ''
    middle_name: str = ''
    nickname: str = ''
    name_suffix: str = ''
    emails: Tuple[EmailRecord, ...] = ()
    phones: Tuple[PhoneRecord, ...] = ()
    #: The value as the directory holds it (a code or a label) and its SAM code, if mapped.
    academic_status: str = ''
    academic_status_code: Optional[str] = None
    token_type: str = ''
    source: str = ''
    positions: Tuple[PositionRecord, ...] = ()
    collaborations: Tuple[CollaborationRecord, ...] = ()
    modified: Optional[datetime] = None
    #: Wired, not shown: supervisor, office, GitHub user and the like, by plain name.
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OrganizationRecord:
    organization_id: int
    acronym: str = ''
    name: str = ''
    level: str = ''
    level_code: str = ''
    parent_org_id: Optional[int] = None
    active: bool = False
    source: str = ''


@dataclass(frozen=True)
class InstitutionRecord:
    institution_id: int
    acronym: str = ''
    name: str = ''
    country: str = ''
    state: str = ''
    state_code: str = ''
    city: str = ''
    street: str = ''
    postal_code: str = ''
    nsf_org_code: str = ''
    org_type: str = ''
    source: str = ''


@dataclass(frozen=True)
class GroupRecord:
    gid: int
    name: str = ''
    tags: Tuple[str, ...] = ()
    member_upids: Tuple[int, ...] = ()
    active: bool = False
    manager_gids: Tuple[int, ...] = ()
    source: str = ''
    managers: Tuple[int, ...] = ()


class DirectoryReader(ABC):
    """One directory. Reads answer a record, ``None``, or raise ``DirectoryUnavailable``."""

    #: Registry key; stable.
    name: ClassVar[str] = ''

    @classmethod
    @abstractmethod
    def from_environment(cls) -> 'DirectoryReader':
        """Build from config. Never raises; check :attr:`configured`."""

    @property
    @abstractmethod
    def configured(self) -> bool:
        """Reads may be attempted."""

    @abstractmethod
    def summary(self) -> Dict[str, Any]:
        """Config for the Admin > Configuration card."""

    @abstractmethod
    def person(self, upid: int) -> Optional[PersonRecord]:
        """The person and their positions and collaborations."""

    @abstractmethod
    def organization(self, organization_id: int) -> Optional[OrganizationRecord]:
        ...

    @abstractmethod
    def institution(self, institution_id: int) -> Optional[InstitutionRecord]:
        ...

    @abstractmethod
    def group(self, gid: int) -> Optional[GroupRecord]:
        ...

    @abstractmethod
    def search(self, kind: str, query: str, *, limit: int = 20) -> List[Any]:
        """Records of ``kind`` whose key or name matches ``query``."""

    def check(self) -> Tuple[bool, str]:
        """Probe the directory without side effects, for the card."""
        return True, ''

    @contextmanager
    def session(self) -> Iterator['DirectoryReader']:
        """Reuse one connection across many reads (sweeps); the default opens none."""
        yield self

    def guard_read(self) -> None:
        if not self.configured:
            raise DirectoryNotConfigured(f'{self.name}: no directory is configured')


class NullDirectory(DirectoryReader):
    """The reader when no directory is configured: every read raises, nothing dials out."""

    name = 'none'

    @classmethod
    def from_environment(cls) -> 'NullDirectory':
        return cls()

    @property
    def configured(self) -> bool:
        return False

    def summary(self) -> Dict[str, Any]:
        return {'provider': self.name, 'configured': False}

    def person(self, upid: int) -> Optional[PersonRecord]:
        self.guard_read()
        return None

    def organization(self, organization_id: int) -> Optional[OrganizationRecord]:
        self.guard_read()
        return None

    def institution(self, institution_id: int) -> Optional[InstitutionRecord]:
        self.guard_read()
        return None

    def group(self, gid: int) -> Optional[GroupRecord]:
        self.guard_read()
        return None

    def search(self, kind: str, query: str, *, limit: int = 20) -> List[Any]:
        self.guard_read()
        return []
