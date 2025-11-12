#!/usr/bin/env python3
"""
Test script to retry the 3 failed tables: comp_activity, comp_job, dataset_activity
"""
import sys
sys.path.insert(0, '.')

from bootstrap_clone import *

cfg = load_config()

# Connect to remote
print("🔌 Connecting to remote DB...")
remote = cfg["remote"]
remote_conn = connect_mysql(remote["host"], remote["user"], remote["password"], remote["database"], use_ssl=True)

# Get table info
tables_info = get_tables_info(remote_conn, remote["database"])
table_sizes = {r["table_name"]: r["size_mb"] for r in tables_info}
all_tables = [r["table_name"] for r in tables_info]

# Get FKs
fk_rows = get_foreign_keys(remote_conn, remote["database"])
fk_map = defaultdict(list)
for r in fk_rows:
    fk_map[r["child_table"]].append({
        "child_col": r["child_col"],
        "parent_table": r["parent_table"],
        "parent_col": r["parent_col"]
    })

# Get primary keys
pk_map = {}
for t in all_tables:
    pk_map[t] = get_primary_key_columns(remote_conn, remote["database"], t)

# Test the three failed tables
test_tables = ["comp_activity", "comp_job", "dataset_activity"]
sampled_ids_store = {}
loaded_tables = set()

for table in test_tables:
    print(f"\n{'='*60}")
    print(f"Testing table: {table}")
    print(f"{'='*60}")

    size_mb = table_sizes.get(table, 0)
    try:
        fname = sample_and_dump_table(cfg, remote_conn, table, size_mb, pk_map, fk_map, sampled_ids_store, loaded_tables)
        print(f"✅ Successfully dumped {table} to {fname}")

        # Try to load it
        print(f"📥 Loading {table} into local DB...")
        load_local(cfg, fname)
        print(f"✅ Successfully loaded {table}")

        loaded_tables.add(table)
    except Exception as e:
        print(f"❌ Error with {table}: {e}")
        import traceback
        traceback.print_exc()

print("\n🎉 Test complete!")
remote_conn.close()
