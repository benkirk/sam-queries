# Plan: `sam-admin accounting --comp` Command

## Context

The SAM project needs a way to post daily HPC charge summaries into the `comp_charge_summary` table. The `hpc-usage-queries` package (already a required dependency) provides pre-aggregated daily charge data from Derecho/Casper PBS logs via a SQLite database. This plan wires up a new `sam-admin accounting --comp` command that bridges the two systems using the ORM path documented in `docs/CHARGING_INTEGRATION.md` (Option 2).

The existing `sam/manage/summaries.py` already implements `upsert_comp_charge_summary()` with full entity resolution, upsert semantics, and error handling — we just need to drive it from the CLI.

---

## Critical Files

| File | Role |
|------|------|
| `src/cli/cmds/admin.py` | Entry point — add `accounting` command group |
| `src/cli/accounting/__init__.py` | New package init |
| `src/cli/accounting/commands.py` | New `AccountingAdminCommand` class |
| `src/sam/manage/summaries.py` | `upsert_comp_charge_summary()` — existing, reuse as-is |
| `src/sam/manage/transaction.py` | `management_transaction()` — existing, reuse as-is |
| `job_history/__init__.py` | `get_session(machine)`, `JobQueries` — external package |
| `job_history/models.py` | `DailySummary` model — `charge_hours`, `cpu_hours`, `gpu_hours`, `memory_hours` |

---

## Implementation Plan

### Step 1 — New `src/cli/accounting/__init__.py`

Empty package init. One line: `"""Accounting admin commands."""`

---

### Step 2 — New `src/cli/accounting/commands.py`

#### Module-level adapter function (the key business logic)

```python
# Threshold: GPU hours must be at least this fraction of total compute hours
# to classify a row as a GPU resource charge rather than CPU.
# Avoids misclassifying CPU jobs that happened to touch a GPU queue briefly.
GPU_FRACTION_THRESHOLD = 0.01  # 1%

def adapt_hpc_row(row: dict, machine: str) -> tuple[str, float, float] | None:
    """
    Classify an hpc-usage-queries daily summary row into a SAM resource and charge fields.

    This function is the single place where the machine-specific billing rules live.
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
        (resource_name, core_hours, charges) to pass to upsert_comp_charge_summary()
        Returns None to silently skip the row (e.g. zero-charge row)

    Raises:
        ValueError: For rows that cannot be classified (caller decides skip vs abort)
    """
    cpu_h = row["cpu_hours"] or 0.0
    gpu_h = row["gpu_hours"] or 0.0
    total = cpu_h + gpu_h

    if total <= 0.0:
        return None  # Skip zero-charge rows

    gpu_fraction = gpu_h / total if total > 0 else 0.0

    if machine == "derecho":
        if gpu_h > 0 and gpu_fraction >= GPU_FRACTION_THRESHOLD:
            # Meaningful GPU usage → Derecho GPU resource
            # core_hours = GPU hours (the Derecho GPU billing metric)
            # TODO: confirm whether charges = gpu_hours or a weighted formula
            return "Derecho GPU", gpu_h, gpu_h
        elif gpu_h > 0:
            # Anomalous: tiny GPU hours relative to CPU hours
            # Treat as CPU job but log a warning
            # (caller will emit a warning; we proceed as CPU)
            pass  # fall through to CPU case below
        # Pure CPU job (or anomalous GPU ratio → treat as CPU)
        # core_hours = CPU core-hours (numnodes * 128 * wall_hours)
        # TODO: confirm charges formula (queue_factor multiplier?)
        return "Derecho", cpu_h, cpu_h

    elif machine == "casper":
        if gpu_h > 0 and gpu_fraction >= GPU_FRACTION_THRESHOLD:
            # Casper GPU resource
            # TODO: confirm Casper GPU resource name and formula
            return "Casper GPU", gpu_h, gpu_h
        else:
            # Casper CPU resource
            # TODO: confirm Casper charges formula (cpu_hours + memory_hours?)
            return "Casper", cpu_h, cpu_h

    else:
        raise ValueError(f"Unknown machine: {machine!r}. Add a case to adapt_hpc_row().")
```

#### `AccountingAdminCommand` class

