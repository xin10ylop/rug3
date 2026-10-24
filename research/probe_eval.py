"""Evaluate latency probes: where did our no-trade tx land relative to the create and to rival slot-0 buys?

usage: python3 probe_eval.py JOURNAL.jsonl WALLET_PUBKEY
"""
import asyncio, collections, json, sys
import numpy as np
from pumpdecode import parse_tx
from rpc import Rpc


async def main():
    jpath, wallet = sys.argv[1], sys.argv[2]
    probes = [json.loads(l) for l in open(jpath)]
    probes = [p for p in probes if p.get("ev") == "probe_sent"]
    print(f"{len(probes)} probes in journal")
    async with Rpc(rps=30, concurrency=8) as rpc:
        ours = {}
        async for t in rpc.txs_for_address(wallet, full=False, order="desc", limit=1000, max_pages=5):
            ours[t["signature"]] = (t["slot"], t.get("transactionIndex"), t.get("err"))
        out = []

        async def one(p):
            seq = []
            async for t in rpc.txs_for_address(p["mint"], order="asc", limit=60, max_pages=1,
                                               slot_gte=p["create_slot"], slot_lt=p["create_slot"] + 6):
                ti = parse_tx(t)
                ok = ti.ok
                is_create = any(n == "create" for n, _ in ti.events)
                buys = [e for n, e in ti.events if n == "trade" and e["is_buy"]]
                seq.append((ti.slot, ti.tx_index, ti.sig, is_create, ok, bool(buys), ti.signer))
            cr = next((x for x in seq if x[3]), None)
            me = ours.get(p["sig"])
            r = {"n": p["n"], "landed": me is not None, "since_recv_ms": p["since_recv_ms"],
                 "paths_ok": [s["name"] for s in p["send"] if s["ok"]]}
            if cr and me:
                r["slot_gap"] = me[0] - cr[0]
                r["err"] = me[2]
                if me[0] == cr[0]:
                    r["idx_gap"] = me[1] - cr[1]
                    rivals = [x for x in seq if x[0] == cr[0] and x[5] and x[6] != p["deployer"] and x[1] > cr[1] + 4]
                    r["rivals_slot0"] = len(rivals)
                    r["rivals_ahead"] = sum(1 for x in rivals if x[1] < me[1])
            out.append(r)

        await asyncio.gather(*(one(p) for p in probes))
    landed = [r for r in out if r["landed"]]
    print(f"landed {len(landed)}/{len(out)}")
    gaps = collections.Counter(r.get("slot_gap") for r in landed)
    print("slot gap (our landing slot - create slot):", dict(sorted((k, v) for k, v in gaps.items() if k is not None)))
    same = [r for r in landed if r.get("slot_gap") == 0]
    if same:
        print(f"same-slot landings: {len(same)} ({len(same)/len(landed):.0%}); in-block index gap after create: "
              f"p50 {np.median([r['idx_gap'] for r in same]):.0f}")
        con = [r for r in same if r.get("rivals_slot0")]
        if con:
            print(f"contested launches (rival buys in slot 0): {len(con)}; rivals ahead of us p50 "
                  f"{np.median([r['rivals_ahead'] for r in con]):.0f} of {np.median([r['rivals_slot0'] for r in con]):.0f}; "
                  f"we beat ALL rivals in {sum(1 for r in con if r['rivals_ahead'] == 0)} of {len(con)}")
    print("our response time create->sent (ms): p50 %.1f p90 %.1f" % tuple(np.percentile([r['since_recv_ms'] for r in out], [50, 90])))
    json.dump(out, open(jpath + ".probes.json", "w"), indent=1)

asyncio.run(main())
