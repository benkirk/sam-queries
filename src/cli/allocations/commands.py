"""Allocation command classes."""

from datetime import datetime
from cli.core.base import BaseAllocationCommand
from cli.core.utils import EXIT_SUCCESS, EXIT_ERROR
from cli.allocations.display import display_allocation_summary, parse_comma_list
from sam.queries.allocations import get_allocation_summary, get_allocation_summary_with_usage


class AllocationSearchCommand(BaseAllocationCommand):
    """Query allocation summaries with flexible grouping and filtering."""

    def execute(self, resource=None, facility=None, allocation_type=None,
                project=None, total_resources=False, total_facilities=False,
                total_types=False, total_projects=False, active_at=None,
                inactive=False, show_usage=False) -> int:
        try:
            # Handle --total-* flags by converting to "TOTAL" string
            if total_resources:
                resource = "TOTAL"
            if total_facilities:
                facility = "TOTAL"
            if total_types:
                allocation_type = "TOTAL"
            if total_projects:
                project = "TOTAL"

            # Parse comma-separated values
            resource = parse_comma_list(resource)
            facility = parse_comma_list(facility)
            allocation_type = parse_comma_list(allocation_type)
            project = parse_comma_list(project)

            # Parse active_at date if provided
            active_at_date = None
            if active_at:
                try:
                    active_at_date = datetime.strptime(active_at, "%Y-%m-%d")
                except ValueError:
                    self.console.print(f"❌ Invalid date format: {active_at}. Use YYYY-MM-DD", style="bold red")
                    return EXIT_ERROR

            # Query allocations
            if show_usage:
                results = get_allocation_summary_with_usage(
                    self.session,
                    resource_name=resource,
                    facility_name=facility,
                    allocation_type=allocation_type,
                    projcode=project,
                    active_only=not inactive,
                    active_at=active_at_date
                )
            else:
                results = get_allocation_summary(
                    self.session,
                    resource_name=resource,
                    facility_name=facility,
                    allocation_type=allocation_type,
                    projcode=project,
                    active_only=not inactive,
                    active_at=active_at_date
                )

            if not results:
                self.console.print("No allocations found matching criteria.", style="yellow")
                return EXIT_SUCCESS

            # Display results
            display_allocation_summary(self.ctx, results, show_usage=show_usage)
            return EXIT_SUCCESS

        except Exception as e:
            return self.handle_exception(e)
