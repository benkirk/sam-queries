# Shared helpers for helm/tests/*.sh. Lives under lib/ so the Makefile's
# `helm/tests/*.sh` glob never runs it as a test. Callers `set -euo pipefail`
# themselves, then:
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/assert.sh"
#   out=$(render dev -s templates/deployment.yaml)
#   assert_contains "$out" 'name: SAM_DB_NAME' "dev must pin the database"

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$TESTS_DIR"
CHART_DIR="${TESTS_DIR}/.."
RELEASE_NAME="samuel"

red()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

assert_contains() {
  local haystack="$1" needle="$2" msg="$3"
  if ! grep -qF -- "$needle" <<<"$haystack"; then
    red "FAIL: $msg"
    red "  expected to find: $needle"
    return 1
  fi
}

assert_not_contains() {
  local haystack="$1" needle="$2" msg="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    red "FAIL: $msg"
    red "  unexpectedly found: $needle"
    return 1
  fi
}

if ! command -v helm >/dev/null 2>&1; then
  red "FAIL: helm not found in PATH (needed for template rendering)"
  exit 1
fi

# render <prod|dev|local> [helm template args...]
# WARNING: `#` comments are stripped. helm renders YAML comments into its
# output, and an assertion has already been satisfied by the comment that
# explained it rather than by the key it was meant to prove.
render() {
  local which="$1"; shift
  local files=(-f "$CHART_DIR/values.yaml")
  case "$which" in
    prod)  ;;
    dev)   files+=(-f "$CHART_DIR/values-dev.yaml");;
    local) files+=(-f "$CHART_DIR/values-local.yaml");;
    *) red "render: unknown target '$which' (prod|dev|local)"; return 2;;
  esac
  helm template "$RELEASE_NAME" "$CHART_DIR" "${files[@]}" "$@" \
    | grep -v '^[[:space:]]*#'
}
