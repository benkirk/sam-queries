"""`sam-admin accounting --comp`: hpc-usage-queries daily rows into comp_charge_summary."""
import re
from cli.core.utils import EXIT_ERROR, EXIT_SUCCESS
from datetime import date
from typing import Optional

from cli.core.display_utils import progress as progress_bar
from cli.accounting.display import (
    display_dry_run_table,
    display_import_summary,
)
from sam.manage.summaries import (
    upsert_comp_charge_summary,
)
from sam.manage.transaction import management_transaction
from sam.plugins import HPC_USAGE_QUERIES
from sam.summaries.comp_summaries import COMP_CHARGING_EPOCH


# Threshold: GPU hours must be at least this fraction of total compute hours
# to classify a row as a GPU resource charge rather than CPU.
# Avoids misclassifying CPU jobs that happened to touch a GPU queue briefly.
GPU_FRACTION_THRESHOLD = 0.01  # 1%

# PBS assigns ephemeral names like R5184776 (reservation), M2498882 (maintenance),
# S870294 (standing reservation) to individual reservations.  SAM has a single
# canonical 'reservation' queue per resource that covers all of them.
_RESERVATION_QUEUE_RE = re.compile(r'^[RMS]\d')


def normalize_queue_name(queue_name: str) -> str:
    """Map ephemeral PBS reservation queue names to the canonical 'reservation' queue.

    Known limitation: if the same user/project has jobs in two different PBS
    reservations on the same day, both rows normalize to the same SAM natural key
    and the second upsert overwrites the first (charges are not summed).  This
    edge case is considered rare enough not to warrant pre-aggregation today.
    """
    if _RESERVATION_QUEUE_RE.match(queue_name):
        return 'reservation'
    return queue_name


def classify_comp_resource(
    machine: str,
    queue: Optional[str],
    cpu_hours,
    gpu_hours,
    cpu_charges,
    gpu_charges,
) -> tuple:
    """
    Classify a single comp record into SAM resource + billed-charge fields.

    This is the single place where machine-specific billing rules live, shared
    by the daily-summary poster (``adapt_jobstats_row``) and the per-job
    listing (``AccountingJobsCommand``) so both agree on resource name, the
    billed charge, and the billing-metric hours.

    Rules:
      - The ``vis`` queue consumes GPUs but we don't charge for that, so its
        GPU hours/charges are zeroed (keeps it accessible to projects without
        a GPU allocation).
      - A record bills as GPU only when GPU hours are a meaningful fraction
        (``GPU_FRACTION_THRESHOLD``) of total compute hours; otherwise it bills
        as CPU.  This avoids misclassifying a CPU job that briefly touched a
        GPU queue.

    Unlike ``adapt_jobstats_row`` this never skips: callers decide what to do
    with a zero-hours result (the poster skips it; the listing may still show
    it).

    Returns:
        (resource_name, machine_name, comp_hours, charges) where comp_hours is
        the billing metric (GPU hours for a GPU charge, CPU core-hours for a
        CPU charge) and machine_name is always explicit so resources with
        multiple SAM machines (e.g. Casper) don't trigger auto-detection.

    Raises:
        ValueError: For an unknown machine name.
    """
    cpu_h = cpu_hours or 0.0
    gpu_h = gpu_hours or 0.0
    cpu_c = cpu_charges or 0.0
    gpu_c = gpu_charges or 0.0

    # special rules for special queues
    if queue == "vis":      # vis queue consumes GPUs, but we don't charge for that
        gpu_h = gpu_c = 0.0 # (so accessible to projects without GPU allocations)

    total = cpu_h + gpu_h
    gpu_fraction = (gpu_h / total) if total > 0.0 else 0.0
    is_gpu = gpu_h > 0 and gpu_fraction >= GPU_FRACTION_THRESHOLD

    if machine == "derecho":
        if is_gpu:
            # Meaningful GPU usage -> Derecho GPU resource
            # comp_hours = GPU hours (the Derecho GPU billing metric)
            return "Derecho GPU", "derecho-gpu", gpu_h, gpu_c
        # Pure CPU job (or anomalous GPU ratio -> treat as CPU)
        # comp_hours = CPU core-hours (numnodes * 128 * wall_hours)
        return "Derecho", "derecho", cpu_h, cpu_c

    elif machine == "casper":
        if is_gpu:
            # Casper GPU resource
            # TODO: confirm Casper GPU resource name and charges formula
            return "Casper GPU", "Casper-gpu", gpu_h, gpu_c
        # Casper CPU resource
        return "Casper", "Casper", cpu_h, cpu_c

    else:
        raise ValueError(f"Unknown machine: {machine!r}. Add a case to classify_comp_resource().")


