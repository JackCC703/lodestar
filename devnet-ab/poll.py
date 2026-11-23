#!/usr/bin/env python3
"""Poll light client REST endpoints of several beacon nodes and compare what they serve.

Usage:
  python3 poll.py --duration 1800 --interval 2 \
      --node unstable=http://127.0.0.1:33000 --node finality=http://127.0.0.1:33005 \
      --node nimbus=http://127.0.0.1:33010

Writes one JSON line per node per tick to results.jsonl, and prints a summary.
"""
import argparse
import hashlib
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

ZERO32 = "0x" + "00" * 32


def get(url, accept=None, timeout=5):
    req = urllib.request.Request(url)
    if accept:
        req.add_header("Accept", accept)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as res:
        return res.read()


def get_json(url):
    return json.loads(get(url))


def node_sample(base, slots_per_period):
    """Return one observation of what this node currently serves."""
    head = get_json(f"{base}/eth/v1/beacon/headers/head")
    head_slot = int(head["data"]["header"]["message"]["slot"])

    fin = get_json(f"{base}/eth/v1/beacon/states/head/finality_checkpoints")
    fin_epoch = int(fin["data"]["finalized"]["epoch"])

    sample = {
        "head_slot": head_slot,
        "finalized_epoch": fin_epoch,
        "finality_update": None,
        "best_update": None,
        "errors": [],
    }

    try:
        raw = get(f"{base}/eth/v1/beacon/light_client/finality_update", accept="application/octet-stream")
        sample["finality_update"] = {
            "ssz": raw.hex(),
            "json": get_json(f"{base}/eth/v1/beacon/light_client/finality_update")["data"],
        }
    except urllib.error.HTTPError as e:
        sample["errors"].append(f"finality_update {e.code}")

    period = head_slot // slots_per_period
    try:
        raw = get(
            f"{base}/eth/v1/beacon/light_client/updates?start_period={period}&count=1",
            accept="application/octet-stream",
        )
        sample["best_update"] = {"period": period, "ssz": raw.hex()}
    except urllib.error.HTTPError as e:
        sample["errors"].append(f"updates {e.code}")

    return sample


def describe(sample):
    """Pull the numbers we compare out of a sample."""
    out = {
        "head_slot": sample["head_slot"],
        "finalized_epoch": sample["finalized_epoch"],
        "finalized_header_slot": None,
        "attested_slot": None,
        "branch_all_zero": None,
        "best_update_hash": None,
    }
    fu = sample.get("finality_update")
    if fu:
        data = fu["json"]
        out["finalized_header_slot"] = int(data["finalized_header"]["beacon"]["slot"])
        out["attested_slot"] = int(data["attested_header"]["beacon"]["slot"])
        out["branch_all_zero"] = all(b.lower() == ZERO32 for b in data["finality_branch"])
    bu = sample.get("best_update")
    if bu:
        out["best_update_hash"] = hashlib.sha256(bytes.fromhex(bu["ssz"])).hexdigest()[:16]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", action="append", required=True, help="name=url")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--duration", type=float, default=600.0)
    ap.add_argument("--slots-per-period", type=int, default=64, help="minimal 64, mainnet 8192")
    ap.add_argument("--out", default="results.jsonl")
    args = ap.parse_args()

    nodes = {}
    for spec in args.node:
        name, _, url = spec.partition("=")
        nodes[name] = url.rstrip("/")

    ticks = []
    deadline = time.time() + args.duration
    with open(args.out, "w") as f:
        while time.time() < deadline:
            tick = {"t": round(time.time(), 3), "nodes": {}}
            for name, url in nodes.items():
                try:
                    sample = node_sample(url, args.slots_per_period)
                except Exception as e:  # keep polling, a node may restart
                    tick["nodes"][name] = {"errors": [repr(e)]}
                    continue
                tick["nodes"][name] = {"raw": sample, "summary": describe(sample)}
            f.write(json.dumps(tick) + "\n")
            f.flush()
            ticks.append(tick)
            time.sleep(args.interval)

    # Summary
    per_node = {name: {"samples": 0, "stale_vs_best": 0, "genesis_empty": 0, "max_fin_slot": 0} for name in nodes}
    for tick in ticks:
        observed = {
            name: t["summary"]["finalized_header_slot"]
            for name, t in tick["nodes"].items()
            if "summary" in t and t["summary"]["finalized_header_slot"] is not None
        }
        if not observed:
            continue
        newest = max(observed.values())
        for name, slot in observed.items():
            s = per_node[name]
            s["samples"] += 1
            s["max_fin_slot"] = max(s["max_fin_slot"], slot)
            if slot < newest:
                s["stale_vs_best"] += 1
            if tick["nodes"][name]["summary"]["branch_all_zero"]:
                s["genesis_empty"] += 1

    print(f"\nticks={len(ticks)}")
    for name, s in per_node.items():
        pct = (100.0 * s["stale_vs_best"] / s["samples"]) if s["samples"] else 0.0
        print(
            f"{name:>10}: samples={s['samples']:5d} max_finalized_header_slot={s['max_fin_slot']:6d} "
            f"stale_vs_newest={s['stale_vs_best']:5d} ({pct:5.1f}%) all_zero_branch={s['genesis_empty']:5d}"
        )

    hashes = {}
    for tick in ticks:
        for name, t in tick["nodes"].items():
            h = t.get("summary", {}).get("best_update_hash")
            if h:
                hashes.setdefault(name, set()).add(h)
    print("\nbest update hashes per node:")
    for name, hs in hashes.items():
        print(f"{name:>10}: {len(hs)} distinct {sorted(hs)[:4]}")
    if len(hashes) > 1:
        common = set.intersection(*hashes.values())
        print(f"shared by all nodes: {len(common)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
