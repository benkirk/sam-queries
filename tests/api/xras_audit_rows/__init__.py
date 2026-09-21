"""XRAS audit-row survival scenarios — see scenarios.json for what each declares.

These need real MySQL under ``STRICT_TRANS_TABLES`` (errors 1406/1366) and run in
the default suite; the package is xdist-safe (rows are cleaned up by captured PK).
"""
