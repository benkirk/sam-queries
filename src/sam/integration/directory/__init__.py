"""An optional read-first client for the site's LDAP directory (docs/plans/DIRECTORY_CLIENT.md).

Import-light on purpose: this file exports only ``base``. ``registry`` and ``ucar``
bring in ``ldap3`` and are imported by path.
"""

from sam.integration.directory.base import (
    KINDS, CollaborationRecord, DirectoryNotConfigured, DirectoryReader,
    DirectoryUnavailable, EmailRecord, GroupRecord, InstitutionRecord, NullDirectory,
    OrganizationRecord, PersonRecord, PhoneRecord, PositionRecord,
)

__all__ = [
    'KINDS', 'CollaborationRecord', 'DirectoryNotConfigured', 'DirectoryReader',
    'DirectoryUnavailable', 'EmailRecord', 'GroupRecord', 'InstitutionRecord',
    'NullDirectory', 'OrganizationRecord', 'PersonRecord', 'PhoneRecord', 'PositionRecord',
]
