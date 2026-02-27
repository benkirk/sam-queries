"""
Accounting admin commands for SAM.

Bridges hpc-usage-queries daily charge data into the SAM comp_charge_summary table.
"""
from datetime import date
from typing import Optional

from cli.core.base import BaseCommand
from cli.accounting.display import display_dry_run_table, display_import_summary
from sam.manage.summaries import upsert_comp_charge_summary
from sam.manage.transaction import management_transaction
from sam.plugins import HPC_USAGE_QUERIES

# Threshold: GPU hours must be at least this fraction of total compute hours
# to classify a row as a GPU resource charge rather than CPU.
# Avoids misclassifying CPU jobs that happened to touch a GPU queue briefly.
GPU_FRACTION_THRESHOLD = 0.01  # 1%


def adapt_jobstats_row(row: dict, machine: str) -> Optional[tuple]:
    """
    Classify an hpc-usage-queries daily summary row into SAM resource and charge fields.

    This function is the single place where machine-specific billing rules live.
    It handles:
      - Which SAM resource name to use (e.g. "Derecho" vs "Derecho GPU")
      - What core_hours and charges to post
      - Sanity checks for anomalous data (e.g. 1M CPU-h + 10 GPU-h → CPU, not GPU)

    Args:
        row: Dict from JobQueries.daily_summary_report() with keys:
             date, user, account, queue,
             job_count, cpu_hours, gpu_hours, memory_hours
        machine: 'derecho' or 'casper'

    Returns:
        (resource_name, machine_name, core_hours, charges) to pass to
        upsert_comp_charge_summary(), or None to silently skip the row (e.g.
        zero-charge row).  machine_name is always explicit so that resources
        with multiple SAM machines (e.g. Casper) don't trigger auto-detection.

    Raises:
        ValueError: For rows with an unknown machine name.
    """
    cpu_h = row["cpu_hours"] or 0.0
    gpu_h = row["gpu_hours"] or 0.0
    total = cpu_h + gpu_h

    if total <= 0.0:
        return None  # Skip zero-charge rows

    gpu_fraction = gpu_h / total

    if machine == "derecho":
        if gpu_h > 0 and gpu_fraction >= GPU_FRACTION_THRESHOLD:
            # Meaningful GPU usage → Derecho GPU resource
            # core_hours = GPU hours (the Derecho GPU billing metric)
            # TODO: confirm whether charges = gpu_hours or a weighted formula
            return "Derecho GPU", "derecho-gpu", gpu_h, gpu_h
        # Pure CPU job (or anomalous GPU ratio → treat as CPU)
        # core_hours = CPU core-hours (numnodes * 128 * wall_hours)
        # TODO: confirm charges formula (queue_factor multiplier?)
        return "Derecho", "derecho", cpu_h, cpu_h

    elif machine == "casper":
        if gpu_h > 0 and gpu_fraction >= GPU_FRACTION_THRESHOLD:
            # Casper GPU resource
            # TODO: confirm Casper GPU resource name and charges formula
            return "Casper GPU", "Casper-gpu", gpu_h, gpu_h
        # Casper CPU resource
        # TODO: confirm Casper charges formula (cpu_hours + memory_hours?)
        return "Casper", "Casper", cpu_h, cpu_h

    else:
        raise ValueError(f"Unknown machine: {machine!r}. Add a case to adapt_jobstats_row().")


class AccountingAdminCommand(BaseCommand):
    """Posts daily charge summaries from hpc-usage-queries into SAM."""

    def execute(
        self,
        *,
        comp: bool = False,
        disk: bool = False,
        archive: bool = False,
        machine: str,
        start_date: date,
        end_date: date,
        dry_run: bool = False,
        skip_errors: bool = False,
        create_queues: bool = False,
        chunk_size: int = 500,
        include_deleted_accounts: bool = False,
    ) -> int:
        if comp:
            return self._run_comp(
                machine, start_date, end_date,
                dry_run=dry_run,
                skip_errors=skip_errors,
                create_queues=create_queues,
                chunk_size=chunk_size,
                include_deleted_accounts=include_deleted_accounts,
            )
        if disk:
            self.console.print("[yellow]--disk: not yet implemented[/yellow]")
            return 0
        if archive:
            self.console.print("[yellow]--archive: not yet implemented[/yellow]")
            return 0
        self.console.print("Error: specify --comp, --disk, or --archive", style="bold red")
        return 1

    def _run_comp(self, machine: str, start_date: date, end_date: date, **kwargs) -> int:
        """Query hpc-usage-queries and post results to comp_charge_summary."""
        # --- 1. Load job_history plugin (graceful error if not installed) ---
        mod = self.require_plugin(HPC_USAGE_QUERIES)
        if mod is None:
            return 2
        jh_get_session = mod.get_session
        JobQueries = mod.JobQueries

        # --- 2. Open hpc-usage-queries session ---
        try:
            jh_session = jh_get_session(machine)
        except Exception as exc:
            self.console.print(f"[bold red]Error opening job_history session for {machine!r}: {exc}[/bold red]")
            return 2

        # --- 3. Fetch daily summary rows ---
        try:
            rows = list(JobQueries(jh_session).daily_summary_report(start=start_date, end=end_date))
        except Exception as exc:
            self.console.print(f"[bold red]Error fetching daily summary: {exc}[/bold red]")
            return 2
        finally:
            jh_session.close()

        # --- 4. Validate rows exist ---
        if not rows:
            self.console.print(
                f"[yellow]No data found for {machine} between {start_date} and {end_date}[/yellow]"
            )
            return 0

        self.console.print(
            f"Found [bold]{len(rows)}[/bold] rows for [bold]{machine}[/bold] "
            f"({start_date} → {end_date})"
        )

        # --- 5. Dry-run: show Rich table and return ---
        if kwargs.get("dry_run"):
            display_dry_run_table(self.ctx, rows, machine, adapt_jobstats_row)
            return 0

        # --- 6-7. Chunk and post rows ---
        n_created = 0
        n_updated = 0
        n_errors = 0
        n_skipped = 0

        chunks = [rows[i:i + kwargs["chunk_size"]] for i in range(0, len(rows), kwargs["chunk_size"])]

        for chunk_idx, chunk in enumerate(chunks, start=1):
            try:
                with management_transaction(self.session):
                    for row in chunk:
                        result = adapt_jobstats_row(row, machine)
                        if result is None:
                            n_skipped += 1
                            continue

                        resource_name, machine_name, core_hours, charges = result

                        # Warn on anomalous GPU fraction (proceeded as CPU)
                        cpu_h = row["cpu_hours"] or 0.0
                        gpu_h = row["gpu_hours"] or 0.0
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
                                queue_name=row["queue"],
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
                return 2

        # --- 8. Summary ---
        display_import_summary(self.ctx, n_created, n_updated, n_errors, n_skipped)

        # --- 9. Exit code ---
        return 0 if n_errors == 0 else 2
