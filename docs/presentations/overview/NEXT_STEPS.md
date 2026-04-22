# Next Steps: Enable `docs` Optional-Dependency Variant

## Context

The presentation uses [Quarto](https://quarto.org/) for rendering and
[Mermaid](https://mermaid.js.org/) for diagrams. It also benefits from
auto-generated assets (ER diagrams, dependency graphs) that require additional
Python tooling. These should live in a `docs` optional-dependency group in
`pyproject.toml`, analogous to the existing `test` and `dev` groups.

---

## 1. `pyproject.toml` — Add `docs` group

Add the following block to `[project.optional-dependencies]`:

```toml
docs = [
    "samuel[test]",        # prerequisite — see rationale below
    "eralchemy2",          # ER diagram generation from SQLAlchemy metadata or live DB
    "pydeps",              # Python module dependency graph → PNG/SVG for architecture slides
]
```

Update `dev` to include `docs`:

```toml
dev = ["samuel[test,docs]", "jupyterlab", "pandas", "pre-commit"]
```

### Why `test` is a prerequisite

`eralchemy2` can generate ER diagrams two ways:

1. **From a live DB connection** (most complete — includes views, indexes, FK rendering):
   ```bash
   eralchemy2 -i 'mysql+pymysql://root:root@127.0.0.1:3307/sam' -o schema.pdf
   ```
   This requires the test container (`docker compose --profile test up -d mysql-test`)
   and the `SAM_TEST_DB_URL` env var — both set up as part of the `test` workflow.

2. **From SQLAlchemy metadata alone** (no DB required):
   ```python
   from sam import Base
   eralchemy2.render_er(Base, 'schema.pdf')
   ```
   This still requires the `samuel` package importable with its full dependency
   tree, which `samuel[test]` guarantees.

Declaring `samuel[test]` as a prerequisite ensures contributors who install
`[docs]` always have a working, database-connected environment — the most
useful state for generating accurate presentation assets.

---

## 2. `conda-env.yaml` — Add Quarto binary

Quarto is **not a pip package** — it's a standalone binary distributed via
`conda-forge`. Add it to `conda-env.yaml`:

```yaml
name: sam_sql
channels:
  - conda-forge
dependencies:
  - python
  - mysql
  - mysql-connector-python
  - coreutils
  - quarto          # <-- add this
  - pip
  - pip:
      - pipdeptree
```

> **Alternative (non-conda):** Download the Quarto installer directly from
> <https://quarto.org/docs/get-started/> if you prefer not to use conda-forge.

---

## 3. Install workflow (after making the changes above)

```bash
# 1. Recreate the conda environment with Quarto
conda env update -f conda-env.yaml --prune

# 2. Install Python deps including new docs group
source etc/config_env.sh
pip install -e ".[test,docs]"

# 3. Verify Quarto is available
quarto check

# 4. Verify eralchemy2 and pydeps
python -c "import eralchemy2, pydeps; print('ok')"
```

---

## 4. Optional: add ER diagram generation to the Makefile

Once the above is in place, extend `docs/presentations/overview/Makefile`:

```makefile
# Generate ER diagram from live test DB (requires docker compose --profile test up)
assets/schema.png:
	eralchemy2 \
	    -i "$$SAM_TEST_DB_URL" \
	    -o assets/schema.png \
	    --include-tables users,project,account,allocation,resources,machine,queue

# Generate Python module dependency graph
assets/pydeps.svg:
	pydeps src/sam --max-bacon 3 --cluster -o assets/pydeps.svg --noshow
```

Then reference these in `overview.qmd` as static images:

```markdown
![SAM Database Schema](assets/schema.png)
```

---

## 5. NCAR PPTX template

Drop the official NCAR/CISL PowerPoint template into:

```
docs/presentations/overview/assets/ncar-template.pptx
```

The `_quarto.yml` and `overview.qmd` front matter already reference this path.
Add `assets/*.pptx` to `.gitignore` if the template is proprietary and should
not be committed.

---

## Summary checklist

- [ ] Add `docs` group to `[project.optional-dependencies]` in `pyproject.toml`
- [ ] Update `dev` to `["samuel[test,docs]", ...]`
- [ ] Add `quarto` to `conda-env.yaml` under conda dependencies
- [ ] Run `conda env update` + `pip install -e ".[test,docs]"` to verify
- [ ] Run `quarto check` to confirm Mermaid/Knitr engines are available
- [ ] Drop NCAR PPTX template into `assets/ncar-template.pptx`
- [ ] Optionally extend `Makefile` with `assets/schema.png` and `assets/pydeps.svg` targets
- [ ] `cd docs/presentations/overview && make pptx` — first full render
