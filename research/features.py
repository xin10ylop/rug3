"""Pre-launch features and pattern keys for each launch (only information available at/before the create tx)."""
from __future__ import annotations

import collections
import gzip

import orjson

SOLQ = "11111111111111111111111111111111"
MIN_FUND = 10_000_000  # 0.01 SOL


def load_creates(path):
    return [orjson.loads(l) for l in gzip.open(path)]


def load_funding(path):
    return {r["creator"]: r for r in (orjson.loads(l) for l in gzip.open(path))}


def deployer_table(creates, funding):
    """Per deployer: funding source/amount/time, freshness, outbound transfers."""
    first_create = {}
    for c in creates:
        first_create.setdefault(c["user"], c)
    dep = {}
    for u, c0 in first_create.items():
        f = funding.get(u, {})
        fund = [x for x in f.get("fund", []) if x[5] == "in" and x[1] >= MIN_FUND]
        first = fund[0] if fund else None
        dep[u] = dict(
            funder=first[0] if first else None, amt=first[1] if first else None,
            fund_slot=first[2] if first else None, pre=f.get("pre_create_txs"),
            fresh=(f.get("pre_create_txs", 99) <= 3), first_slot=f.get("first_slot"),
            outs=f.get("outs", []), create_slot=c0["slot"],
        )
    return dep


def funder_kinds(dep):
    """Heuristic: a funder that funded many deployers with many distinct amounts = exchange/hot wallet."""
    by = collections.defaultdict(list)
    for u, d in dep.items():
        if d["funder"]:
            by[d["funder"]].append(d["amt"])
    kind = {}
    for f, amts in by.items():
        distinct = len({a // 100_000 for a in amts})
        kind[f] = "hot" if (len(amts) >= 20 and distinct >= 0.5 * len(amts)) else ("mother" if len(amts) >= 3 else "single")
    return kind


def launch_keys(c, dep, fkind, deployer_seq):
    """Pattern keys for a launch. deployer_seq: index of this launch among the deployer's launches (0-based)."""
    d = dep.get(c["user"], {})
    keys = []
    if d.get("funder"):
        amt = d["amt"]
        keys.append(("fund_amt", d["funder"], amt // 100_000))  # funder + amount to 0.0001 SOL
        if fkind.get(d["funder"]) == "mother":
            keys.append(("mother", d["funder"]))
    keys.append(("deployer", c["user"]))
    # bot fingerprint of the create tx: dev-buy size bucket, prio bucket, tip service, creator-fee setting
    dev = sum(x[1] for x in c["dev_buys"] if x[0] == c["user"])
    tipsvc = c["tips"][0][0] if c["tips"] else "none"
    prio = c.get("prio") or 0
    keys.append(("fp", round(dev / 1e9, 2), min(prio // 10_000, 50), tipsvc, c.get("cfee_bps"), c.get("cashback"),
                 len(c["ixs"])))
    return keys
