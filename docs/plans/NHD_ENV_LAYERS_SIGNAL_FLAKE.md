# Handoff: the `test_nhd_env_layers` signal flake

**Status:** diagnostics landed 2026-11-05, cause still uncaptured. The test now fails with the lane's return code, the leftover file names and its stderr tail, and names a held pipe when `communicate` times out. 12 runs of the file alone under 8 workers (192 signal cases) did not reproduce it; it needs the whole suite's load. Next: paste the first full-run failure here.
**Kickoff:** "Read docs/plans/NHD_ENV_LAYERS_SIGNAL_FLAKE.md and make the signal test deterministic
without weakening what it proves. Branch from `origin/staging`, one PR."

## What fails

`tests/unit/gates/test_nhd_env_layers.py::TestMergedCopyRemoved::test_after_a_signal_to_the_process_group`,
parametrized over SIGTERM, SIGINT and SIGHUP. Seen on the external-contacts branch on 2026-11-05: `[15]` on one
full parallel run (`pytest`, `-n auto`), `[2]` on the next. Each time it passed alone (`pytest -n 0` on the
file, 296 passed) and on the base commit. The failing assertion was not captured: capture it first.

## What the test proves

`nhd_exec` (`containers/ncar-hpc-deploy/libexec/lane.sh:74-78`) writes the merged env layers (secrets
included) to `mktemp $TMPDIR/nhd-env.XXXXXX`, then runs `apptainer` as a child, never `exec`, so its
traps stay live: `EXIT` removes the file, and HUP/INT/TERM exit 129/130/143 so `EXIT` runs. The test
starts the lane in its own session with a fake `apptainer` that prints `READY` on stderr and sleeps
30 s. It checks that one `nhd-env.*` file exists, then `os.killpg`s the group, waits on `communicate(20)`,
and polls 5 s for the file to vanish.

## Candidate causes (unverified)

1. **The signal lands before the trap.** `READY` comes from the fake apptainer, which starts only after
   the traps are set, so this is unlikely. Confirm it anyway from the failure text: a nonzero
   `_leftovers` versus a `communicate` timeout.
2. **The 5 s poll under xdist load.** bash runs a trap only after its foreground child returns. The
   child is `sleep` inside the fake apptainer; the group signal kills it too, but a loaded runner can
   delay the reap. The assertion would then see the file still present.
3. **`readline()` blocking.** If the fake ever fails before printing `READY`, the test hangs, not fails;
   that rules it out unless the failure was a timeout.

## Next steps

- Reproduce under load and keep the traceback:
  `for i in $(seq 20); do pytest -q -n 8 tests/unit/gates/test_nhd_env_layers.py || break; done`.
- If it is (2): wait on the process's exit status (`p.wait`) and assert the return code (129/130/143)
  before checking leftovers, and drop the fixed 5 s poll for a bounded wait keyed to the exit.
- If it is a real ordering hole in `lane.sh`, fix the script, not the test: the file holds secrets.
- Keep it in the gates directory and keep the three signals.
