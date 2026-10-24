"""Live evaluation: score ONLY what actually executed, against on-chain ground truth.

usage: python3 live_eval.py JOURNAL.jsonl [WALLET_PUBKEY]   (wallet defaults to ../wallet.json)

  1. ground-truth PnL per traded mint = sum of our wallet's SOL balance deltas over every tx we sent for it
     (pump fees, priority fees, tips, ATA rent/refund, failed txs all included)
  2. race outcome of every fill: won (ahead of every slot-0 rival), lost (a rival bought before us), uncontested
     (create slot, no rival), late (after the create slot, no rival) -> PnL by outcome, live contested win rate
  3. sim-vs-live: simulator re-run at OUR EXACT landing position (slot + in-block index) for each fill
  4. control: launches deliberately skipped (control_skip) simulated as race winner (b0start) and loser (b0end)
Trade data for 2-4 comes from the bot's own stream log ("obs" events) when present, else from RPC history
(which can lag by an hour); PnL from the wallet's on-chain history, else from the journal's fills minus fees.
"""
from __future__ import annotations

import asyncio
import collections
import json
import sys

import numpy as np

from anatomy import bundle_end
from probe_eval import wallet_pubkey
from pumpdecode import parse_tx
from rpc import Rpc
from sim import load_launch, simulate

TX_COST = 495_000 + 195_000  # configured buy + sell priority fee, tip and base fee (lamports)
BUY_FEE, SELL_FEE, FAILED_BUY = 495_000, 195_000, 485_000
TP, HOLD = 0.3, 142          # exit as configured: +30% net or 30 s (~142 slots)
RACE_WIN = 0.42              # measured by the gated probe: ahead of all slot-0 rivals on 42% of contested launches


async def mint_txs(rpc: Rpc, mint: str, s0: int, slots: int):
    seq = []
    # succeeded only: on contested launches failed attempts can outnumber trades 1000:1 and would exhaust the page cap
    async for t in rpc.txs_for_address(mint, order="asc", limit=100, slot_gte=s0, slot_lt=s0 + slots, status="succeeded",
                                       max_pages=20):
        ti = parse_tx(t)
        trades = [e for n, e in ti.events if n == "trade" and e["mint"] == mint]
        seq.append(dict(slot=ti.slot, idx=ti.tx_index or 0, ok=ti.ok, signer=ti.signer, sig=ti.sig,
                        create=any(n == "create" for n, _ in ti.events), trades=trades))
    return seq


def seq_from_obs(obs, cre):
    """Pseudo-transactions (one per slot/index) from the bot's trade log of a launch and its executed-create position."""
    g = collections.defaultdict(list)
    for o in obs:
        g[(o["slot"], o["idx"])].append({"is_buy": o["buy"], "user": o["user"], "sol_amount": o["sol"], "token_amount": o["tok"],
                                         "virtual_sol_reserves": o["vsol"], "virtual_token_reserves": o["vtok"], "mint": o["mint"]})
    if cre and cre not in g:
        g[cre] = []
    return [dict(slot=k[0], idx=k[1], ok=True, signer=tr[0]["user"] if tr else "", sig="", create=k == cre, trades=tr)
            for k, tr in sorted(g.items())]


async def launch_seq(rpc, m, es, create_slot, src):
    obs = [e for e in es if e["ev"] == "obs"]
    cre = next(((e["slot"], e["idx"]) for e in es if e["ev"] == "obs_create"), None)
    if cre:
        src["journal"] += 1
        return seq_from_obs(obs, cre)
    src["rpc"] += 1
    return await mint_txs(rpc, m, create_slot, 900)


def launch_of(seq, mint, deployer, exclude_user=None):
    rows = [{"slot": x["slot"], "idx": x["idx"], "ok": 1, "user": e["user"], "buy": int(e["is_buy"]), "sol": e["sol_amount"],
             "tok": e["token_amount"], "vsol": e["virtual_sol_reserves"], "vtok": e["virtual_token_reserves"], "sig": x["sig"]}
            for x in seq if x["ok"] for e in x["trades"] if e["user"] != exclude_user]
    cr = next((x for x in seq if x["create"] and x["ok"]), None)
    if not cr:
        return None, None
    return load_launch({"mint": mint, "user": deployer, "create_slot": cr["slot"], "create_idx": cr["idx"], "rows": rows}), cr


def race(seq, cr, deployer, wallet, fill_slot, fill_idx):
    end = cr["idx"]  # dev bundle = contiguous txs after the create; our own tx breaks the run
    for x in sorted((x for x in seq if x["slot"] == cr["slot"] and x["idx"] > cr["idx"] and x["signer"] != wallet),
                    key=lambda x: x["idx"]):
        if x["idx"] != end + 1:
            break
        end = x["idx"]
    riv = [x for x in seq if x["slot"] == cr["slot"] and x["idx"] > end and x["ok"] and x["signer"] not in (deployer, wallet)
           and any(e["is_buy"] for e in x["trades"])]
    if riv:
        return "won" if fill_slot == cr["slot"] and all(x["idx"] > fill_idx for x in riv) else "lost"
    return "uncontested" if fill_slot == cr["slot"] else "late"


