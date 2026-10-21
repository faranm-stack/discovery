#!/usr/bin/env bash
set -euo pipefail
root="$RUNNER_TEMP/gate-controls"
gate="$RUNNER_TEMP/harness/fbnode-result-gate.sh"
mkdir -p "$root"
checks=(build-prepare build-run identity-prepare identity-run tests-prepare tests-run
  public-prepare public-run e2e-prepare e2e-run gate-controls negative-loader negative-drift)
for check in "${checks[@]}"; do printf '0\n' > "$root/$check.exit"; done
bash "$gate" "$root" combined-node26
printf '127\n' > "$root/identity-run.exit"
set +e
bash "$gate" "$root" combined-node26
code=$?
set -e
[[ "$code" == 1 ]] || { echo "Gate accepted a failed runtime receipt" >&2; exit 1; }
printf '0\n' > "$root/identity-run.exit"
rm "$root/e2e-run.exit"
set +e
bash "$gate" "$root" combined-node26
code=$?
set -e
[[ "$code" == 1 ]] || { echo "Gate accepted a missing end-to-end receipt" >&2; exit 1; }
: > "$root/e2e-run.exit"
set +e
bash "$gate" "$root" combined-node26
code=$?
set -e
[[ "$code" == 1 ]] || { echo "Gate accepted an empty end-to-end receipt" >&2; exit 1; }
printf '0\n' > "$RUNNER_TEMP/results/gate-controls.exit"
echo "Known-good, failed, missing and empty receipt controls passed."
