"""
Job schemas for API serialization.

Provides schemas for computational job records (CompJob) with proper
datetime handling and computed fields.

Usage:
    from sam.schemas import CompJobSchema

    # Serialize job records
    jobs_data = CompJobSchema(many=True).dump(jobs)
"""

from marshmallow import fields
from datetime import datetime
from . import BaseSchema
from sam.activity.computational import CompJob


class CompJobSchema(BaseSchema):
    """
    Schema for computational job records.

    Serializes CompJob model with Unix timestamp conversion to ISO datetime
    strings and computed properties (wall_time_hours, queue_wait_hours, is_successful).
    """
    class Meta(BaseSchema.Meta):
        model = CompJob
        fields = (
            'job_id',
            'job_idx',
            'username',
            'machine',
            'queue',
            'resource',
            'projcode',
            'job_name',
            'activity_date',
            'submit_time',
            'start_time',
            'end_time',
            'wall_time_hours',
            'queue_wait_hours',
            'exit_status',
            'is_successful',
            'interactive',
        )

    # Convert Unix timestamps to ISO datetime strings
    submit_time = fields.Method('get_submit_time')
    start_time = fields.Method('get_start_time')
    end_time = fields.Method('get_end_time')

    # Computed properties from model
    wall_time_hours = fields.Method('get_wall_time_hours')
    queue_wait_hours = fields.Method('get_queue_wait_hours')
    is_successful = fields.Method('get_is_successful')
    interactive = fields.Method('get_interactive')

    def get_submit_time(self, obj):
        """Convert Unix timestamp to ISO datetime string."""
        if obj.submit_time:
            return datetime.fromtimestamp(obj.submit_time).isoformat()
        return None

    def get_start_time(self, obj):
        """Convert Unix timestamp to ISO datetime string."""
        if obj.start_time:
            return datetime.fromtimestamp(obj.start_time).isoformat()
        return None

    def get_end_time(self, obj):
        """Convert Unix timestamp to ISO datetime string."""
        if obj.end_time:
            return datetime.fromtimestamp(obj.end_time).isoformat()
        return None

    def get_wall_time_hours(self, obj):
        """Get wall clock time in hours."""
        return obj.wall_time_hours

    def get_queue_wait_hours(self, obj):
        """Get queue wait time in hours."""
        return obj.queue_wait_time_seconds / 3600.0 if obj.queue_wait_time_seconds else 0.0

    def get_is_successful(self, obj):
        """Check if job completed successfully."""
        return obj.is_successful

    def get_interactive(self, obj):
        """Check if job was interactive."""
        return bool(obj.interactive)
