#!/usr/bin/env python
# coding: utf-8

# In[ ]:


from dotenv import load_dotenv
import os

load_dotenv()  # Loads .env into environment variables
username = os.environ['SAM_DB_USERNAME']
password = os.environ['SAM_DB_PASSWORD']
server = os.environ['SAM_DB_SERVER']
database = 'sam'

print(f'{username}:$SAM_DB_PASSWORD@{server}/{database}')


# In[ ]:


from sqlalchemy import create_engine, inspect, text

# Create connection string
connection_string = f'mysql+mysqlconnector://{username}:{password}@{server}/{database}'

engine = create_engine(connection_string)
try:
    connection = engine.connect()
    print("Successfully connected to the database!")
    connection.close() # Close the connection when done
except Exception as e:
    print(f"Error connecting to the database: {e}")

try:
    # Create inspector
    inspector = inspect(engine)

    # Get all table names
    table_names = inspector.get_table_names()
    print("Available tables:")
    for table in table_names:
        print(f"  • {table}")

    # Get view names too
    view_names = inspector.get_view_names()
    print("\nAvailable views:")
    for view in view_names:
        print(f"  • {view}")

except Exception as e:
    print(f"Error querying to the database: {e}")


# In[ ]:


def summarize_table(engine, table_name, sample_size=5):
    """
    Get comprehensive table summary without downloading entire table
    """
    inspector = inspect(engine)

    print("="*80)
    print(f"TABLE SUMMARY: {table_name}")
    print("="*80)

    # 1. Get row count
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        row_count = result.scalar()

    print(f"\n📊 Total Rows: {row_count:,}")

    # 2. Get column information
    columns = inspector.get_columns(table_name)
    print(f"📋 Total Columns: {len(columns)}")

    # 3. Display column details
    print("\n" + "-"*80)
    print("COLUMN DETAILS:")
    print("-"*80)

    col_df = pd.DataFrame([{
        'Column': col['name'],
        'Type': str(col['type']),
        'Nullable': '✓' if col['nullable'] else '✗',
        'Default': col['default'] if col['default'] else ''
    } for col in columns])

    display(col_df)

    # 4. Get primary keys
    pk = inspector.get_pk_constraint(table_name)
    if pk['constrained_columns']:
        print(f"\n🔑 Primary Key(s): {', '.join(pk['constrained_columns'])}")

    # 5. Preview sample data
    if row_count > 0:
        print(f"\n" + "-"*80)
        print(f"SAMPLE DATA (first/last rows):")
        print("-"*80)

        sample = pd.read_sql(f'SELECT * FROM {table_name} LIMIT {sample_size}', engine)

        if row_count > 100:
            # Get primary key or first column for ordering
            if pk['constrained_columns']:
                order_col = pk['constrained_columns'][0]
            else:
                order_col = columns[0]['name']

            # Different approaches for different databases
            # This works for MySQL, PostgreSQL, SQL Server
            last_rows_query = f"""
            SELECT * FROM {table_name}
            ORDER BY {order_col} DESC
            LIMIT {sample_size}
            """

            last_rows = pd.read_sql(last_rows_query, engine)
            # Reverse to show in ascending order
            last_rows = last_rows.iloc[::-1].reset_index(drop=True)
            sample = pd.concat([sample, last_rows], ignore_index=True)
        display(sample)

#    # 7. Get NULL counts for each column (sampling approach)    
#    null_query = f"""
#    SELECT 
#        {', '.join([f"SUM(CASE WHEN {col['name']} IS NULL THEN 1 ELSE 0 END) as {col['name']}_nulls" 
#                    for col in columns])}
#    FROM {table_name}
#    """
#    
#    null_counts = pd.read_sql(null_query, engine)
#
#    if len(null_counts) > 1:
#        null_summary = pd.DataFrame({
#            'Column': [col.replace('_nulls', '') for col in null_counts.columns],
#            'Null Count': null_counts.iloc[0].values
#        })
#    
#        print("\n" + "-"*80)
#        print("NULL VALUE COUNTS:")
#        print("-"*80)
#        display(null_summary[null_summary['Null Count'] > 0])

    print("\n"*3)


# In[ ]:


import pandas as pd

def df_from_sql_table(name, engine):
    df = pd.read_sql_table(name, engine)
    display(df)

    # With memory usage per column
    display(df.info(memory_usage='deep'))  # More accurate memory calculation
    return df


# In[ ]:


# Load data
#users_df = df_from_sql_table('users', engine)

#projects_df = df_from_sql_table('project', engine)

#alloc_df = df_from_sql_table('allocation', engine)


# In[ ]:


# Read the SQL query from a file
with open("queries/hpc_annual_usage_only.sql", "r") as f:
    query = f.read()

u_p_history_df = pd.read_sql_query(query, engine)
print(u_p_history_df)


# In[ ]:


import matplotlib.pyplot as plt

df = u_p_history_df
df.plot(x='year', y='unique_users', marker='o', linestyle='-', figsize=(8,5))

plt.title('Unique Users per Year')
plt.xlabel('Year')
plt.ylabel('Unique Users')
plt.grid(True, linestyle='--', alpha=0.6)
plt.tight_layout()
plt.show()


# In[ ]:


import matplotlib.pyplot as plt

# assuming your DataFrame is named df

fig, ax1 = plt.subplots(figsize=(9,5))

ax1.plot(df['year'], df['unique_users'], '-', color='black', label='# Users', lw=4, zorder=1)
ax1.plot(df['year'], df['unique_projects'], '--', color='tab:blue', label='# Projects', lw=3)
ax1.plot(df['year'], df['unique_institutions'], '-.', color='tab:orange', label='# Institutions', lw=3)

ax1.set_xlabel('Year')
ax1.set_ylabel('Unique Users, Projects, & Institutions')

# --- Title and grid
#plt.title('Unique Users, Institutions, and Projects per Year')
ax1.grid(True, linestyle='--', alpha=0.6)
ax1.set_ylim([0, 3500])

# --- Combine all legends
lines, labels = [], []
for ax in [ax1]:
    line, label = ax.get_legend_handles_labels()
    lines += line
    labels += label
ax1.legend(
    lines, 
    labels, 
    loc='upper left', 
    frameon=False,
    handlelength=4,   # default is 2; larger number = longer line
    handleheight=1,   # default is 0.7; controls vertical size of legend handle
    handletextpad=0.5 # space between line and label
)
ax1.set_xticks(df['year'])
ax1.set_xticklabels(df['year'], rotation=0, ha='center')
ax1.set_xlim([2013,2025])

plt.tight_layout()
plt.savefig('history.png', dpi=200)
plt.show()


# In[ ]:


#df = df_from_sql_table('access_branch_resource', engine)

# Get all table names
table_names = inspector.get_table_names()
table_names.append('comp_charge_summary')
print("Available tables:")
for table in table_names:
    #break
    if table == 'api_credentials': continue
    summarize_table(engine, table)


# In[ ]:




