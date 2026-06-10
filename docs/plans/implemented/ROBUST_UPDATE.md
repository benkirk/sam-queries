# Robust conda-env rebuild via hashed dir + symlink swap

## Context

Today, after `git pull`, `source etc/config_env.sh` calls
`make conda-env`. The Makefile pattern rule (lines 53–60) treats
`./conda-env/` as a real directory: if `conda-env.yaml` or
`pyproject.toml` is newer than the env dir, the rule moves it to
`conda-env.old`, removes it, and **rebuilds from scratch** — a
multi-minute window during which the env doesn't exist and any other
terminal pointed at `./conda-env/bin/...` breaks. Two other gaps:

1. **No `HPC_USAGE_QUERIES_REF` plumbing.** Containers honor it
   (`compose.yaml:15-16,99-100`, `containers/webapp/Dockerfile:32-38`),
   but the Makefile hardcodes `git+https://.../hpc-usage-queries.git`
   without a `@ref`. Local envs are always on `main`, even when CI
   containers test against a peer branch.
2. **No way to share/cache envs across branch toggles.** Every checkout
   that mutates `conda-env.yaml` or `pyproject.toml` blows the env away.

### Goals

- Build new envs into `./conda-env-<sha12>/` and make `./conda-env` an
  **atomically-swappable symlink** to the active one.
- Old env stays usable on disk during the build (other terminals keep
  working). Failed builds don't leave a broken state.
- Rebuild trigger keys off a **content hash** of `conda-env.yaml` +
  `pyproject.toml` + `HPC_USAGE_QUERIES_REF` — so toggling branches /
  refs hits a cached env when one exists.
- Retain last 2 hashed envs (current + previous) for fast rollback;
  prune older in the background.
- `HPC_USAGE_QUERIES_REF` defaults to `main`, env-var override only
  (mirrors container convention; no `.env` coupling).

## Approach

### 1. Makefile — replace the `%: %.yaml pyproject.toml` pattern rule

Rewrite the conda-env target (Makefile:53–60) as an explicit rule whose
**name is the symlink** and whose **prerequisites are stamp files
keyed by hash**. Rough shape:

```make
HPC_USAGE_QUERIES_REF ?= main

# Hash inputs: env spec + pyproject + hpc ref. 12-char sha is plenty.
ENV_HASH := $(shell { cat conda-env.yaml pyproject.toml; \
                      echo "HPC_USAGE_QUERIES_REF=$(HPC_USAGE_QUERIES_REF)"; \
                    } | shasum -a 256 | cut -c1-12)
ENV_PREFIX := conda-env-$(ENV_HASH)
ENV_STAMP  := $(ENV_PREFIX)/.sam-env-ready

.PHONY: conda-env
conda-env: $(ENV_STAMP)
	@# Ensure the ./conda-env symlink points at the current hashed env.
	@current=$$(readlink conda-env 2>/dev/null || true); \
	if [ "$$current" != "$(ENV_PREFIX)" ]; then \
	    ln -snfv $(ENV_PREFIX) conda-env; \
	    $(MAKE) --silent prune-old-envs & \
	fi

$(ENV_STAMP):
	@echo ">>> Building $(ENV_PREFIX) (hpc-usage-queries ref: $(HPC_USAGE_QUERIES_REF))"
	@# Clear any half-built dir from a prior interrupted run for this same hash.
	@rm -rf $(ENV_PREFIX)
	$(config_env) && \
	    conda env create --file conda-env.yaml --prefix $(ENV_PREFIX) && \
	    conda activate ./$(ENV_PREFIX) && \
	    pip install -e ".[test]" && \
	    pip install "hpc-usage-queries[postgres] @ \
	        git+https://github.com/benkirk/hpc-usage-queries.git@$(HPC_USAGE_QUERIES_REF)" && \
	    pipdeptree --all 2>/dev/null || true
	@touch $(ENV_STAMP)

.PHONY: prune-old-envs
prune-old-envs: ## Keep the current + most-recent-previous conda-env-*; remove older
	@current=$$(readlink conda-env 2>/dev/null); \
	ls -1dt conda-env-* 2>/dev/null \
	    | grep -v "^$$current$$" \
	    | tail -n +2 \
	    | xargs -r rm -rf
```

Key properties:

- **Content-addressed cache, not mtime-driven.** The stamp-file rule
  has **no source-file prereqs** — the hash *is* the cache key. So
  `touch conda-env.yaml` (mtime bump, no content change) computes the
  same `$(ENV_HASH)`, finds the stamp present, and is a no-op. A real
  edit changes the hash → new `$(ENV_PREFIX)` path → stamp absent →
  build runs. This avoids the trap where mtime-based prereqs would
  fire `conda env create --prefix` against an already-existing dir.
- **Build into a fresh dir.** If `$(ENV_PREFIX)` already exists (cached
  from a prior branch), the stamp is present and we skip straight to
  the symlink check — near-instant. The `rm -rf $(ENV_PREFIX)` inside
  the build recipe only runs when the stamp is *missing*, cleaning up
  any half-built dir from an interrupted prior run with the same hash.
