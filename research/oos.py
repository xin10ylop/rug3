"""Out-of-sample test of the frozen live rule across days (chronological: d3 -> d2 -> 24h)."""
import collections, sys
import numpy as np, pandas as pd
from analyze import BUY_TX_COST, SELL_TX_COST, FAIL_RATE, FAIL_COST
BF = "/home/user/rug3/data/bf/"
ADJ = (BUY_TX_COST + SELL_TX_COST + FAIL_RATE * FAIL_COST - 600_000) / 1e9   # sim costs -> live low-fee costs
N_MIN, MEAN_MIN, LAST_N, DELAY = 5, 0.005, 30, 2000

def load(tag):
    d = pd.read_parquet(BF + f"sim_{tag}.parquet")
    d["day"] = tag
    for s in ("b1", "b2", "b4"):
        for e in ("tp30_t30", "tp50_t60", "tp100_t180"):
            d[f"{s}|{e}"] = d[f"{s}|{e}"] + ADJ
    return d

def run(df, score_cols, eval_cols):
    df = df.sort_values("s0").reset_index(drop=True)
    past = collections.defaultdict(list); pend = collections.deque(); picks = []
    for i, r in enumerate(df.itertuples(index=False)):
        while pend and pend[0][0] <= r.s0:
            _, k, v = pend.popleft(); past[k].append(v)
        k = r.k_fund_amt
        if isinstance(k, str):
            h = past.get(k, [])[-LAST_N:]
            if len(h) >= N_MIN and np.mean(h) >= MEAN_MIN:
                picks.append(i)
            pend.append((r.s0 + DELAY, k, float(np.mean([getattr(r, c) if False else df.at[i, c] for c in score_cols]))))
    sel = df.loc[picks]
    return sel

def st(p):
    p = np.asarray(p)
    if len(p) < 3: return f"n={len(p)}"
    return f"n={len(p):4d} mean={p.mean():+.4f} ({p.mean()/0.25*100:+.1f}%) tot={p.sum():+6.2f} win={np.mean(p>0):.2f} t={p.mean()/(p.std(ddof=1)/np.sqrt(len(p))):+.1f}"

if __name__ == "__main__":
    tags = [t for t in ["d3", "d2", "24h"] if __import__("os").path.exists(BF + f"sim_{t}.parquet")]
    df = pd.concat([load(t) for t in tags], ignore_index=True)
    print("days:", tags, "launches:", len(df))
    for score in (["b2|tp30_t30", "b4|tp30_t30"],):
        sel = run(df, score, None)
        print(f"\nFROZEN RULE (score={'+'.join(score)}; n>={N_MIN}; mean_last{LAST_N}>={MEAN_MIN}; delay={DELAY} slots)")
        for day in tags:
            s = sel[sel.day == day]
            print(f"  day {day:4s}: realized b2 {st(s['b2|tp30_t30'])} | b4 {st(s['b4|tp30_t30'])}")
            print(f"            no-rival@0 b2 {st(s[s.riv0==0]['b2|tp30_t30'])} | keys {s.k_fund_amt.nunique()}")
        print(f"  ALL     : realized b2 {st(sel['b2|tp30_t30'])} | b1 {st(sel['b1|tp30_t30'])} | b4 {st(sel['b4|tp30_t30'])}")
        # cross-day persistence: keys that qualified on an earlier day and fired on a later day
        first_day = sel.groupby("k_fund_amt").day.first()
