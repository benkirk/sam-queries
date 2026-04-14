"""
Domain enums for SAM.

Centralizes string constants that appear in many modules so they can be
referenced by name rather than typed by hand. All members are StrEnum, so
each value remains a real string — equality with bare string literals
(e.g. ``rt == 'HPC'`` or ``rt == ResourceTypeName.HPC``) works in both
directions, and ORM comparisons against these enums do not need explicit
``.value``.

These values must match the strings actually stored in the database.

Usage:
    from sam.enums import ResourceTypeName, FacilityName, ChargeType

    if resource.resource_type.resource_type == ResourceTypeName.HPC:
        ...

    facility_names = [FacilityName.UNIV, FacilityName.WNA]
"""

from enum import StrEnum


class ResourceTypeName(StrEnum):
    """Values stored in `resource_type.resource_type`."""
    HPC = 'HPC'
    DAV = 'DAV'
    DISK = 'DISK'
    ARCHIVE = 'ARCHIVE'
    DATA_ACCESS = 'DATA ACCESS'

    @classmethod
    def is_compute(cls, name: str) -> bool:
        """Return True for resource types charged via comp/dav summaries."""
        return name in (cls.HPC, cls.DAV)


class FacilityName(StrEnum):
    """Common facility names. Not exhaustive — additional facilities exist
    in the database; these are the ones referenced from code."""
    UNIV = 'UNIV'
    WNA = 'WNA'
    NCAR = 'NCAR'

    @classmethod
    def default_user_facing(cls) -> list['FacilityName']:
        """Default facilities used by user-facing CLI commands and queries."""
        return [cls.UNIV, cls.WNA]


class ChargeType(StrEnum):
    """Per-resource-type charge bucket names returned by allocation usage
    breakdowns (``Project.get_detailed_allocation_usage``)."""
    COMP = 'comp'
    DAV = 'dav'
    DISK = 'disk'
    ARCHIVE = 'archive'