```python
class AccountingAdminCommand(BaseCommand):
    """Posts daily charge summaries from hpc-usage-queries into SAM."""

    def execute(
        self,
        *,
        comp: bool = False,
        disk: bool = False,      # placeholder
        archive: bool = False,   # placeholder
        machine: str,            # "derecho" or "casper"
        start_date: date,
        end_date: date,
        dry_run: bool = False,
        skip_errors: bool = False,
        create_queues: bool = False,
        chunk_size: int = 500,
        include_deleted_accounts: bool = False,
    ) -> int:
        if comp:
            return self._run_comp(machine, start_date, end_date,
                                  dry_run=dry_run, skip_errors=skip_errors,
                                  create_queues=create_queues, chunk_size=chunk_size,
                                  include_deleted_accounts=include_deleted_accounts)
        if disk:
            self.console.print("[yellow]--disk: not yet implemented[/yellow]")
            return 0
        if archive:
            self.console.print("[yellow]--archive: not yet implemented[/yellow]")
            return 0
        self.console.print("Error: specify --comp, --disk, or --archive", style="bold red")
        return 1

    def _run_comp(self, machine, start_date, end_date, **kwargs) -> int:
        """Query hpc-usage-queries and post to comp_charge_summary."""
        ...
```

**`_run_comp` logic:**

```
1. Import job_history (with graceful ImportError: print helpful message and exit 2)
2. Get hpc-usage-queries session: job_history.get_session(machine)
3. Call JobQueries(jh_session).daily_summary_report(start=start_date, end=end_date)
4. Validate rows exist; report total count to console
5. If dry_run: print Rich table (date, user, account, queue, jobs, cpu_h, gpu_h, resource→, core_h, charges) and return 0
6. Chunk rows into groups of chunk_size
7. For each chunk, open management_transaction(self.session):
     For each row:
       result = adapt_hpc_row(row, machine)
       if result is None: continue  (zero-charge row)
       resource_name, core_hours, charges = result

       # Warn if anomalous GPU fraction but proceeding as CPU
       gpu_fraction = row['gpu_hours'] / (row['cpu_hours'] + row['gpu_hours'])
       if row['gpu_hours'] > 0 and gpu_fraction < GPU_FRACTION_THRESHOLD:
           self.console.print(f"[yellow]Warning: low GPU fraction ({gpu_fraction:.1%}) "
                              f"for {row['user']}/{row['account']} on {row['date']} "
                              f"— posting to {resource_name}[/yellow]")

       try:
           record, action = upsert_comp_charge_summary(
               self.session,
               activity_date=date.fromisoformat(row['date']),
               act_username=row['user'],
               act_projcode=row['account'],
               act_unix_uid=None,
               resource_name=resource_name,
               queue_name=row['queue'],
               num_jobs=row['job_count'],
               core_hours=core_hours,
               charges=charges,
               create_queue_if_missing=kwargs['create_queues'],
               include_deleted_accounts=kwargs['include_deleted_accounts'],
           )
           increment n_created or n_updated
       except ValueError as e:
           n_errors += 1
           if not kwargs['skip_errors']:
               raise  # bubbles up, aborts chunk transaction
           log warning to console

8. Print Rich summary: n_created, n_updated, n_errors, n_skipped
9. Return EXIT_SUCCESS (0) if n_errors == 0, else EXIT_ERROR (2) if errors and not skip_errors
```

---

### Step 3 — Modify `src/cli/cmds/admin.py`

Add the `accounting` command group. Pattern follows existing `project` command:

```python
from cli.accounting.commands import AccountingAdminCommand

@cli.command()
@click.option('--comp', is_flag=True, help='Post computational charge summaries')
@click.option('--disk', is_flag=True, help='Post disk charge summaries (not yet implemented)')
@click.option('--archive', is_flag=True, help='Post archive charge summaries (not yet implemented)')
@click.option('--machine', '-m', type=click.Choice(['derecho', 'casper']), required=True,
              help='HPC machine to pull charges from')
@click.option('--start-date', type=click.DateTime(formats=['%Y-%m-%d']), required=True)
@click.option('--end-date', type=click.DateTime(formats=['%Y-%m-%d']), required=True)
@click.option('--dry-run', is_flag=True, help='Show what would be posted, without writing')
@click.option('--skip-errors', is_flag=True, help='Skip rows that fail entity resolution')
@click.option('--create-queues', is_flag=True, help='Auto-create unknown queues in SAM')
@click.option('--chunk-size', type=int, default=500, show_default=True)
@click.option('--include-deleted-accounts', is_flag=True)
@click.option('--verbose', '-v', is_flag=True)
@pass_context
def accounting(ctx, comp, disk, archive, machine, start_date, end_date,
               dry_run, skip_errors, create_queues, chunk_size,
               include_deleted_accounts, verbose):
    """Post daily charge summaries from HPC job history into SAM."""
    if verbose:
        ctx.verbose = True
    command = AccountingAdminCommand(ctx)
    exit_code = command.execute(
        comp=comp, disk=disk, archive=archive,
        machine=machine,
        start_date=start_date.date(),
        end_date=end_date.date(),
        dry_run=dry_run, skip_errors=skip_errors,
        create_queues=create_queues, chunk_size=chunk_size,
        include_deleted_accounts=include_deleted_accounts,
    )
    sys.exit(exit_code)
```