def stat(p):
    p = np.asarray(p)
    return f"n {len(p):3d}  total {p.sum():+.4f}  mean {p.mean():+.5f}  win {np.mean(p > 0):.2f}" if len(p) else "n   0"


async def main():
    jpath = sys.argv[1]
    wallet = sys.argv[2] if len(sys.argv) > 2 else wallet_pubkey()
    ev = [json.loads(l) for l in open(jpath)]
    by_mint = collections.defaultdict(list)
    for e in ev:
        m = e.get("mint") or (e.get("d") or {}).get("mint")
        if m:
            by_mint[m].append(e)
    traded = {m: es for m, es in by_mint.items() if any(x["ev"] == "buy_sent" for x in es)}
    controls = {m: es for m, es in by_mint.items() if any(x["ev"] == "control_skip" for x in es)}
    skips = collections.Counter(e["why"] for e in ev if e.get("ev") == "skip")
    print(f"journal: {len(ev)} events; buys sent for {len(traded)} mints; control skips {len(controls)}; skips {dict(skips)}")
    obs = collections.defaultdict(list)
    for e in ev:
        if e.get("ev") == "obs":
            obs[e["mint"]].append(e)
    jumps = 0  # each trade's reported reserves must follow from the previous trade's: a jump means a missing trade
    for xs in obs.values():
        xs.sort(key=lambda e: (e["slot"], e["idx"]))
        jumps += sum(abs((a["vsol"] + b["sol"] if b["buy"] else a["vsol"] - b["sol"]) - b["vsol"]) > 2 for a, b in zip(xs, xs[1:]))
    if obs:
        print(f"trade log: {len(obs)} launches, {sum(map(len, obs.values()))} trades, {jumps} reserve jumps (0 = complete)")

    async with Rpc(rps=30, concurrency=8) as rpc:
        hist = []
        async for t in rpc.txs_for_address(wallet, order="asc", limit=100, max_pages=50, status="any"):
            hist.append(t)
        delta_by_mint, fails = collections.defaultdict(int), collections.Counter()
        first_ok = {}  # mint -> (slot, index) of our first successful tx for it = our fill (ground truth, not journal)
        okn = collections.Counter()  # successful txs per mint in the (possibly lagging) history
        for t in hist:
            keys = t["transaction"]["message"]["accountKeys"]
            la = t["meta"].get("loadedAddresses") or {}
            allk = keys + la.get("writable", []) + la.get("readonly", [])
            if wallet not in allk:
                continue
            i = allk.index(wallet)
            d = t["meta"]["postBalances"][i] - t["meta"]["preBalances"][i]
            for m in (m for m in traded if m in allk):
                delta_by_mint[m] += d
                fails[m] += bool(t["meta"].get("err"))
                okn[m] += not t["meta"].get("err")
                if not t["meta"].get("err") and m not in first_ok:
                    first_ok[m] = (t["slot"], t.get("transactionIndex") or 0)

        res = []
        src = collections.Counter()

        async def one(m, es):
            sent = next(x for x in es if x["ev"] == "buy_sent")
            jf = next((x for x in es if x["ev"] == "buy_fill"), None)
            fill = (jf["slot"], jf["idx"]) if jf else first_ok.get(m)
            sell = next((x for x in es if x["ev"] == "sell_fill"), None)
            d = sent["d"]
            if m in delta_by_mint and (okn[m] >= 2 or not sell):  # history has both legs (or there was no sell)
                pnl, psrc = delta_by_mint[m] / 1e9, "chain"
            elif fill and sell:
                n_sells = sum(1 for x in es if x["ev"] == "sell_sent")
                pnl, psrc = (sell["pnl"] - BUY_FEE - SELL_FEE * max(n_sells, 1)) / 1e9, "journal"
            else:
                pnl, psrc = (-FAILED_BUY if not fill else -BUY_FEE) / 1e9, "journal"
            r = {"mint": m, "rule": d["rule"], "fp": d.get("fp"), "leader_km": d.get("leader_km"),
                 "into_slot_ms": d.get("into_slot_ms"), "create_slot": d["create_slot"], "spend": sent["spend"] / 1e9,
                 "pnl_chain": pnl, "pnl_src": psrc, "failed_txs": fails.get(m, 0),
                 "filled": fill is not None, "closed": sell is not None, "race": "no_fill"}
            res.append(r)
            if not fill:
                return
            seq = await launch_seq(rpc, m, es, d["create_slot"], src)
            L, cr = launch_of(seq, m, d["deployer"], exclude_user=wallet)
            if not L:
                return
            r["race"] = race(seq, cr, d["deployer"], wallet, *fill)
            r["slot_off"] = fill[0] - cr["slot"]
            s = simulate(L, buy_sol=r["spend"], d_buy=r["slot_off"], where="at", at_idx=fill[1], tp=TP,
                         max_hold_slots=HOLD, d_sell=2)
            r["pnl_sim_at_our_position"] = (s.pnl_lamports - TX_COST) / 1e9

        await asyncio.gather(*(one(m, es) for m, es in traded.items()))

        ctrl = []

        async def ctl(m, es):
            c = next(x for x in es if x["ev"] == "control_skip")["d"]
            seq = await launch_seq(rpc, m, es, c["create_slot"], src)
            L, cr = launch_of(seq, m, c["deployer"])
            if L:
                size = float(np.median([r["spend"] for r in res])) if res else 0.03
                a = simulate(L, buy_sol=size, d_buy=0, where="start", bundle_end_idx=bundle_end(L), tp=TP, max_hold_slots=HOLD)
                b = simulate(L, buy_sol=size, d_buy=0, where="end", tp=TP, max_hold_slots=HOLD)
                contested = race(seq, cr, c["deployer"], wallet, cr["slot"], -1) != "uncontested"  # any slot-0 rival
                first, last = (a.pnl_lamports - TX_COST) / 1e9, (b.pnl_lamports - TX_COST) / 1e9
                ctrl.append((first, last, RACE_WIN * first + (1 - RACE_WIN) * last if contested else first))

        await asyncio.gather(*(ctl(m, es) for m, es in controls.items()))

    filled = [r for r in res if r["filled"]]
    print(f"trade data: {dict(src)} launches | PnL source: {dict(collections.Counter(r['pnl_src'] for r in res))}")
    print(f"\nbuys sent {len(res)}, filled {len(filled)} ({len(filled) / max(len(res), 1):.0%}), "
          f"closed {sum(r['closed'] for r in res)}")
    if not res:
        return
    print("PnL (SOL; every sent buy, incl. fees and failed/missed costs):")
    print(f"  all            {stat([r['pnl_chain'] for r in res])}")
    for k in ("won", "lost", "uncontested", "late", "no_fill"):
        print(f"  {k:14s} {stat([r['pnl_chain'] for r in res if r['race'] == k])}")
    con = [r for r in filled if r["race"] in ("won", "lost")]
    if con:
        print(f"live race: ahead of all rivals on {sum(r['race'] == 'won' for r in con)}/{len(con)} contested fills "
              f"({np.mean([r['race'] == 'won' for r in con]):.0%}); same slot on "
              f"{np.mean([r.get('slot_off') == 0 for r in filled]):.0%} of fills")
    cmp = [(r["pnl_chain"], r["pnl_sim_at_our_position"]) for r in filled if "pnl_sim_at_our_position" in r and r["closed"]]
    if cmp:
        a = np.array(cmp)
        print(f"sim-vs-live on {len(a)} closed trades: live mean {a[:, 0].mean():+.5f} vs sim at our exact position "
              f"{a[:, 1].mean():+.5f}  (gap {a[:, 0].mean() - a[:, 1].mean():+.5f} SOL/trade)")
    if ctrl:
        c = np.array(ctrl)
        print(f"CONTROL (skipped, same size): n {len(c)}  sim if first {c[:, 0].mean():+.5f}  sim if last in slot 0 "
              f"{c[:, 1].mean():+.5f} SOL/trade")
        # presence test: random skips show what the same triggers do WITHOUT us (at the measured race odds);
        # traded on-chain PnL shows what they do WITH us
        t = np.array([r["pnl_chain"] for r in res if r["closed"]])
        if len(t) >= 2 and len(c) >= 2:
            d = t.mean() - c[:, 2].mean()
            se = np.sqrt(t.var(ddof=1) / len(t) + c[:, 2].var(ddof=1) / len(c))
            print(f"PRESENCE TEST: traded (live) {t.mean():+.5f} SOL/trade (n {len(t)}) vs skipped (sim, measured race odds) "
                  f"{c[:, 2].mean():+.5f} (n {len(c)}): difference {d:+.5f} = {d / se:+.1f} standard errors")
            if len(t) >= 15 and len(c) >= 15:
                print("VERDICT: our presence hurts the trade -> STOP" if d < -2 * se else
                      "VERDICT: no presence effect detected -> continue to 60 fills, then judge PnL at 0.1 SOL")
            else:
                print(f"(verdict at >= 15 traded and >= 15 skipped; now {len(t)} / {len(c)})")
    json.dump(res, open(jpath + ".eval.json", "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
