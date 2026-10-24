"""Live evaluation: score ONLY what actually executed, against on-chain ground truth.

usage: python3 live_eval.py JOURNAL.jsonl WALLET_PUBKEY

  1. ground-truth PnL per traded mint = sum of our wallet's SOL balance deltas over every tx we sent for it
     (pump fees, priority fees, tips, ATA rent/refund, failed txs all included)
  2. landing position of every fill (slot offset vs create, tx index) and failed/missed buys
  3. sim-vs-live: simulator re-run at OUR ACTUAL landing slot for each traded launch
  4. control: launches deliberately skipped (control_skip) scored by the simulator at b2 -> compares
     traded vs untraded outcomes of the same rule (does our presence change the outcome?)
"""
from __future__ import annotations

import asyncio
import collections
import json
import sys

import numpy as np

from anatomy import bundle_end
from pumpdecode import parse_tx
from rpc import Rpc
from sim import load_launch, simulate


async def launch_rows(rpc: Rpc, bc: str, mint: str, s0: int, slots: int = 900):
    rows = []
    async for t in rpc.txs_for_address(bc, order="asc", limit=100, slot_gte=s0, slot_lt=s0 + slots,
                                       status="succeeded", max_pages=6):
        ti = parse_tx(t)
        for n, e in ti.events:
            if n == "trade" and e["mint"] == mint:
                rows.append({"slot": ti.slot, "idx": ti.tx_index, "ok": 1, "user": e["user"], "buy": int(e["is_buy"]),
                             "sol": e["sol_amount"], "tok": e["token_amount"], "vsol": e["virtual_sol_reserves"],
                             "vtok": e["virtual_token_reserves"], "sig": ti.sig})
    return rows


