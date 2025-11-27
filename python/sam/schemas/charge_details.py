"""
Detailed charge record schemas for API serialization.

Provides schemas for individual charge records (HPC, DAV, Disk, Archive)
joined with activity and user data. These are used for detailed charge
breakdowns in the charges/details endpoint.

Usage:
    from sam.schemas import HPCChargeDetailSchema, DiskChargeDetailSchema

    # Serialize charge records with activity data
    hpc_data = HPCChargeDetailSchema(many=True).dump(charge_tuples)
"""

from marshmallow import Schema, fields
from .user import UserSummarySchema


class ChargeDetailBaseSchema(Schema):
    """
    Base schema for charge detail serialization.

    Expects a tuple: (charge, activity, user)

    Subclasses should define:
    - charge_type: String constant for the charge type
    - get_comment(): Method to generate the comment from activity
    """

    date = fields.Method('get_date')
    type = fields.Method('get_type')
    comment = fields.Method('get_comment')
    user = fields.Method('get_user')
    amount = fields.Method('get_amount')

    # Override in subclass
    charge_type = 'Unknown'

    def get_date(self, obj):
        """Extract activity date from tuple."""
        _, activity, _ = obj
        return activity.activity_date.strftime('%Y-%m-%d') if activity.activity_date else 'N/A'

    def get_type(self, obj):
        """Return the charge type constant."""
        return self.charge_type

    def get_comment(self, obj):
        """Extract comment from activity - override in subclass."""
        return '-'

    def get_user(self, obj):
        """Extract username from tuple."""
        _, _, user = obj
        return user.username if user else '-'

    def get_amount(self, obj):
        """Extract charge amount from tuple."""
        charge, _, _ = obj
        return float(charge.charge) if charge.charge else 0.0


class HPCChargeDetailSchema(ChargeDetailBaseSchema):
    """
    Schema for HPC charge details with activity and user information.

    Expects a tuple: (hpc_charge, hpc_activity, user)
    """
    charge_type = 'HPC Compute'

    def get_comment(self, obj):
        """Extract job ID comment from tuple."""
        _, hpc_activity, _ = obj
        return f'Job {hpc_activity.job_id}' if hpc_activity.job_id else '-'


class DavChargeDetailSchema(ChargeDetailBaseSchema):
    """
    Schema for DAV charge details with activity and user information.

    Expects a tuple: (dav_charge, dav_activity, user)
    """
    charge_type = 'DAV'

    def get_comment(self, obj):
        """Extract session ID comment from tuple."""
        _, dav_activity, _ = obj
        return f'Session {dav_activity.session_id}' if dav_activity.session_id else '-'


class DiskChargeDetailSchema(ChargeDetailBaseSchema):
    """
    Schema for Disk charge details with activity and user information.

    Expects a tuple: (disk_charge, disk_activity, user)
    """
    charge_type = 'Disk Storage'

    def get_comment(self, obj):
        """Extract volume comment from tuple."""
        _, disk_activity, _ = obj
        return f'{disk_activity.volume_gb} GB' if disk_activity.volume_gb else '-'


class ArchiveChargeDetailSchema(ChargeDetailBaseSchema):
    """
    Schema for Archive charge details with activity and user information.

    Expects a tuple: (archive_charge, archive_activity, user)
    """
    charge_type = 'Archive'

    def get_comment(self, obj):
        """Extract volume comment from tuple."""
        _, archive_activity, _ = obj
        return f'{archive_activity.volume_gb} GB' if archive_activity.volume_gb else '-'
