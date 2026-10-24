"""Walk-forward (no look-ahead) selection by pattern key on execution-realistic PnL."""
from __future__ import annotations

import collections
import itertools
import sys

import numpy as np
import pandas as pd

KEYS = ["k_fund_amt", "k_mother", "k_deployer", "k_fp"]
DELAY = {"tp30_t30": 200, "tp50_t60": 350, "tp100_t180": 900, "tp100_t600": 2900}


def walk(df: pd.DataFrame, col: str, key: str, n_min: int, m_min: float, delay: int, hit_min: float = 0.0):
    """Return list of (idx, pnl) for launches traded by the rule. df must be sorted by s0."""
    past = collections.defaultdict(list)  # key -> list of (s0, pnl)
    trades = []
    s0 = df["s0"].values
    keys = df[key].values
    pnl = df[col].values
    # pending outcomes become visible after `delay` slots
    pending = collections.deque()
    for i in range(len(df)):
        while pending and pending[0][0] <= s0[i]:
            _, k, p = pending.popleft()
            past[k].append(p)
        k = keys[i]
        if k is not None and k == k:  # not NaN
            h = past.get(k)
            if h and len(h) >= n_min:
                arr = np.asarray(h[-30:])  # most recent 30 outcomes
                if arr.mean() >= m_min and (arr > 0).mean() >= hit_min:
                    trades.append((i, pnl[i]))
            pending.append((s0[i] + delay, k, pnl[i]))
    return trades


def summarize(trades, label):
    if not trades:
        return dict(rule=label, n=0)
    p = np.array([t[1] for t in trades])
    return dict(rule=label, n=len(p), mean=p.mean(), total=p.sum(), win=(p > 0).mean(),
                t=p.mean() / (p.std(ddof=1) / np.sqrt(len(p))) if len(p) > 2 else np.nan,
                med=np.median(p))


def main():
    df = pd.read_parquet("/home/user/rug3/data/bf/sim_24h.parquet").sort_values("s0").reset_index(drop=True)
    rows = []
    for scen, ex, key, n_min, m_min in itertools.product(["b0start", "b0end", "b1", "b2"],
                                                          ["tp30_t30", "tp50_t60", "tp100_t180"],
                                                          KEYS, [3, 5, 10], [0.0, 0.01]):
        col = f"{scen}|{ex}"
        tr = walk(df, col, key, n_min, m_min, DELAY[ex])
        r = summarize(tr, f"{scen} {ex} {key} n>={n_min} m>={m_min}")
        r.update(scen=scen, ex=ex, key=key, n_min=n_min, m_min=m_min)
        rows.append(r)
    out = pd.DataFrame(rows)
    out.to_csv("/home/user/rug3/data/bf/walkforward.csv", index=False)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    cols = ["scen", "ex", "key", "n_min", "m_min", "n", "mean", "total", "win", "t", "med"]
    print(out[out.n >= 20].sort_values("total", ascending=False)[cols].head(40).round(4).to_string())


if __name__ == "__main__":
    main()