async def main():
    jpath, wallet = sys.argv[1], sys.argv[2]
    ev = [json.loads(l) for l in open(jpath)]
    by_mint = collections.defaultdict(list)
    for e in ev:
        m = e.get("mint") or (e.get("d") or {}).get("mint")
        if m:
            by_mint[m].append(e)
    traded = {m: es for m, es in by_mint.items() if any(x["ev"] == "buy_sent" for x in es)}
    controls = {m: es for m, es in by_mint.items() if any(x["ev"] == "control_skip" for x in es)}
    print(f"journal: {len(ev)} events; buys sent for {len(traded)} mints; control skips {len(controls)}")

    async with Rpc(rps=30, concurrency=8) as rpc:
        # 1. our wallet's on-chain history -> exact SOL delta per tx, mapped to mints
        hist = []
        async for t in rpc.txs_for_address(wallet, order="asc", limit=100, max_pages=50, status="any"):
            hist.append(t)
        delta_by_mint = collections.defaultdict(int)
        fails = collections.Counter()
        for t in hist:
            keys = t["transaction"]["message"]["accountKeys"]
            la = t["meta"].get("loadedAddresses") or {}
            allk = keys + la.get("writable", []) + la.get("readonly", [])
            i = allk.index(wallet) if wallet in allk else None
            if i is None:
                continue
            d = t["meta"]["postBalances"][i] - t["meta"]["preBalances"][i]
            mints = {m for m in traded if m in allk}
            for m in mints:
                delta_by_mint[m] += d
                if t["meta"].get("err"):
                    fails[m] += 1
        res = []
        for m, es in traded.items():
            sent = next(x for x in es if x["ev"] == "buy_sent")
            fill = next((x for x in es if x["ev"] == "buy_fill"), None)
            sell = next((x for x in es if x["ev"] == "sell_fill"), None)
            r = {"mint": m, "rule": sent["d"]["rule"], "create_slot": sent["d"]["create_slot"],
                 "pnl_chain": delta_by_mint.get(m, 0) / 1e9, "failed_txs": fails.get(m, 0),
                 "filled": fill is not None, "closed": sell is not None,
                 "slot_off": fill["slot_off"] if fill else None, "fill_idx": fill["idx"] if fill else None,
                 "spend": sent["spend"] / 1e9}
            res.append(r)
        # bonding curve addresses: read from the fill tx accounts (index of pump program accounts)
        for r in res:
            if not r["filled"]:
                continue
            fill = next(x for x in traded[r["mint"]] if x["ev"] == "buy_fill")
            t = await rpc.call("getTransaction", [fill["sig"], {"encoding": "json", "maxSupportedTransactionVersion": 1,
                                                                "commitment": "confirmed"}])
            keys = t["transaction"]["message"]["accountKeys"]
            # v3 layout: bonding_curve is account #6 of the pump ix; find via pump ix
            bc = None
            for ix in t["transaction"]["message"]["instructions"]:
                if keys[ix["programIdIndex"]] == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
                    bc = keys[ix["accounts"][5]]
            if not bc:
                continue
            rows = await launch_rows(rpc, bc, r["mint"], r["create_slot"])
            others = [x for x in rows if x["user"] != wallet]
            L = load_launch({"mint": r["mint"], "user": "", "create_slot": r["create_slot"], "create_idx": 0,
                             "rows": others})
            if L:
                s = simulate(L, buy_sol=r["spend"], d_buy=fill["slot_off"], where="end", tp=0.3,
                             max_hold_slots=142, d_sell=2)
                r["pnl_sim_at_our_slot"] = (s.pnl_lamports - 600_000) / 1e9
        # 4. controls: simulate at b2
        ctrl = []
        for m, es in controls.items():
            c = next(x for x in es if x["ev"] == "control_skip")["d"]
            # bonding curve PDA requires derivation; get it from any trade of the mint via getTransactionsForAddress(mint)
            bc = None
            async for t in rpc.txs_for_address(m, order="asc", limit=5, max_pages=1):
                for n, e in parse_tx(t).events:
                    if n == "create":
                        bc = e["bonding_curve"]
                break
            if not bc:
                continue
            rows = await launch_rows(rpc, bc, m, c["create_slot"])
            L = load_launch({"mint": m, "user": c["deployer"], "create_slot": c["create_slot"], "create_idx": 0,
                             "rows": rows})
            if L:
                s = simulate(L, buy_sol=0.25, d_buy=2, where="end", bundle_end_idx=bundle_end(L), tp=0.3,
                             max_hold_slots=142, d_sell=2)
                ctrl.append((s.pnl_lamports - 600_000) / 1e9)

    filled = [r for r in res if r["filled"]]
    print(f"\nbuys sent {len(res)}, filled {len(filled)} ({len(filled)/max(len(res),1):.0%}), "
          f"closed {sum(r['closed'] for r in res)}")
    if filled:
        so = collections.Counter(r["slot_off"] for r in filled)
        print("landing slot offset vs create:", dict(sorted(so.items())))
        p = np.array([r["pnl_chain"] for r in res])
        print(f"ON-CHAIN PnL (all sent buys, incl. failed/missed costs): total {p.sum():+.4f} SOL, "
              f"mean {p.mean():+.5f}, win {np.mean(p > 0):.2f}, n {len(p)}")
        cmp = [(r["pnl_chain"], r["pnl_sim_at_our_slot"]) for r in filled if "pnl_sim_at_our_slot" in r and r["closed"]]
        if cmp:
            a = np.array(cmp)
            print(f"sim-vs-live on {len(a)} closed trades: live mean {a[:,0].mean():+.5f} vs sim-at-our-slot "
                  f"{a[:,1].mean():+.5f}  (gap {a[:,0].mean()-a[:,1].mean():+.5f} SOL/trade)")
    if ctrl:
        c = np.array(ctrl)
        print(f"CONTROL (skipped, simulated at b2, 0.25 SOL): n {len(c)} mean {c.mean():+.5f} SOL")
    json.dump(res, open(jpath + ".eval.json", "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
