#!/usr/bin/env bash
#
# Emit the human-readable timestamp used to stamp container builds — the
# BUILD_DATE build-arg baked into images (surfaced on the admin server card and
# the build-summary page) and the cirrus helm-pin commit message.
#
# Mountain, not UTC: runners are UTC, but SAM is naive-Mountain throughout and
# the prod pod already runs TZ=America/Denver, so a Mountain stamp is the one
# that reads correctly alongside everything else.
#
# %Z resolves MST/MDT on its own — never hardcode an offset.
#
# Guarded, because the failure mode here is silent rather than loud: with no
# tzdata, glibc does not error. It returns UTC while printing a truncated zone
# name ("2026-07-15 America") — a wrong time wearing a plausible label, which
# is worse than the UTC we started with. So verify the zone actually resolved,
# and otherwise fall back to honest UTC and say so.
#
# The ubuntu-24.04 runner image ships tzdata, so the fallback should never
# fire; it exists so that if it ever does, the timestamp is not quietly lying.
#
# Usage:  BUILD_DATE="$(.github/scripts/build_stamp.sh)"
# The warning goes to stderr so it never contaminates the captured value.

set -euo pipefail

stamp="$(TZ=America/Denver date '+%Y-%m-%d %H:%M %Z')"

case "${stamp}" in
    *\ MST | *\ MDT) ;;
    *)
        echo "::warning::tzdata unavailable — stamping UTC instead of Mountain" >&2
        stamp="$(date -u '+%Y-%m-%d %H:%M UTC')"
        ;;
esac

printf '%s\n' "${stamp}"
