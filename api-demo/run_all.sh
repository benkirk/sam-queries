#!/usr/bin/env bash
# Run every example and report which failed: the smoke test for this tree.
#
#   ./run_all.sh                 every family, bash then Python
#   ./run_all.sh rest xras       only these families
#   ./run_all.sh --bash-only     (or --python-only)
#   ./run_all.sh -v              show each example's output, not just pass/fail
#
# An example fails when a call returns an HTTP error or a response is not the
# shape it reads. Exit status: 0 when all pass, 1 when any fails.
set -uo pipefail
cd "$(dirname "$0")" || exit 2

FAMILIES=(rest systems-integration ldapsync xras)
kinds=(sh py)
verbose=0
selected=()
for arg in "$@"; do
    case $arg in
        --bash-only)   kinds=(sh) ;;
        --python-only) kinds=(py) ;;
        -v|--verbose)  verbose=1 ;;
        -h|--help)     sed -n '2,10p' "$0"; exit 0 ;;
        *)             selected+=("$arg") ;;
    esac
done
[[ ${#selected[@]} -gt 0 ]] && FAMILIES=("${selected[@]}")

failed=()
for family in "${FAMILIES[@]}"; do
    for kind in "${kinds[@]}"; do
        for script in "$family"/*."$kind"; do
            [[ -e $script ]] || continue
            out=$(mktemp)
            if [[ $kind == py ]]; then python3 "$script" >"$out" 2>&1; else bash "$script" >"$out" 2>&1; fi
            status=$?
            if [[ $status -eq 0 ]]; then
                echo "PASS  $script"
                [[ $verbose -eq 1 ]] && sed 's/^/      /' "$out"
            else
                echo "FAIL  $script (exit $status)"
                sed 's/^/      /' "$out" | tail -25
                failed+=("$script")
            fi
            rm -f "$out"
        done
    done
done

echo
if [[ ${#failed[@]} -eq 0 ]]; then
    echo "All examples passed against $SAM_API_BASE."
else
    echo "${#failed[@]} failed: ${failed[*]}"
    exit 1
fi