- **Atomic swap.** `ln -snf` uses `rename(2)`; the symlink either
  points at the old or the new env, never an inconsistent state.
- **`pip install -e` works unchanged.** Editable installs write
  absolute paths to `src/` into the new env's site-packages; each
  hashed env gets its own editable record. No symlink trickery needed.
- **Old env remains live** for any shell that activated it before the
  swap (conda resolves `CONDA_PREFIX` to the real path at activation
  time, so existing sessions are unaffected by the relink).
- **Pruning is async** — `&` returns the shell immediately; `ls -t`
  keeps the newest non-current entry and drops the rest.

### 2. `clobber` / `distclean` (Makefile:40–45)

Update to handle the new layout:

```make
clobber:    git clean -xdf --exclude ".env" --exclude "conda-env*"
distclean:  $(MAKE) clobber && rm -rf conda-env conda-env-*
```

### 3. `etc/config_env.sh` — pass the ref through

No structural change needed; just preserve the env var when invoking
make so the user pattern works:

```bash
HPC_USAGE_QUERIES_REF=my-branch source etc/config_env.sh
```

Change line 35 to:

```bash
make --silent -C "${ROOT_DIR}" HPC_USAGE_QUERIES_REF="${HPC_USAGE_QUERIES_REF:-main}" ${ENV_NAME}
```

(Exporting it before the `make` call would also work, but explicit
arg-passing keeps it discoverable in the make output.)

`conda activate ${ENV_DIR}` (line 37) needs no change — conda
dereferences the symlink and sets `CONDA_PREFIX` to the resolved
hashed path. Re-sourcing the script after a rebuild picks up the new
target automatically.

### 4. Pattern rule for other yamls

The original generic rule (`%: %.yaml pyproject.toml`) supported other
env names like `make some-other-env`. There don't appear to be any
callers of that — `conda-env` is the only env in the repo and CI
(`.github/workflows/sam-ci-conda_make.yaml:59`) hard-codes
`./conda-env/`. Drop the generic pattern; if a second env spec
appears later, parameterize then.

The `solve-%` dry-run rule (Makefile:62–63) is independent and stays.

## Files to modify

- `Makefile` — replace the env pattern rule (lines 53–60), update
  `clobber` and `distclean` (lines 40–45). Add `HPC_USAGE_QUERIES_REF`
  default, hash computation, stamp-file rule, and `prune-old-envs`.
- `etc/config_env.sh` — line 35 only: pass `HPC_USAGE_QUERIES_REF`
  through to `make`.

No changes needed in `pyproject.toml`, `compose.yaml`, the Dockerfile,
or any CI workflow — the symlink is transparent to conda activation
and PEP 508 doesn't support env-var substitution in pyproject extras
anyway (the Makefile already does the parameterized install
separately).

## Verification

1. **Fresh build (no env present)**:
   ```bash
   rm -rf conda-env conda-env-*
   source etc/config_env.sh
   # Expect: builds conda-env-<sha>/, symlinks ./conda-env, activates,
   # `python -c "import sam; import job_history"` works.
   ls -la conda-env  # should be a symlink
   ```

2. **No-op rebuild (hash unchanged)**:
   ```bash
   source etc/config_env.sh   # second run
   # Expect: stamp present, symlink correct → make does nothing.
   # End-to-end should complete in <1s for the make portion.
   ```

3. **Hash-triggered rebuild via HPC ref**:
   ```bash
   HPC_USAGE_QUERIES_REF=some-branch source etc/config_env.sh
   # Expect: new conda-env-<sha>/ built, symlink swapped,
   # `pip show hpc-usage-queries` shows the branch's commit.
   readlink conda-env   # new hashed dir
   ls -d conda-env-*    # current + previous retained
   ```

4. **Hash-triggered rebuild via yaml change**:
   Make a real content change to `conda-env.yaml` (add a dep), re-source.
   Verify a new hashed env is created and symlink updated.

5. **`touch` is a no-op (mtime ≠ hash)**:
   ```bash
   touch conda-env.yaml
   time (source etc/config_env.sh)   # expect <1s, no rebuild
   ```
   Confirms the stamp rule has no mtime-based prereqs and the content
   hash drives caching.

6. **Old-env survival during build**: In terminal A, activate the env
   and start a long-running `python -i` session. In terminal B, force
   a rebuild (mutate yaml). Confirm terminal A's Python session keeps
   working throughout the rebuild and after the swap (it still holds
   the old `CONDA_PREFIX`).

7. **Prune retention**: After 3 rebuilds with 3 different refs,
   `ls -d conda-env-*` should show exactly 2 directories (current +
   most-recent previous).

8. **CI sanity**: Confirm `.github/workflows/sam-ci-conda_make.yaml`
   still passes — its `activate-environment: ./conda-env/` step
   dereferences the symlink transparently.
