#!/usr/bin/env python3
import pymysql, yaml

CONFIG_FILE = "config.yaml"

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

def cleanup_orphans():
    cfg = load_config()
    conn = pymysql.connect(
        host=cfg["local"]["host"],
        user=cfg["local"]["user"],
        password=cfg["local"]["password"],
        database=cfg["local"]["database"]
    )

    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name, column_name, referenced_table_name, referenced_column_name
            FROM information_schema.key_column_usage
            WHERE table_schema=%s AND referenced_table_name IS NOT NULL
        """, (cfg["local"]["database"],))
        fks = cur.fetchall()

    with conn.cursor() as cur:
        for table, col, parent, parent_col in fks:
            query = f"""
            DELETE c FROM {table} c
            LEFT JOIN {parent} p ON c.{col} = p.{parent_col}
            WHERE p.{parent_col} IS NULL;
            """
            print(f"Cleaning {table}.{col} → {parent}.{parent_col}")
            cur.execute(query)
        conn.commit()

    print("✅ Orphan cleanup complete.")

if __name__ == "__main__":
    cleanup_orphans()
