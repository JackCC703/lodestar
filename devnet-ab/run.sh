#!/usr/bin/env bash
# Runs inside the codespace. Builds both Lodestar images, starts the devnet,
# polls the light client endpoints of every node, and prints the comparison.
set -euo pipefail

UPSTREAM_BRANCH=${UPSTREAM_BRANCH:-unstable}
PR_BRANCH=${PR_BRANCH:-jackcc/lc-finality-fix}
ENCLAVE=${ENCLAVE:-lc-ab}
WORKDIR=${WORKDIR:-/workspaces/lodestar}
FILES="$(cd "$(dirname "$0")" && pwd)"
POLL_SECONDS=${POLL_SECONDS:-1500}

step() { echo; echo "=== $* ($(date +%H:%M:%S)) ==="; }

cd "$WORKDIR"

step "preflight"
grep -q "ARG LODESTAR_PRESET" Dockerfile.dev || { echo "Dockerfile.dev lacks ARG LODESTAR_PRESET"; exit 1; }
docker info --format 'docker {{.ServerVersion}} {{.NCPU}}cpu'
command -v kurtosis >/dev/null || {
  echo "installing kurtosis"
  curl -fsSL https://docs.kurtosis.com/install.sh | bash
}
export PATH="$PATH:$HOME/bin:$HOME/.local/bin"
kurtosis version | head -2

step "build unstable"
git fetch origin "$UPSTREAM_BRANCH"
git checkout --detach "origin/$UPSTREAM_BRANCH"
docker build --build-arg LODESTAR_PRESET=minimal -t lodestar:unstable -f Dockerfile.dev .

step "build finality branch"
git fetch origin "$PR_BRANCH"
git checkout --detach "origin/$PR_BRANCH"
docker build --build-arg LODESTAR_PRESET=minimal -t lodestar:finality -f Dockerfile.dev .

step "start devnet"
kurtosis enclave rm -f "$ENCLAVE" 2>/dev/null || true
kurtosis run github.com/ethpandaops/ethereum-package \
  --enclave "$ENCLAVE" \
  --args-file "$FILES/network_params.yaml" \
  --image-download always 2>&1 | tail -40

step "wait for genesis and two periods"
sleep 300
for p in 33000 33005 33010; do
  echo -n "port $p: "
  curl -s -m 5 "http://127.0.0.1:$p/eth/v1/node/version" || echo "no answer"
  echo
done

step "poll"
python3 "$FILES/poll.py" \
  --duration "$POLL_SECONDS" \
  --interval 2 \
  --node "unstable=http://127.0.0.1:33000" \
  --node "finality=http://127.0.0.1:33005" \
  --node "nimbus=http://127.0.0.1:33010" \
  --out "$FILES/results.jsonl" | tee "$FILES/summary.txt"

step "done"
echo "summary: $FILES/summary.txt"
echo "cleanup: kurtosis enclave rm -f $ENCLAVE"
