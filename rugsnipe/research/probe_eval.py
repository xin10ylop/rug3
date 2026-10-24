"""Evaluate latency probes: where did our no-trade tx land relative to the create and to rival slot-0 buys?

usage: python3 probe_eval.py JOURNAL.jsonl [WALLET_PUBKEY]   (wallet defaults to ../wallet.json)

Decision rule (from docs/REPORT.md): continue to a micro-size race test only if
  - we land in the create slot in >= 60% of probes, and
  - on contested launches (rivals bought in the create slot) we are ahead of ALL rivals in >= 40%.
"""
import asyncio
import collections
import json
import sys
from pathlib import Path

import base58
import numpy as np

from pumpdecode import parse_tx
from rpc import Rpc


def wallet_pubkey() -> str:
    p = Path(__file__).resolve().parents[1] / "wallet.json"
    return base58.b58encode(bytes(json.load(open(p))[32:64])).decode()


async def main():
    jpath = sys.argv[1]
    wallet = sys.argv[2] if len(sys.argv) > 2 else wallet_pubkey()
    probes = [p for p in (json.loads(l) for l in open(jpath)) if p.get("ev") == "probe_sent"]
    print(f"{len(probes)} probes in {jpath}; wallet {wallet}")
    if not probes:
        return
    send_ok = collections.Counter()
    for p in probes:
        for s in p["send"]:
            send_ok[(s["name"], s["ok"])] += 1
    print("send results by path:", dict(send_ok))

    async with Rpc(rps=30, concurrency=8) as rpc:
        ours = {}
        async for t in rpc.txs_for_address(wallet, full=False, order="desc", limit=1000, max_pages=5):
            ours[t["signature"]] = (t["slot"], t.get("transactionIndex"), t.get("err"))
        out = []

        async def one(p):
            seq = []
            async for t in rpc.txs_for_address(p["mint"], order="asc", limit=80, max_pages=1,
                                               slot_gte=p["create_slot"], slot_lt=p["create_slot"] + 8):
                ti = parse_tx(t)
                seq.append(dict(slot=ti.slot, idx=ti.tx_index or 0, create=any(n == "create" for n, _ in ti.events),
                                buy=any(n == "trade" and e["is_buy"] for n, e in ti.events), signer=ti.signer, ok=ti.ok))
            cr = next((x for x in seq if x["create"]), None)
            me = ours.get(p["sig"])
            r = {"n": p["n"], "landed": me is not None, "since_recv_ms": p["since_recv_ms"]}
            if not (cr and me):
                out.append(r)
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
            r["slot_gap"] = me[0] - cr["slot"]
            r["rivals_slot0"] = len(rivals0)
            if me[0] == cr["slot"]:
                r["idx_gap"] = me[1] - cr["idx"]
                r["rivals_ahead"] = sum(1 for x in rivals0 if x["idx"] < me[1])
            else:
                r["rivals_ahead"] = len(rivals0) + sum(1 for x in seq if cr["slot"] < x["slot"] < me[0] and x["buy"]
                                                       and x["ok"] and x["signer"] != p["deployer"])
            out.append(r)

        await asyncio.gather(*(one(p) for p in probes))

    landed = [r for r in out if r["landed"]]
    print(f"\nlanded {len(landed)}/{len(out)} probes")
    if not landed:
        print("VERDICT: no probe landed -> check senders/tips/balance (see data/logs/probe_stderr.log)")
        return
    gaps = collections.Counter(r.get("slot_gap") for r in landed if r.get("slot_gap") is not None)
    print("landing slot - create slot:", dict(sorted(gaps.items())))
    same = [r for r in landed if r.get("slot_gap") == 0]
    same_rate = len(same) / len(landed)
    print(f"same-slot landings: {len(same)} ({same_rate:.0%})")
    if same:
        print(f"  in-block index after create: p50 {np.median([r['idx_gap'] for r in same]):.0f}")
    con = [r for r in landed if r.get("rivals_slot0")]
    win = sum(1 for r in con if r["rivals_ahead"] == 0)
    if con:
        print(f"contested launches (rival buys in the create slot): {len(con)}; ahead of ALL rivals in {win} "
              f"({win / len(con):.0%}); rivals ahead of us p50 {np.median([r['rivals_ahead'] for r in con]):.0f}")
    print("create seen -> sent (ms): p50 %.1f  p90 %.1f" % tuple(np.percentile([r['since_recv_ms'] for r in out], [50, 90])))
    ok = same_rate >= 0.60 and len(con) >= 10 and win / len(con) >= 0.40
    if len(con) < 10:
        print(f"\nVERDICT: inconclusive ({len(con)} contested launches; need >= 10) -> run more probes")
    elif ok:
        print("\nVERDICT: race looks winnable -> next step is the micro-size race test (send me this output)")
    else:
        print("\nVERDICT: race NOT winnable from this box -> do not trade rugger-sniping")
    json.dump(out, open(jpath + ".probes.json", "w"), indent=1)


asyncio.run(main())
