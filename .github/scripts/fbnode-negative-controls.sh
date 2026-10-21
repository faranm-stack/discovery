#!/usr/bin/env bash
set -euo pipefail
results="$RUNNER_TEMP/results"
src="$RUNNER_TEMP/source"
printf '1\n' > "$results/negative-loader.exit"
printf '1\n' > "$results/negative-drift.exit"

dockerfile="$results/Dockerfile.missing-libatomic"
sed '/^COPY --from=node-runtime .*libatomic\.so\.1/d' \
  "$src/tools/flowblind-prepare/Dockerfile" > "$dockerfile"
! grep -q '^COPY .*libatomic' "$dockerfile"
grep -q '^RUN node --version$' "$dockerfile"
set +e
timeout --signal=TERM --kill-after=15s 180s docker build --platform linux/amd64 \
  --progress=plain --file "$dockerfile" "$src/tools/flowblind-prepare" \
  > "$results/negative-loader.log" 2>&1
code=$?
set -e
if [[ "$code" != 1 ]] || ! grep -q 'error while loading shared libraries: libatomic.so.1' "$results/negative-loader.log"; then
  echo "Expected the missing-library build to fail at the Node loader; got exit $code." >&2
  tail -n 25 "$results/negative-loader.log"
  exit 1
fi
printf '0\n' > "$results/negative-loader.exit"
echo "Missing-libatomic build was rejected before publication."

node_ref="$(sed -n 's/^FROM \(node:[^ ]*\) AS node-runtime$/\1/p' "$src/tools/flowblind-prepare/Dockerfile")"
[[ -n "$node_ref" ]]
set +e
timeout --signal=TERM --kill-after=15s 180s docker run --rm --platform linux/amd64 \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 256 --memory 3g --cpus 2 --user 1000:1000 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777 \
  --tmpfs /work:rw,noexec,nosuid,nodev,size=1g,mode=1777 \
  --mount "type=bind,src=$src,dst=/source,readonly" \
  --env HOME=/tmp --env CI=true --env NO_COLOR=1 \
  --entrypoint /bin/sh "$node_ref" -ec '
    cp -R /source /work/package
    cd /work/package
    printf "\n# intentional outer recipe drift\n" >> tools/flowblind-prepare/Dockerfile
    node --test --test-reporter=tap --test-name-pattern="validate package mirrors and confinement metadata" tests/portable-runtime.node-test.js
  ' > "$results/negative-drift.log" 2>&1
code=$?
set -e
if [[ "$code" != 1 ]] || ! grep -q 'Dockerfile must match its attested package copy' "$results/negative-drift.log"; then
  echo "Expected the recipe-drift regression assertion to fail; got exit $code." >&2
  tail -n 40 "$results/negative-drift.log"
  exit 1
fi
[[ "$(awk '$1=="#"&&$2=="fail"{n=$3}END{print n}' "$results/negative-drift.log")" == 1 ]]
printf '0\n' > "$results/negative-drift.exit"
echo "Outer Dockerfile drift was rejected by the targeted package regression test."