def adapt_jobstats_row(row: dict, machine: str) -> Optional[tuple]:
    """
    Classify an hpc-usage-queries daily summary row into SAM resource and charge fields.

    Thin wrapper over ``classify_comp_resource`` that applies the poster-only
    rule of skipping zero-compute rows.

    Args:
        row: Dict from JobQueries.daily_summary_report() with keys:
             date, user, account, queue,
             job_count, cpu_hours, gpu_hours, memory_hours
        machine: 'derecho' or 'casper'

    Returns:
        (resource_name, machine_name, core_hours, charges) to pass to
        upsert_comp_charge_summary(), or None to silently skip the row (e.g.
        zero-charge row).

    Raises:
        ValueError: For rows with an unknown machine name.
    """
    resource_name, machine_name, comp_hours, charges = classify_comp_resource(
        machine,
        row.get("queue", "Unknown"),
        row["cpu_hours"],
        row["gpu_hours"],
        row["cpu_charges"],
        row["gpu_charges"],
    )
    if comp_hours <= 0.0:
        return None  # Skip zero-charge rows
    return resource_name, machine_name, comp_hours, charges


class CompIngestMixin:
    """The --comp path of `AccountingAdminCommand`."""

    def _run_comp(self, machine: str, start_date: date, end_date: date, **kwargs) -> int:
        """Query hpc-usage-queries and post results to comp_charge_summary."""
        # --- 0. Cutover-epoch enforcement (refuse pre-epoch writes) ----
        # Check before plugin load so operators see a clean epoch error
        # even when hpc-usage-queries isn't installed.
        effective_epoch = kwargs.get("epoch") or COMP_CHARGING_EPOCH
        if start_date < effective_epoch:
            self.console.print(
                f"Error: start_date {start_date} is before the "
                f"COMP_CHARGING_EPOCH ({effective_epoch}). "
                "This command only writes post-epoch comp_charge_summary "
                "rows; pre-epoch historical data is not rewritten. "
                "Pass --epoch YYYY-MM-DD to override for a known-safe "
                "backfill.",
                style="bold red",
            )
            return EXIT_ERROR

        # NOTE: unlike _run_disk, this deliberately does NOT write to
        # `comp_charge_summary_status`. Legacy migration V13 reshaped that table
        # into an in-flight aggregation lock keyed by (command_id,
        # charge_summary_id) -- a transient lock legacy's Quartz watchdog
        # cleaned up, not a "this date is posted" checkpoint. The lock pipeline
        # has been dormant since 2024-11 (438 stale rows, one abandoned
        # command_id) and no legacy report joins it to filter
        # comp_charge_summary, so our writes are visible as-is.

        # --- 1. Load job_history plugin (graceful error if not installed) ---
        mod = self.require_plugin(HPC_USAGE_QUERIES)
        if mod is None:
            return EXIT_ERROR
        jh_get_session = mod.get_session
        JobQueries = mod.JobQueries

        # --- 2. Open hpc-usage-queries session ---
        try:
            jh_session = jh_get_session(machine)
        except Exception as exc:
            self.console.print(f"[bold red]Error opening job_history session for {machine!r}: {exc}[/bold red]")
            return EXIT_ERROR

        # --- 3. Fetch daily summary rows ---
        try:
            rows = list(JobQueries(jh_session).daily_summary_report(start=start_date, end=end_date))
        except Exception as exc:
            self.console.print(f"[bold red]Error fetching daily summary: {exc}[/bold red]")
            return EXIT_ERROR
        finally:
            jh_session.close()

        # --- 4. Validate rows exist ---
        if not rows:
            self.console.print(
                f"[yellow]No data found for {machine} between {start_date} and {end_date}[/yellow]"
            )
            return EXIT_SUCCESS

        self.console.print(
            f"Found [bold]{len(rows)}[/bold] rows for [bold]{machine}[/bold] "
            f"({start_date} → {end_date})"
        )

        # --- 5. Verbose: show charge-row table (independent of dry-run) ---
        if self.ctx.verbose:
            display_dry_run_table(
                self.ctx, rows, machine, adapt_jobstats_row, normalize_queue_name,
                dry_run=kwargs.get("dry_run", False),
            )

        # --- 5b. Dry-run: skip insertion ---
        if kwargs.get("dry_run"):
            return EXIT_SUCCESS

        # --- 6-7. Chunk and post rows ---
        n_created = 0
        n_updated = 0
        n_errors = 0
        n_skipped = 0

        chunks = [rows[i:i + kwargs["chunk_size"]] for i in range(0, len(rows), kwargs["chunk_size"])]

        with progress_bar(self.ctx) as progress:
            task = progress.add_task(f"Posting {machine} charges...", total=len(rows))

            for chunk_idx, chunk in enumerate(chunks, start=1):
                try:
                    with management_transaction(self.session):
                        for row in chunk:
                            result = adapt_jobstats_row(row, machine)
                            progress.advance(task)
                            if result is None:
                                n_skipped += 1
                                continue

                            resource_name, machine_name, core_hours, charges = result

                            # Warn on anomalous GPU fraction (proceeded as CPU)
                            cpu_h = row["cpu_hours"] or 0.0
                            gpu_h = row["gpu_hours"] or 0.0
                            queue = row.get("queue", "Unknown")
                            if queue == "vis":
                                gpu_h = 0.0
                            if gpu_h > 0 and resource_name in ("Derecho", "Casper"):
                                gpu_fraction = gpu_h / (cpu_h + gpu_h)
                                if self.ctx.verbose:
                                    self.console.print(
                                        f"[yellow]Warning: low GPU fraction ({gpu_fraction:.1%}) "
                                        f"for {row['user']}/{row['account']} on {row['date']} "
                                        f"— posting to {resource_name}[/yellow]"
                                    )

                            try:
                                _, action = upsert_comp_charge_summary(
                                    self.session,
                                    activity_date=date.fromisoformat(str(row["date"])),
                                    act_username=row["user"],
                                    act_projcode=row["account"],
                                    act_unix_uid=None,
                                    resource_name=resource_name,
                                    machine_name=machine_name,
                                    queue_name=normalize_queue_name(row["queue"]),
                                    num_jobs=row["job_count"],
                                    core_hours=core_hours,
                                    charges=charges,
                                    create_queue_if_missing=kwargs["create_queues"],
                                    include_deleted_accounts=kwargs["include_deleted_accounts"],
                                )
                                if action == "created":
                                    n_created += 1
                                else:
                                    n_updated += 1
                            except ValueError as exc:
                                n_errors += 1
                                if not kwargs["skip_errors"]:
                                    raise
                                if self.ctx.verbose:
                                    self.console.print(f"[yellow]Skip: {exc}[/yellow]")

                except ValueError as exc:
                    # Chunk-level failure (skip_errors=False): re-raised from inner loop
                    self.console.print(f"[bold red]Chunk {chunk_idx} aborted: {exc}[/bold red]")
                    return EXIT_ERROR

        # --- 8. Summary ---
        display_import_summary(self.ctx, n_created, n_updated, n_errors, n_skipped)

        # --- 9. Exit code ---
        return EXIT_SUCCESS if n_errors == 0 else EXIT_ERROR
