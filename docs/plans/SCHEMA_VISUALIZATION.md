# Schema Visualization Guide

This document outlines options for generating entity-relationship diagrams from the SAM database schema and SQLAlchemy ORM models.

**Goal**: Generate two visual diagrams:
1. **ORM Diagram** - Shows SQLAlchemy model relationships (PDF/SVG)
2. **Database Diagram** - Shows actual SQL table relationships (PDF/SVG)

**Last Updated**: 2025-11-21

---

## Tool Options

### Option 1: eralchemy2 (Recommended)

Python library that generates ER diagrams from both SQLAlchemy models AND live databases. Single tool for both use cases.

**Installation**:
```bash
# Install Graphviz first (required for rendering)
brew install graphviz        # macOS
apt-get install graphviz     # Debian/Ubuntu

# Install Python package
pip install eralchemy2
```

**Usage**:
```bash
# From SQLAlchemy models (ORM diagram)
eralchemy2 -i 'sam.base.Base' -o diagrams/orm.pdf

# From database connection (SQL diagram)
eralchemy2 -i 'mysql+pymysql://root:root@127.0.0.1/sam' -o diagrams/database.pdf

# SVG output
eralchemy2 -i 'sam.base.Base' -o diagrams/orm.svg
```

| Pros | Cons |
|------|------|
| Single tool for both ORM and SQL | Requires Graphviz |
| Direct PDF/SVG/PNG output | Large schemas can be cluttered |
| Minimal setup | Limited layout control |
| Active maintenance (eralchemy2 fork) | |

---

### Option 2: sadisplay + Graphviz

SQLAlchemy-specific tool with more control over output formatting.

**Installation**:
```bash
pip install sadisplay
brew install graphviz
```

**Usage**:
```python
# generate_schema.py
import sadisplay
from sam.base import Base

# Generate from all models
desc = sadisplay.describe(Base.metadata.tables.values())

# Write DOT file
with open('schema.dot', 'w') as f:
    f.write(sadisplay.dot(desc))

# Or PlantUML format
with open('schema.plantuml', 'w') as f:
    f.write(sadisplay.plantuml(desc))
```

```bash
# Convert DOT to PDF
dot -Tpdf schema.dot -o schema.pdf

# Convert DOT to SVG
dot -Tsvg schema.dot -o schema.svg
```

| Pros | Cons |
|------|------|
| Fine-grained control over included tables | Two-step process |
| Can filter/exclude tables | More code required |
| PlantUML output option | ORM only (no database introspection) |

**Filtering Example**:
```python
# Include only specific tables
tables = [t for t in Base.metadata.tables.values()
          if t.name.startswith('project') or t.name.startswith('account')]
desc = sadisplay.describe(tables)
```

---

### Option 3: SchemaCrawler

Java-based comprehensive database documentation tool. Best for database-side diagrams with detailed metadata.

**Installation**:
```bash
# macOS
brew install schemacrawler

# Or download from https://www.schemacrawler.com/
```

**Usage**:
```bash
schemacrawler \
  --server=mysql \
  --host=127.0.0.1 \
  --port=3306 \
  --database=sam \
  --user=root \
  --password=root \
  --command=schema \
  --outputformat=pdf \
  --outputfile=database_schema.pdf
```

| Pros | Cons |
|------|------|
| Most comprehensive database introspection | Java dependency |
| Professional output quality | SQL only (no ORM awareness) |
| Includes indexes, constraints, comments | More complex setup |
| Multiple output formats | |

---

### Option 4: dbdiagram.io / dbdocs.io

Online service - export schema to DBML format, render via web UI.

**Usage**:
1. Export schema to DBML format (manually or via script)
2. Upload to https://dbdiagram.io
3. Export as PDF/PNG

```sql
-- Example DBML format
Table users {
  user_id int [pk]
  username varchar
  created_time timestamp
}

Table projects {
  project_id int [pk]
  projcode varchar
  project_lead_user_id int [ref: > users.user_id]
}
```