---

## Key Design Decisions

1. **`adapt_hpc_row()` as a module-level function** — the single authoritative place for machine-specific billing classification rules. Clearly marked with `# TODO` comments for each formula that needs production values. Easy to test independently and modify without touching the command class. The `GPU_FRACTION_THRESHOLD` constant (1%) is tunable without touching logic.

2. **Per-row resource routing** — a single batch from Derecho may contain both CPU queue rows (→ "Derecho") and GPU queue rows (→ "Derecho GPU"). The adapter function is called per-row; `upsert_comp_charge_summary` is called with the per-row `resource_name`. No pre-split of the batch is needed.

3. **Anomalous GPU ratio handling** — rows with `0 < gpu_fraction < threshold` emit a console warning but proceed as CPU charges (not silently dropped). This surfaces data anomalies without aborting the batch.

4. **`--machine` required, not `--resource`** — maps unambiguously to the hpc-usage-queries machine name (`derecho`/`casper`). SAM resource names come from `adapt_hpc_row()` internally, which can return different resource names for different rows from the same machine.

5. **`act_unix_uid=None`** — hpc-usage-queries daily summary has no unix UID. `_resolve_user()` in `summaries.py` already handles `None` uid gracefully (tries username only). Most SAM users have usernames that match PBS usernames.

6. **Chunked transactions** — per the CHARGING_INTEGRATION.md concurrency warning and batch pattern, large date ranges are chunked into groups of `chunk_size` rows per transaction. Rollback scope is one chunk.

7. **`--disk` and `--archive` as placeholders** — implement the Click options but print "not yet implemented" and return `EXIT_SUCCESS`. Future work will wire these to `fs_scans` package.

8. **Rich progress display** — use a Rich table for dry-run output and a progress/summary Rich table for actual runs showing per-date counts.

---

## Usage

```bash
# Dry run — see what would be posted
sam-admin accounting --comp --machine derecho \
  --start-date 2026-01-01 --end-date 2026-01-02 --dry-run

# Post charges for a date range
sam-admin accounting --comp --machine derecho \
  --start-date 2026-01-01 --end-date 2026-01-31

# Skip rows where user/project not found in SAM (common for new/test accounts)
sam-admin accounting --comp --machine derecho \
  --start-date 2026-01-01 --end-date 2026-01-02 --skip-errors --verbose

# Auto-create queues if they don't exist
sam-admin accounting --comp --machine casper \
  --start-date 2026-01-01 --end-date 2026-01-31 --create-queues
```

---

## Verification

```bash
# 1. Activate environment
source etc/config_env.sh

# 2. Test with dry-run first (no DB writes)
export JOB_HISTORY_DATA_DIR=/Users/benkirk/codes/hpc-usage-queries/devel/data
sam-admin accounting --comp --machine derecho \
  --start-date 2026-01-01 --end-date 2026-01-02 --dry-run

# 3. Run for real with --skip-errors to see counts
sam-admin accounting --comp --machine derecho \
  --start-date 2026-01-01 --end-date 2026-01-02 --skip-errors --verbose

# 4. Verify rows in MySQL
mysql -u root -h 127.0.0.1 -proot sam \
  -e "SELECT activity_date, COUNT(*), SUM(num_jobs), SUM(core_hours), SUM(charges) \
      FROM comp_charge_summary WHERE activity_date BETWEEN '2026-01-01' AND '2026-01-02' \
      GROUP BY activity_date;"

# 5. Run tests (no new tests required for stub, but regression check)
source ../.env && pytest tests/ --no-cov -k "not integration"
```
