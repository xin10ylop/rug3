"""Walk-forward evaluation of rugger-sniping selection rules on execution-realistic simulated PnL.

python3 analyze.py  (paths default to data/bf/*_24h.jsonl.gz)
"""
from __future__ import annotations

import collections
import gzip
import sys
import zlib

import numpy as np
import orjson
import pandas as pd

from anatomy import bundle_end, summarize
from features import deployer_table, funder_kinds, launch_keys, load_creates, load_funding
from sim import load_launch, simulate

BF = "/home/user/rug3/data/bf/"
SLOT_S = 0.212
BUY_SOL = 0.25
# per-round-trip tx costs (lamports): buy base+prio+tip, sell base+prio+tip
BUY_TX_COST = 5000 + 500_000 + 1_000_000
SELL_TX_COST = 5000 + 200_000 + 500_000
FAIL_RATE = 0.10
FAIL_COST = 5000 + 500_000

SCENARIOS = {
    # name: (d_buy, where)
    "b0start": (0, "start"),  # same slot, right after bundle (best case, needs to win the race)
    "b0end": (0, "end"),  # same slot, after everything else in the slot
    "b1": (1, "end"),
    "b2": (2, "end"),
    "b4": (4, "end"),
}
EXITS = {
    # name: kwargs for simulate
    "tp50_t60": dict(tp=0.5, max_hold_slots=int(60 / SLOT_S)),
    "tp100_t180": dict(tp=1.0, max_hold_slots=int(180 / SLOT_S)),
    "tp30_t30": dict(tp=0.3, max_hold_slots=int(30 / SLOT_S)),
    "tp100_t600": dict(tp=1.0, max_hold_slots=int(600 / SLOT_S)),
}


def iter_trades(path):
    with gzip.open(path) as f:
        try:
            for line in f:
                try:
                    yield orjson.loads(line)
                except orjson.JSONDecodeError:
                    return
        except (EOFError, zlib.error, OSError):
            return


def find_snipers(launches, min_launches=15, min_deployers=8, max_off=3):
    seen = collections.defaultdict(set)
    seen_dep = collections.defaultdict(set)
    for L in launches:
        be = bundle_end(L)
        for r in L.rows:
            if r["buy"] and r["slot"] - L.s0 <= max_off and (r["slot"], r["idx"] or 0) > (L.s0, be) \
                    and r["user"] != L.user:
                seen[r["user"]].add(L.mint)
                seen_dep[r["user"]].add(L.user)
    return {u for u in seen if len(seen[u]) >= min_launches and len(seen_dep[u]) >= min_deployers}


def main(tag="24h"):
    creates = load_creates(BF + f"creates_{tag}.jsonl.gz")
    funding = load_funding(BF + f"funding_{tag}.jsonl.gz")
    cmap = {c["mint"]: c for c in creates}
    dep = deployer_table(creates, funding)
    fkind = funder_kinds(dep)
    launches = []
    for d in iter_trades(BF + f"trades_{tag}.jsonl.gz"):
        L = load_launch(d)
        if L and L.mint in cmap:
            launches.append(L)
    launches.sort(key=lambda L: L.s0)
    print(f"{len(launches)} launches with trades", flush=True)
    snipers = find_snipers(launches)
    print(f"{len(snipers)} sniper wallets identified", flush=True)

    recs = []
    for L in launches:
        c = cmap[L.mint]
        a = summarize(L)
        be = bundle_end(L)
        rec = dict(mint=L.mint, user=L.user, s0=L.s0, pb_mcap=a["pb_mcap"], peak_x=a["peak_x"],
                   bund_sol=a["bund_sol"], dev=a["dev"], n=a["n"])
        # rivals: outsider sniper buys after the bundle in slot 0..k
        for k in (0, 1, 2):
            rec[f"riv{k}"] = sum(1 for r in L.rows if r["buy"] and r["user"] in snipers and r["slot"] - L.s0 <= k
                                 and (r["slot"], r["idx"] or 0) > (L.s0, be))
        for sn, (db, where) in SCENARIOS.items():
            for en, ek in EXITS.items():
                res = simulate(L, buy_sol=BUY_SOL, d_buy=db, where=where, bundle_end_idx=be, d_sell=2, **ek)
                pnl = res.pnl_lamports - BUY_TX_COST - SELL_TX_COST - FAIL_RATE * FAIL_COST
                rec[f"{sn}|{en}"] = pnl / 1e9
                rec[f"{sn}|{en}|why"] = res.exit_reason
                rec[f"{sn}|em"] = res.entry_mcap
        keys = launch_keys(c, dep, fkind, 0)
        for k in keys:
            rec["k_" + k[0]] = "|".join(map(str, k[1:]))
        d = dep.get(L.user, {})
        rec["fresh"] = d.get("fresh")
        rec["funder_kind"] = fkind.get(d.get("funder"), "none")
        recs.append(rec)
    df = pd.DataFrame(recs)
    df.to_parquet(BF + f"sim_{tag}.parquet")
    print("saved", len(df))
    return df


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "24h")