| Pros | Cons |
|------|------|
| Nice web UI | Manual sync required |
| Shareable links | External service dependency |
| Collaborative editing | Privacy considerations |

---

## Recommended Approach

**eralchemy2 + Makefile** provides the best balance of simplicity and maintainability.

### Directory Structure
```
sam-queries/
├── diagrams/           # Generated diagrams (gitignored)
│   ├── orm.pdf
│   ├── orm.svg
│   ├── database.pdf
│   └── database.svg
├── Makefile            # Or scripts/generate_diagrams.sh
└── ...
```

### Makefile Implementation

```makefile
# Add to existing Makefile or create new one

.PHONY: diagrams clean-diagrams

DIAGRAMS_DIR := diagrams
DB_URL := mysql+pymysql://root:root@127.0.0.1/sam
ORM_BASE := sam.base.Base

# Create diagrams directory
$(DIAGRAMS_DIR):
	mkdir -p $(DIAGRAMS_DIR)

# Generate ORM diagram from SQLAlchemy models
$(DIAGRAMS_DIR)/orm.pdf: $(DIAGRAMS_DIR)
	cd python && eralchemy2 -i '$(ORM_BASE)' -o ../$(DIAGRAMS_DIR)/orm.pdf

$(DIAGRAMS_DIR)/orm.svg: $(DIAGRAMS_DIR)
	cd python && eralchemy2 -i '$(ORM_BASE)' -o ../$(DIAGRAMS_DIR)/orm.svg

# Generate database diagram from live MySQL
$(DIAGRAMS_DIR)/database.pdf: $(DIAGRAMS_DIR)
	eralchemy2 -i '$(DB_URL)' -o $(DIAGRAMS_DIR)/database.pdf

$(DIAGRAMS_DIR)/database.svg: $(DIAGRAMS_DIR)
	eralchemy2 -i '$(DB_URL)' -o $(DIAGRAMS_DIR)/database.svg

# Generate all diagrams
diagrams: $(DIAGRAMS_DIR)/orm.pdf $(DIAGRAMS_DIR)/orm.svg \
          $(DIAGRAMS_DIR)/database.pdf $(DIAGRAMS_DIR)/database.svg
	@echo "Diagrams generated in $(DIAGRAMS_DIR)/"

# Clean generated diagrams
clean-diagrams:
	rm -rf $(DIAGRAMS_DIR)
```

### Shell Script Alternative

```bash
#!/bin/bash
# scripts/generate_diagrams.sh

set -e

DIAGRAMS_DIR="diagrams"
DB_URL="mysql+pymysql://root:root@127.0.0.1/sam"

mkdir -p "$DIAGRAMS_DIR"

echo "Generating ORM diagram..."
cd python
eralchemy2 -i 'sam.base.Base' -o "../$DIAGRAMS_DIR/orm.pdf"
eralchemy2 -i 'sam.base.Base' -o "../$DIAGRAMS_DIR/orm.svg"
cd ..

echo "Generating database diagram..."
eralchemy2 -i "$DB_URL" -o "$DIAGRAMS_DIR/database.pdf"
eralchemy2 -i "$DB_URL" -o "$DIAGRAMS_DIR/database.svg"

echo "Done! Diagrams saved to $DIAGRAMS_DIR/"
ls -la "$DIAGRAMS_DIR"
```

---

## Automation Options

### Option A: On-Demand (Simplest)

Run manually when needed:
```bash
make diagrams
# or
./scripts/generate_diagrams.sh
```

**Best for**: Infrequent schema changes, manual review process

---

### Option B: Pre-commit Hook

Auto-regenerate when ORM files change:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: generate-diagrams
        name: Regenerate schema diagrams
        entry: make diagrams
        language: system
        files: ^src/sam/.*\.py$
        pass_filenames: false
```

**Best for**: Ensuring diagrams stay in sync with code

---

### Option C: CI/CD Job

Generate diagrams on merge/release:

```yaml
# .github/workflows/diagrams.yml
name: Generate Schema Diagrams

on:
  push:
    branches: [main]
    paths:
      - 'src/sam/**/*.py'

