"""
Detailed charge record schemas for API serialization.

Provides schemas for individual charge records (HPC, DAV, Disk, Archive)
joined with activity and user data. These are used for detailed charge
breakdowns in the charges/details endpoint.

Usage:
    from webui.schemas import HPCChargeDetailSchema, DiskChargeDetailSchema

    # Serialize charge records with activity data
    hpc_data = HPCChargeDetailSchema(many=True).dump(charge_tuples)
"""

from marshmallow import Schema, fields
from .user import UserSummarySchema


class HPCChargeDetailSchema(Schema):
    """
    Schema for HPC charge details with activity and user information.

    Expects a tuple: (hpc_charge, hpc_activity, user)
    """
    date = fields.Method('get_date')
    type = fields.Constant('HPC Compute')
    comment = fields.Method('get_comment')
    user = fields.Method('get_user')
    amount = fields.Method('get_amount')

    def get_date(self, obj):
        """Extract activity date from tuple."""
        _, hpc_activity, _ = obj
        return hpc_activity.activity_date.strftime('%Y-%m-%d') if hpc_activity.activity_date else 'N/A'

    def get_comment(self, obj):
        """Extract job ID comment from tuple."""
        _, hpc_activity, _ = obj
        return f'Job {hpc_activity.job_id}' if hpc_activity.job_id else '-'

    def get_user(self, obj):
        """Extract username from tuple."""
        _, _, user = obj
        return user.username if user else '-'

    def get_amount(self, obj):
        """Extract charge amount from tuple."""
        hpc_charge, _, _ = obj
        return float(hpc_charge.charge) if hpc_charge.charge else 0.0


class DavChargeDetailSchema(Schema):
    """
    Schema for DAV charge details with activity and user information.

    Expects a tuple: (dav_charge, dav_activity, user)
    """
    date = fields.Method('get_date')
    type = fields.Constant('DAV')
    comment = fields.Method('get_comment')
    user = fields.Method('get_user')
    amount = fields.Method('get_amount')

    def get_date(self, obj):
        """Extract activity date from tuple."""
        _, dav_activity, _ = obj
        return dav_activity.activity_date.strftime('%Y-%m-%d') if dav_activity.activity_date else 'N/A'

    def get_comment(self, obj):
        """Extract session ID comment from tuple."""
        _, dav_activity, _ = obj
        return f'Session {dav_activity.session_id}' if dav_activity.session_id else '-'

    def get_user(self, obj):
        """Extract username from tuple."""
        _, _, user = obj
        return user.username if user else '-'

    def get_amount(self, obj):
        """Extract charge amount from tuple."""
        dav_charge, _, _ = obj
        return float(dav_charge.charge) if dav_charge.charge else 0.0


class DiskChargeDetailSchema(Schema):
    """
    Schema for Disk charge details with activity and user information.

    Expects a tuple: (disk_charge, disk_activity, user)
    """
    date = fields.Method('get_date')
    type = fields.Constant('Disk Storage')
    comment = fields.Method('get_comment')
    user = fields.Method('get_user')
    amount = fields.Method('get_amount')

    def get_date(self, obj):
        """Extract activity date from tuple."""
        _, disk_activity, _ = obj
        return disk_activity.activity_date.strftime('%Y-%m-%d') if disk_activity.activity_date else 'N/A'

    def get_comment(self, obj):
        """Extract volume comment from tuple."""
        _, disk_activity, _ = obj
        return f'{disk_activity.volume_gb} GB' if disk_activity.volume_gb else '-'

    def get_user(self, obj):
        """Extract username from tuple."""
        _, _, user = obj
        return user.username if user else '-'

    def get_amount(self, obj):
        """Extract charge amount from tuple."""
        disk_charge, _, _ = obj
        return float(disk_charge.charge) if disk_charge.charge else 0.0


class ArchiveChargeDetailSchema(Schema):
    """
    Schema for Archive charge details with activity and user information.

    Expects a tuple: (archive_charge, archive_activity, user)
    """
    date = fields.Method('get_date')
    type = fields.Constant('Archive')
    comment = fields.Method('get_comment')
    user = fields.Method('get_user')
    amount = fields.Method('get_amount')

    def get_date(self, obj):
        """Extract activity date from tuple."""
        _, archive_activity, _ = obj
        return archive_activity.activity_date.strftime('%Y-%m-%d') if archive_activity.activity_date else 'N/A'

    def get_comment(self, obj):
        """Extract volume comment from tuple."""
        _, archive_activity, _ = obj
        return f'{archive_activity.volume_gb} GB' if archive_activity.volume_gb else '-'

    def get_user(self, obj):
        """Extract username from tuple."""
        _, _, user = obj
        return user.username if user else '-'

    def get_amount(self, obj):
        """Extract charge amount from tuple."""
        archive_charge, _, _ = obj
        return float(archive_charge.charge) if archive_charge.charge else 0.0
