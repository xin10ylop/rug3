"""Live rule generator: rolling 24h walk-forward stats per (funder, amount) key -> rules.json for the bot.

Runs forever (every INTERVAL s). Incremental: only new creates / deployers / launches are fetched each cycle.
Every rule is backed by execution-realistic simulated outcomes of launches whose result is already known.

usage: python3 rulegen.py [--seed creates.jsonl.gz funding.jsonl.gz sim.parquet] [--once]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import gzip
import json
import os
import time

import orjson
import pandas as pd

from analyze import BUY_TX_COST, SELL_TX_COST
from anatomy import bundle_end
from backfill_creates import row_from_tx
from backfill_funding import sol_moves, touches_pump
from pumpdecode import MINT_AUTHORITY, parse_tx
from rpc import Rpc
from sim import load_launch, simulate

SOLQ = "11111111111111111111111111111111"
LIVE = "/home/user/rug3/data/live/"
RULES = os.environ.get("RULES_PATH", "/home/user/rug3/data/live/rules.json")
INTERVAL = 600
WINDOW_SLOTS = int(24 * 3600 / 0.212)
READY_SLOTS = 650  # need ~2.3 min of trades after create to score a 30s-hold trade
TRADE_SLOTS = 700
N_MIN, MEAN_MIN, LAST_N = 5, 0.005, 30  # rule thresholds (SOL per 0.25-SOL trade)
TX_COST_LIVE = 600_000  # lamports per round trip with low-fee landing (SWQoS + moderate prio)


def sim_launch(L) -> dict:
    be = bundle_end(L)
    out = {}
    for name, d_buy in (("b2", 2), ("b4", 4)):
        r = simulate(L, buy_sol=0.25, d_buy=d_buy, where="end", bundle_end_idx=be, tp=0.3,
                     max_hold_slots=int(30 / 0.212), d_sell=2)
        out[name] = (r.pnl_lamports - TX_COST_LIVE) / 1e9
        out[name + "_why"] = r.exit_reason
    return out


class State:
    def __init__(self):
        os.makedirs(LIVE, exist_ok=True)
        self.cursor = 0
        self.deployers: dict[str, dict] = {}
        self.launch_count = collections.Counter()  # deployer -> launches in window
        self.pending: list[dict] = []  # candidate creates awaiting trade window
        self.rows: list[dict] = []  # scored launches (rolling)
        p = LIVE + "state.json"
        if os.path.exists(p):
            d = json.load(open(p))
            self.cursor = d["cursor"]
            self.deployers = d["deployers"]
            self.pending = d["pending"]
            self.rows = d["rows"]
            self.launch_count = collections.Counter(d.get("launch_count", {}))

    def save(self):
        tmp = LIVE + "state.json.tmp"
        json.dump({"cursor": self.cursor, "deployers": self.deployers, "pending": self.pending, "rows": self.rows,
                   "launch_count": dict(self.launch_count)}, open(tmp, "w"))
        os.replace(tmp, LIVE + "state.json")


def fund_key(dep: dict | None):
    if not dep or not dep.get("funder") or not dep.get("amt"):
        return None
    return f"{dep['funder']}|{dep['amt'] // 100_000}"


async def funding_of(rpc: Rpc, user: str, first_slot: int) -> dict:
    first = []
    async for t in rpc.txs_for_address(user, order="asc", limit=12, max_pages=1):
        first.append(t)
    fund = []
    pre = 0
    for t in first:
        if t["slot"] < first_slot or (t["slot"] == first_slot and not touches_pump(t)):
            pre += 1
        for d, cp, lam, kind in sol_moves(t, user):
            if d == "in" and lam >= 10_000_000:
                fund.append((cp, lam, t["slot"]))
    f = fund[0] if fund else None
    return {"funder": f[0] if f else None, "amt": f[1] if f else None, "fund_slot": f[2] if f else None,
            "pre": pre, "fresh": pre <= 3}


async def cycle(st: State, rpc: Rpc):
    tip = await rpc.call("getSlot", [{"commitment": "confirmed"}])
    if not st.cursor:
        st.cursor = tip - 3000
    # 1. new creates
    new = []
    async for t in rpc.txs_for_address(MINT_AUTHORITY, order="asc", limit=100, slot_gte=st.cursor, slot_lt=tip,
                                       status="succeeded", max_pages=200):
        new.extend(row_from_tx(t))
    st.cursor = tip
    for c in new:
        st.launch_count[c["user"]] += 1
    # 2. funding for unseen deployers (only those that could be candidates)
    cands = [c for c in new if c["quote"] == SOLQ and not c["mayhem"]]
    unseen = list({c["user"]: c for c in cands if c["user"] not in st.deployers}.values())

    async def one(c):
        try:
            st.deployers[c["user"]] = await funding_of(rpc, c["user"], c["slot"])
        except Exception as e:
            st.deployers[c["user"]] = {"error": str(e)[:100]}
    await asyncio.gather(*(one(c) for c in unseen))
    for c in cands:
        if st.launch_count[c["user"]] <= 20:
            st.pending.append({"mint": c["mint"], "bc": c["bc"], "user": c["user"], "slot": c["slot"],
                               "idx": c["idx"]})
    # 3. score launches whose trade window is complete
    ready = [p for p in st.pending if p["slot"] + READY_SLOTS < tip]
    st.pending = [p for p in st.pending if p["slot"] + READY_SLOTS >= tip]

    async def score(p):
        rows = []
        async for t in rpc.txs_for_address(p["bc"], order="asc", limit=100, slot_gte=p["slot"],
                                           slot_lt=p["slot"] + TRADE_SLOTS, status="succeeded", max_pages=4):
            nsig = t["transaction"]["message"]["header"]["numRequiredSignatures"]
            ti = parse_tx(t)
            for n, e in ti.events:
                if n == "trade" and e["mint"] == p["mint"]:
                    rows.append({"slot": ti.slot, "idx": ti.tx_index, "ok": 1, "user": e["user"],
                                 "buy": int(e["is_buy"]), "sol": e["sol_amount"], "tok": e["token_amount"],
                                 "vsol": e["virtual_sol_reserves"], "vtok": e["virtual_token_reserves"],
                                 "prio": ti.fee - 5000 * nsig, "tips": ti.tip_lamports})
        L = load_launch({"mint": p["mint"], "user": p["user"], "create_slot": p["slot"], "create_idx": p["idx"],
                         "rows": rows})
        if not L:
            return
        s = sim_launch(L)
        st.rows.append({"mint": p["mint"], "user": p["user"], "s0": p["slot"],
                        "fk": fund_key(st.deployers.get(p["user"])), **s})
    await asyncio.gather(*(score(p) for p in ready))
    # 4. prune window
    lo = tip - WINDOW_SLOTS
    st.rows = [r for r in st.rows if r["s0"] >= lo]
    return tip, len(new), len(ready)


def make_rules(st: State, tip: int) -> list[dict]:
    by = collections.defaultdict(list)
    for r in sorted(st.rows, key=lambda r: r["s0"]):
        if r.get("fk"):
            by[r["fk"]].append((r["b2"] + r["b4"]) / 2)
    rules = []
    for k, v in by.items():
        last = v[-LAST_N:]
        if len(last) >= N_MIN and sum(last) / len(last) >= MEAN_MIN:
            funder, bucket = k.split("|")
            b = int(bucket)
            rules.append({"name": k, "funder": funder, "min_lamports": b * 100_000, "max_lamports": b * 100_000 + 99_999,
                          "fresh_only": True, "ttl_s": 3 * 3600, "n": len(v), "mean_last": sum(last) / len(last),
                          "hit": sum(1 for x in last if x > 0) / len(last)})
    rules.sort(key=lambda r: -r["mean_last"])
    return rules


def armed_deployers(st: State, rules: list[dict], tip: int) -> list[str]:
    """Deployers already funded (within TTL) with a rule's funder+amount: pre-arm them in the bot."""
    keys = {r["name"] for r in rules}
    ttl_slots = int(3 * 3600 / 0.212)
    return [u for u, d in st.deployers.items()
            if d.get("fund_slot") and d["fund_slot"] >= tip - ttl_slots and fund_key(d) in keys and d.get("fresh")]