jobs:
  diagrams:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install dependencies
        run: |
          sudo apt-get install -y graphviz
          pip install eralchemy2 pymysql

      - name: Generate diagrams
        run: make diagrams

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: schema-diagrams
          path: diagrams/
```

**Best for**: Automated documentation, artifact storage

---

### Option D: pytest Integration

Regenerate during test runs (when schema validation tests pass):

```python
# tests/conftest.py
import pytest
import subprocess

@pytest.fixture(scope="session", autouse=True)
def generate_diagrams_on_success(request):
    """Regenerate diagrams after successful test run."""
    yield
    # Only run if all tests passed
    if request.session.testsfailed == 0:
        subprocess.run(["make", "diagrams"], check=True)
```

**Best for**: Ensuring diagrams match validated schema

---

## Handling Large Schemas

With 97 tables, the full diagram may be cluttered. Consider:

### 1. Domain-Specific Diagrams

Generate separate diagrams per domain:

```python
# scripts/generate_domain_diagrams.py
import sadisplay
from sam.base import Base

DOMAINS = {
    'core': ['users', 'organization', 'institution', 'email_address'],
    'projects': ['project', 'project_code', 'area_of_interest'],
    'accounting': ['account', 'account_user', 'allocation', 'allocation_type'],
    'activity': ['comp_job', 'hpc_activity', 'dav_activity', 'disk_activity'],
    'resources': ['resources', 'resource_type', 'machine', 'queue', 'facility'],
}

for domain, tables in DOMAINS.items():
    filtered = [t for t in Base.metadata.tables.values() if t.name in tables]
    desc = sadisplay.describe(filtered)
    with open(f'diagrams/{domain}.dot', 'w') as f:
        f.write(sadisplay.dot(desc))
```

### 2. Graphviz Layout Options

Control diagram layout:

```bash
# Different layout engines
dot -Tpdf schema.dot -o schema.pdf      # Hierarchical (default)
neato -Tpdf schema.dot -o schema.pdf    # Spring model
fdp -Tpdf schema.dot -o schema.pdf      # Force-directed
circo -Tpdf schema.dot -o schema.pdf    # Circular
```

### 3. eralchemy2 Table Exclusion

Exclude specific tables:

```bash
eralchemy2 -i 'sam.base.Base' -o orm.pdf \
  --exclude-tables 'alembic_version,django_*'
```

---

## Prerequisites Checklist

Before generating diagrams, ensure:

- [ ] **Graphviz installed**: `brew install graphviz` or `apt-get install graphviz`
- [ ] **Python packages**: `pip install eralchemy2 pymysql`
- [ ] **Database accessible**: MySQL running at `127.0.0.1:3306`
- [ ] **PYTHONPATH set**: Can import `sam.base.Base`

### Quick Test

```bash
# Verify Graphviz
dot -V

# Verify eralchemy2
python -c "import eralchemy2; print('OK')"

# Verify ORM import
cd python && python -c "from sam.base import Base; print(f'{len(Base.metadata.tables)} tables')"
```

---

## Troubleshooting

### "No module named 'sam'"

Set PYTHONPATH or run from python directory:
```bash
cd python && eralchemy2 -i 'sam.base.Base' -o ../diagrams/orm.pdf
```

### "Graphviz not found"

Install Graphviz system package:
```bash
brew install graphviz      # macOS
apt-get install graphviz   # Linux
```

### "Connection refused" (database diagram)

Ensure MySQL is running and credentials are correct:
```bash
mysql -u root -h 127.0.0.1 -proot sam -e "SELECT 1"
```

### Diagram too large/cluttered

Use domain-specific diagrams (see "Handling Large Schemas" above) or adjust DPI:
```bash
dot -Tpdf -Gdpi=72 schema.dot -o schema.pdf
```

---

## Future Enhancements

- [ ] Add to `.gitignore`: `diagrams/*.pdf`, `diagrams/*.svg`
- [ ] Create domain-specific diagram generation
- [ ] Add diagram generation to CI pipeline
- [ ] Consider interactive viewer (D3.js, Mermaid)
