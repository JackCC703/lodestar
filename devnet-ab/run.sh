#!/usr/bin/env bash
# Runs inside the codespace. Builds both Lodestar images, starts the devnet,
# polls the light client endpoints of every node, and prints the comparison.
set -euo pipefail

UPSTREAM_BRANCH=${UPSTREAM_BRANCH:-unstable}
PR_BRANCH=${PR_BRANCH:-jackcc/lc-finality-fix}
ENCLAVE=${ENCLAVE:-lc-ab}
WORKDIR=${WORKDIR:-/workspaces/lodestar}

cd "$WORKDIR"

echo "== native preset =="
# Dockerfile.dev must declare ARG LODESTAR_PRESET and export it before `pnpm build:native`,
# otherwise the addon is built for mainnet and the node fails with PresetMismatch.
grep -q "ARG LODESTAR_PRESET" Dockerfile.dev || { echo "Dockerfile.dev lacks ARG LODESTAR_PRESET"; exit 1; }

echo "== docker =="
docker info --format '{{.ServerVersion}} {{.NCPU}}cpu {{.MemTotal}}' || {
  echo "docker unavailable, aborting"; exit 1; }

echo "== kurtosis =="
command -v kurtosis >/dev/null || {
  echo "Installing kurtosis CLI"
  curl -fsSL https://docs.kurtosis.com/install.sh | bash
}

echo "== build unstable =="
git fetch origin "$UPSTREAM_BRANCH"
git checkout --detach "origin/$UPSTREAM_BRANCH"
docker build --build-arg LODESTAR_PRESET=minimal -t lodestar:unstable -f Dockerfile.dev .

echo "== build finality branch =="
git fetch origin "$PR_BRANCH"
git checkout --detach "origin/$PR_BRANCH"
docker build --build-arg LODESTAR_PRESET=minimal -t lodestar:finality -f Dockerfile.dev .

echo "== start devnet =="
kurtosis enclave rm -f "$ENCLAVE" 2>/dev/null || true
kurtosis run github.com/ethpandaops/ethereum-package \
  --enclave "$ENCLAVE" \
  --args-file network_params.yaml \
  --image-download always

echo "== wait for two sync committee periods =="
sleep 240

echo "== poll =="
python3 poll.py \
  --duration "${POLL_SECONDS:-1500}" \
  --interval 2 \
  --node "unstable=http://127.0.0.1:33000" \
  --node "finality=http://127.0.0.1:33005" \
  --node "nimbus=http://127.0.0.1:33010" \
  --out results.jsonl | tee summary.txt

echo "== done, results in results.jsonl and summary.txt =="
echo "cleanup: kurtosis enclave rm -f $ENCLAVE"
