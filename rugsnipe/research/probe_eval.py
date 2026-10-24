"""Evaluate latency probes: where did our no-trade tx land relative to the create and to rival slot-0 buys?

usage: python3 probe_eval.py JOURNAL.jsonl [WALLET_PUBKEY]   (wallet defaults to ../wallet.json)

Decision rules (fixed before the runs they judge):
  ungated run (probe.json):       same-slot >= 60% and ahead of ALL slot-0 rivals in >= 40% of contested launches
  gated run (probe_gated.json, leader < 1200 km and create early in its slot):
                                  same-slot >= 85% and ahead of ALL rivals in >= 40% of >= 30 contested launches
Probes that reused an earlier probe's signature (byte-identical tx, same blockhash) are dropped: the network kept
only the first, so they say nothing about their own create.
"""
import asyncio
import collections
import json
import sys
from pathlib import Path

import base58
import numpy as np

from paths import LIVE
from pumpdecode import parse_tx
from rpc import Rpc


def wallet_pubkey() -> str:
    p = Path(__file__).resolve().parents[1] / "wallet.json"
    return base58.b58encode(bytes(json.load(open(p))[32:64])).decode()


def load_geo():
    try:
        return json.load(open(LIVE + "leaders.json"))["leaders"]
    except OSError:
        return None


