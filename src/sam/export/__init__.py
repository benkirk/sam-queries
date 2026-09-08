"""Spreadsheet export helpers (write-only, Flask-free so the CLI can reuse them)."""

from .workbook import Column, build_workbook

__all__ = ["Column", "build_workbook"]
