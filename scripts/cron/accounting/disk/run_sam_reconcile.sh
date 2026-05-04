#!/bin/bash
# Usage: run_ncar_accounting.sh --resource <Resource> --user-usage <file> [extra sam-admin args...]
# Runs sam-admin accounting --disk with env setup, passing all args through.
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

time sam-admin accounting --resource Campaign_Store --verify-paths --reconcile-quotas "$@"