async def measure(rpc, probes, wallet, geo):
    ours = {}
    async for t in rpc.txs_for_address(wallet, full=False, order="desc", limit=1000, max_pages=5):
        ours[t["signature"]] = (t["slot"], t.get("transactionIndex"), t.get("err"))
    sched = {}
    lo, hi = min(p["create_slot"] for p in probes), max(p["create_slot"] for p in probes) + 8
    for s in range(lo, hi + 1, 4000):
        for i, l in enumerate(await rpc.call("getSlotLeaders", [s, min(4000, hi + 1 - s)])):
            sched[s + i] = l
    out = []

    async def one(p):
        seq = []
        # succeeded only (failed attempts can be most of a hot launch's txs and would crowd out the rivals' buys)
        async for t in rpc.txs_for_address(p["mint"], order="asc", limit=100, max_pages=5, status="succeeded",
                                           slot_gte=p["create_slot"], slot_lt=p["create_slot"] + 8):
            ti = parse_tx(t)
            seq.append(dict(slot=ti.slot, idx=ti.tx_index or 0, create=any(n == "create" for n, _ in ti.events),
                            buy=any(n == "trade" and e["is_buy"] for n, e in ti.events), signer=ti.signer, ok=ti.ok))
        cr = next((x for x in seq if x["create"] and x["ok"]), None)
        me = ours.get(p["sig"])
        r = {"n": p["n"], "landed": me is not None, "create_found": cr is not None, "since_recv_ms": p["since_recv_ms"],
             "into_slot_ms": p.get("into_slot_ms"), "gate": p.get("gate")}
        out.append(r)
        if not (cr and me):
            return
        # dev bundle = contiguous txs (by in-block index) right after the create, in the create slot
        end = cr["idx"]
        for x in sorted((x for x in seq if x["slot"] == cr["slot"] and x["idx"] > cr["idx"]), key=lambda x: x["idx"]):
            if x["idx"] == end + 1:
                end = x["idx"]
            else:
                break
        rivals0 = [x for x in seq if x["slot"] == cr["slot"] and x["idx"] > end and x["buy"] and x["ok"]
                   and x["signer"] != p["deployer"]]
        leader = sched.get(cr["slot"])
        blk = await rpc.call("getBlock", [cr["slot"], {"transactionDetails": "signatures", "rewards": False,
                                                       "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
        r.update(slot_gap=me[0] - cr["slot"], rivals_slot0=len(rivals0), create_slot=cr["slot"], create_idx=cr["idx"],
                 block_pos=cr["idx"] / max(len(blk["signatures"]), 1), leader=leader,
                 leader_km=p["leader_km"] if p.get("leader") == leader and "leader_km" in p
                 else ((geo or {}).get(leader) or {}).get("km"))
        if me[0] == cr["slot"]:
            r["idx_gap"] = me[1] - cr["idx"]
            r["rivals_ahead"] = sum(1 for x in rivals0 if x["idx"] < me[1])
        else:
            r["rivals_ahead"] = len(rivals0) + sum(1 for x in seq if cr["slot"] < x["slot"] < me[0] and x["buy"]
                                                   and x["ok"] and x["signer"] != p["deployer"])

    await asyncio.gather(*(one(p) for p in probes))
    return sorted(out, key=lambda r: r["n"])


def stats(rows):
    rows = [r for r in rows if r.get("slot_gap") is not None]
    con = [r for r in rows if r["rivals_slot0"]]
    win = sum(1 for r in con if r["rivals_ahead"] == 0)
    same = sum(1 for r in rows if r["slot_gap"] == 0)
    return dict(n=len(rows), same=same / max(len(rows), 1), con=len(con), win=win, win_rate=win / max(len(con), 1))


def line(name, rows):
    s = stats(rows)
    if s["n"]:
        print(f"  {name:44s} n={s['n']:3d}  same-slot {s['same']:4.0%}  contested {s['con']:3d}  "
              f"ahead of all {s['win']:3d} ({s['win_rate']:4.0%})")
    return s


async def main():
    jpath = sys.argv[1]
    wallet = sys.argv[2] if len(sys.argv) > 2 else wallet_pubkey()
    probes = [p for p in (json.loads(l) for l in open(jpath)) if p.get("ev") == "probe_sent"]
    print(f"{len(probes)} probes in {jpath}; wallet {wallet}")
    if not probes:
        return
    seen, uniq = set(), []
    for p in sorted(probes, key=lambda p: p["n"]):
        if p["sig"] not in seen:
            seen.add(p["sig"])
            uniq.append(p)
    if len(uniq) < len(probes):
        print(f"duplicates: {len(probes) - len(uniq)} probes were byte-identical to an earlier one (same signature) -> dropped")
    send_ok = collections.Counter()
    for p in uniq:
        for s in p["send"]:
            send_ok[(s["name"], s["ok"])] += 1
    print("send results by path:", dict(send_ok))
    geo = load_geo()
    if geo is None:
        print(f"(no {LIVE}leaders.json: run research/leaders.py for the leader-distance split)")

    async with Rpc(rps=30, concurrency=8) as rpc:
        out = await measure(rpc, uniq, wallet, geo)

    landed = [r for r in out if r["landed"]]
    print(f"\nlanded {len(landed)}/{len(out)} probes; create never executed for {sum(1 for r in landed if not r['create_found'])}"
          " (a real buy there fails, ~0.0005 SOL)")
    rows = [r for r in landed if r.get("slot_gap") is not None]
    if not rows:
        print("VERDICT: no probe landed next to its create -> check senders/tips/balance (see data/logs/probe_stderr.log)")
        return
    print("landing slot - create slot:", dict(sorted(collections.Counter(r["slot_gap"] for r in rows).items())))
    same = [r for r in rows if r["slot_gap"] == 0]
    if same:
        print(f"in-block index after create (same slot): p50 {np.median([r['idx_gap'] for r in same]):.0f}")
    print("create seen -> sent (ms): p50 %.1f  p90 %.1f" % tuple(np.percentile([r["since_recv_ms"] for r in out], [50, 90])))

    print("\nby slot leader distance from this box:")
    for lo, hi, nm in [(0, 100, "< 100 km (same metro)"), (100, 1200, "100-1200 km (rest of Europe)"), (1200, 1e9, ">= 1200 km")]:
        line(nm, [r for r in rows if r["leader_km"] is not None and lo <= r["leader_km"] < hi])
    line("unknown location", [r for r in rows if r["leader_km"] is None])
    print("by create position in its block:")
    for lo, hi, nm in [(0, 1 / 3, "first third"), (1 / 3, 2 / 3, "middle third"), (2 / 3, 1.01, "last third")]:
        line(nm, [r for r in rows if lo <= r["block_pos"] < hi])
    if any(r["into_slot_ms"] is not None for r in rows):
        print("by time into slot when the create reached us:")
        for lo, hi, nm in [(0, 40, "0-40 ms"), (40, 80, "40-80 ms"), (80, 120, "80-120 ms"), (120, 1e9, ">= 120 ms")]:
            line(nm, [r for r in rows if r["into_slot_ms"] is not None and lo <= r["into_slot_ms"] < hi])
        line("superseded slot (-1)", [r for r in rows if r["into_slot_ms"] is not None and r["into_slot_ms"] < 0])

    gated_run = any(r["gate"] for r in rows)
    print()
    a = line("ALL", rows)
    if gated_run:
        ok = a["same"] >= 0.85 and a["con"] >= 30 and a["win_rate"] >= 0.40
        if a["con"] < 30:
            print(f"\nVERDICT: inconclusive ({a['con']} contested launches; need >= 30) -> run more gated probes")
        elif ok:
            print("\nVERDICT: gate confirmed -> next step is the micro-size live test (send me this output)")
        else:
            print("\nVERDICT: gate NOT confirmed -> do not trade rugger-sniping from this box")
    else:
        h = line("post-hoc gate (leader<1200 km, early create)", [r for r in rows if r["leader_km"] is not None
                                                                         and r["leader_km"] < 1200 and r["block_pos"] < 0.5])
        ok = a["same"] >= 0.60 and a["con"] >= 10 and a["win_rate"] >= 0.40
        if a["con"] < 10:
            print(f"\nVERDICT: inconclusive ({a['con']} contested launches; need >= 10) -> run more probes")
        elif ok:
            print("\nVERDICT: race looks winnable -> next step is the micro-size race test (send me this output)")
        else:
            print("\nVERDICT: race NOT winnable from this box overall"
                  + (" (the post-hoc gate line is a hypothesis: confirm it with ./scripts/probe.sh gated)" if h["n"] else ""))
    json.dump(out, open(jpath + ".probes.json", "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