def seed(st: State, creates_p, funding_p, sim_p):
    """Bootstrap rolling state from a full backfill (same scoring as live: b2/b4, tp30, 30s)."""
    sim = pd.read_parquet(sim_p)
    fund = {}
    for l in gzip.open(funding_p):
        r = orjson.loads(l)
        fl = [x for x in r.get("fund", []) if x[5] == "in" and x[1] >= 10_000_000]
        f = fl[0] if fl else None
        fund[r["creator"]] = {"funder": f[0] if f else None, "amt": f[1] if f else None,
                              "fund_slot": f[2] if f else None, "pre": r.get("pre_create_txs"),
                              "fresh": (r.get("pre_create_txs", 99) <= 3)}
    st.deployers.update(fund)
    for l in gzip.open(creates_p):
        c = orjson.loads(l)
        st.launch_count[c["user"]] += 1
        st.cursor = max(st.cursor, c["slot"] + 1)
    adj = (BUY_TX_COST + SELL_TX_COST - TX_COST_LIVE) / 1e9 + 0.1 * 0.000505  # undo sim's higher assumed costs
    for r in sim[["mint", "user", "s0", "b2|tp30_t30", "b4|tp30_t30"]].to_dict("records"):
        st.rows.append({"mint": r["mint"], "user": r["user"], "s0": int(r["s0"]), "fk": fund_key(fund.get(r["user"])),
                        "b2": r["b2|tp30_t30"] + adj, "b4": r["b4|tp30_t30"] + adj})


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", nargs=3)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    st = State()
    if a.seed and not st.rows:
        seed(st, *a.seed)
        st.save()
        print(f"seeded: {len(st.rows)} scored launches, {len(st.deployers)} deployers, cursor {st.cursor}", flush=True)
    async with Rpc(rps=40, concurrency=16) as rpc:
        while True:
            t0 = time.time()
            try:
                tip, n_new, n_scored = await cycle(st, rpc)
                rules = make_rules(st, tip)
                tmp = RULES + ".tmp"
                json.dump({"generated_at": time.time(), "tip_slot": tip, "rules": rules,
                           "armed": armed_deployers(st, rules, tip)}, open(tmp, "w"), indent=1)
                os.replace(tmp, RULES)
                st.save()
                print(f"{time.strftime('%H:%M:%S')} tip={tip} new_creates={n_new} scored={n_scored} "
                      f"window_rows={len(st.rows)} rules={len(rules)} calls={rpc.calls} "
                      f"MB={rpc.bytes / 1e6:.0f} took={time.time() - t0:.0f}s", flush=True)
            except Exception as e:
                print("cycle error:", repr(e)[:300], flush=True)
            if a.once:
                break
            await asyncio.sleep(max(5, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    asyncio.run(main())
