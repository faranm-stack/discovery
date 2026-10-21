#!/usr/bin/env bash
set -euo pipefail
results="${1:?results directory required}"
variant="${2:?variant required}"
checks=(build-prepare build-run identity-prepare identity-run tests-prepare tests-run
  public-prepare public-run e2e-prepare e2e-run gate-controls)
if [[ "$variant" == combined-node26 ]]; then
  checks+=(negative-loader negative-drift)
elif [[ "$variant" != base-control ]]; then
  echo "Unknown validation variant: $variant" >&2
  exit 1
fi
failed=0
for check in "${checks[@]}"; do
  file="$results/$check.exit"
  if [[ ! -f "$file" ]]; then
    echo "FAIL $check: missing receipt" >&2
    failed=1
  elif [[ "$(cat "$file")" != 0 ]]; then
    echo "FAIL $check: exit $(cat "$file")" >&2
    failed=1
  fi
done
if [[ "$failed" != 0 ]]; then exit 1; fi
echo "PASS: ${#checks[@]} required receipts for $variant."
echo "Private-route conformance is reported separately; this gate does not approve live activation."
