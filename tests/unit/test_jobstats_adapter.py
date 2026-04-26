"""Tests for cli.accounting.commands.adapt_jobstats_row.

`adapt_jobstats_row` is the one piece of CLI command code that is pure
logic (no Rich console, no DB, no plugin loading) — it classifies an
hpc-usage-queries daily summary row into the right SAM resource +
charge fields based on machine and CPU/GPU hour mix.

These tests pin the classification matrix so changes to the GPU-fraction
threshold or per-machine resource names show up as test failures instead
of silently mis-charging downstream.
"""
import pytest

from cli.accounting.commands import (
    GPU_FRACTION_THRESHOLD,
    adapt_jobstats_row,
    normalize_queue_name,
)

pytestmark = pytest.mark.unit


def _row(*, cpu_hours=0.0, gpu_hours=0.0, cpu_charges=0.0, gpu_charges=0.0):
    """Minimal jobstats row — only the keys adapt_jobstats_row reads."""
    return {
        "cpu_hours": cpu_hours,
        "gpu_hours": gpu_hours,
        "cpu_charges": cpu_charges,
        "gpu_charges": gpu_charges,
    }


class TestAdaptJobstatsRow:
    """Classification matrix for derecho / casper jobs."""

    def test_zero_total_returns_none(self):
        """Zero CPU + zero GPU hours → silently skipped (returns None)."""
        assert adapt_jobstats_row(_row(), "derecho") is None
        assert adapt_jobstats_row(_row(), "casper") is None

    def test_null_hours_treated_as_zero(self):
        """SQL NULLs in either column must coerce to 0.0, not raise."""
        row = {
            "cpu_hours": None,
            "gpu_hours": None,
            "cpu_charges": None,
            "gpu_charges": None,
        }
        assert adapt_jobstats_row(row, "derecho") is None

    def test_derecho_pure_cpu(self):
        result = adapt_jobstats_row(
            _row(cpu_hours=1000.0, cpu_charges=500.0),
            "derecho",
        )
        assert result == ("Derecho", "derecho", 1000.0, 500.0)

    def test_derecho_pure_gpu(self):
        result = adapt_jobstats_row(
            _row(gpu_hours=100.0, gpu_charges=200.0),
            "derecho",
        )
        assert result == ("Derecho GPU", "derecho-gpu", 100.0, 200.0)

    def test_derecho_anomalous_gpu_below_threshold_classifies_as_cpu(self):
        """Tiny GPU touch on a big CPU job → CPU resource (the comment in
        adapt_jobstats_row calls this out as the explicit guard)."""
        # 1M cpu-h + 10 gpu-h → gpu_fraction = 1e-5, well below 1% threshold
        result = adapt_jobstats_row(
            _row(cpu_hours=1_000_000.0, gpu_hours=10.0,
                 cpu_charges=999.0, gpu_charges=1.0),
            "derecho",
        )
        assert result is not None
        resource_name, _, _, _ = result
        assert resource_name == "Derecho"

    def test_derecho_gpu_at_exact_threshold_classifies_as_gpu(self):
        """gpu_fraction == GPU_FRACTION_THRESHOLD → GPU (>= comparison)."""
        # Pick numbers where gpu_h / (cpu_h + gpu_h) == GPU_FRACTION_THRESHOLD exactly.
        gpu_h = GPU_FRACTION_THRESHOLD * 100.0
        cpu_h = 100.0 - gpu_h
        result = adapt_jobstats_row(
            _row(cpu_hours=cpu_h, gpu_hours=gpu_h, gpu_charges=5.0),
            "derecho",
        )
        assert result is not None
        assert result[0] == "Derecho GPU"

    def test_casper_pure_cpu(self):
        result = adapt_jobstats_row(
            _row(cpu_hours=50.0, cpu_charges=12.5),
            "casper",
        )
        assert result == ("Casper", "Casper", 50.0, 12.5)

    def test_casper_pure_gpu(self):
        result = adapt_jobstats_row(
            _row(gpu_hours=20.0, gpu_charges=8.0),
            "casper",
        )
        assert result == ("Casper GPU", "Casper-gpu", 20.0, 8.0)

    def test_unknown_machine_raises(self):
        with pytest.raises(ValueError, match="Unknown machine"):
            adapt_jobstats_row(_row(cpu_hours=10.0), "frontier")


class TestNormalizeQueueName:
    """Ephemeral PBS reservation queues collapse to canonical 'reservation'."""

    @pytest.mark.parametrize("ephemeral", [
        "R5184776",   # standard reservation
        "M2498882",   # maintenance reservation
        "S870294",    # standing reservation
    ])
    def test_ephemeral_reservation_queues_collapse(self, ephemeral):
        assert normalize_queue_name(ephemeral) == "reservation"

    @pytest.mark.parametrize("normal", [
        "main",
        "preempt",
        "casper",
        "develop",
        "regular",
    ])
    def test_normal_queues_pass_through(self, normal):
        assert normalize_queue_name(normal) == normal

    def test_letter_only_no_digits_passes_through(self):
        # Only [RMS]<digit> is collapsed; "Reserved" should pass through.
        assert normalize_queue_name("Reserved") == "Reserved"
