"""Causal feature model: predict execution-realistic PnL from information available when our buy is sent.

Strict time split: train (first 60%), validate (next 20%) for threshold choice, test (last 20%) untouched.
"""
from __future__ import annotations

import collections
import math
import sys
from urllib.parse import urlparse

import lightgbm as lgb
import numpy as np
import pandas as pd

from features import deployer_table, funder_kinds, load_creates, load_funding

BF = "/home/user/rug3/data/bf/"
TARGETS = ["b1|tp100_t180", "b1|tp50_t60", "b2|tp100_t180", "b0end|tp100_t180", "b1|tp100_t600", "b1|tp30_t30"]
DELAY = 900  # slots until a launch's outcome is known (t180 exit)


def causal_key_stats(df, key, col, delay=DELAY):
    """past mean/count of `col` for the same key, only from launches whose outcome is already known."""
    past_sum = collections.defaultdict(float)
    past_n = collections.defaultdict(int)
    pending = collections.deque()
    means = np.full(len(df), np.nan)
    counts = np.zeros(len(df))
    s0 = df["s0"].values
    keys = df[key].values
    vals = df[col].values
    for i in range(len(df)):
        while pending and pending[0][0] <= s0[i]:
            _, k, v = pending.popleft()
            past_sum[k] += v
            past_n[k] += 1
        k = keys[i]
        if isinstance(k, str):
            n = past_n.get(k, 0)
            counts[i] = n
            if n:
                means[i] = past_sum[k] / n
            pending.append((s0[i] + delay, k, vals[i]))
    return means, counts


def build():
    df = pd.read_parquet(BF + "sim_24h.parquet").sort_values("s0").reset_index(drop=True)
    C = load_creates(BF + "creates_24h.jsonl.gz")
    F = load_funding(BF + "funding_24h.jsonl.gz")
    cm = {c["mint"]: c for c in C}
    dep = deployer_table(C, F)
    fk = funder_kinds(dep)
    # deployer sequence number (all launches, not just candidates)
    seq = {}
    cnt = collections.Counter()
    for c in sorted(C, key=lambda c: (c["slot"], c["idx"] or 0)):
        seq[c["mint"]] = cnt[c["user"]]
        cnt[c["user"]] += 1
    feats = []
    for r in df.itertuples():
        c = cm[r.mint]
        d = dep.get(r.user, {})
        uri = c.get("uri") or ""
        host = urlparse(uri).netloc if uri.startswith("http") else uri[:8]
        tips = sum(t[1] for t in c.get("tips") or [])
        feats.append(dict(
            dev_sol=r.dev, bund_sol=r.bund_sol, pb_mcap=r.pb_mcap,
            prio=c.get("prio") or 0, tip=tips, tipsvc=(c["tips"][0][0] if c.get("tips") else "none"),
            cfee=c.get("cfee_bps") or 0, cashback=int(bool(c.get("cashback"))), nix=len(c.get("ixs") or []),
            name_len=len(c.get("name") or ""), sym_len=len(c.get("symbol") or ""), host=host[:40],
            fresh=int(bool(d.get("fresh"))), pre=d.get("pre") if d.get("pre") is not None else 99,
            fund_sol=(d.get("amt") or 0) / 1e9,
            lag=math.log1p(max(0, (d.get("create_slot", 0) - d.get("fund_slot", 0)))) if d.get("fund_slot") else -1,
            fkind={"hot": 0, "mother": 1, "single": 2}.get(fk.get(d.get("funder")), 3),
            seq=seq.get(r.mint, 0), hour=((r.s0 * 0.212) / 3600) % 24,
        ))
    X = pd.DataFrame(feats)
    for cat in ["tipsvc", "host"]:
        top = X[cat].value_counts().index[:30]
        X[cat] = pd.Categorical(X[cat].where(X[cat].isin(top), "other"))
    for key in ["k_mother", "k_fund_amt", "k_deployer", "k_fp"]:
        m, n = causal_key_stats(df, key, "b1|tp100_t180")
        X[f"{key}_mean"] = m
        X[f"{key}_n"] = n
        m2, _ = causal_key_stats(df, key, "b0end|tp100_t180")
        X[f"{key}_mean0"] = m2
    return df, X


def evaluate(df, X, target, q_list=(0.02, 0.05, 0.1, 0.2)):
    n = len(df)
    tr, va = int(n * 0.6), int(n * 0.8)
    y = df[target].clip(-0.3, 0.6).values
    params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=60,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1, seed=7)
    ds = lgb.Dataset(X.iloc[:tr], y[:tr])
    dv = lgb.Dataset(X.iloc[tr:va], y[tr:va])
    m = lgb.train(params, ds, 600, valid_sets=[dv], callbacks=[lgb.early_stopping(50, verbose=False)])
    pv = m.predict(X.iloc[tr:va])
    pt = m.predict(X.iloc[va:])
    real_v = df[target].values[tr:va]
    real_t = df[target].values[va:]
    out = []
    for q in q_list:
        thr = np.quantile(pv, 1 - q)
        sel_v = real_v[pv >= thr]
        sel_t = real_t[pt >= thr]
        def st(a):
            if len(a) < 3:
                return (len(a), np.nan, np.nan, np.nan)
            return (len(a), a.mean(), a.sum(), a.mean() / (a.std(ddof=1) / np.sqrt(len(a))))
        out.append(dict(target=target, q=q, thr=thr, val=st(sel_v), test=st(sel_t)))
    imp = sorted(zip(m.feature_importance("gain"), X.columns), reverse=True)[:12]
    return m, out, imp


if __name__ == "__main__":
    df, X = build()
    print(len(df), "launches;", X.shape[1], "features")
    for tgt in TARGETS:
        m, out, imp = evaluate(df, X, tgt)
        print(f"\n=== target {tgt}   (test = last 20% of the day, never seen in training or threshold choice)")
        for o in out:
            v, t = o["val"], o["test"]
            print(f"  top {o['q']*100:4.0f}%: VAL n={v[0]:4d} mean={v[1]:+.4f} tot={v[2]:+6.2f} t={v[3]:+.1f} | "
                  f"TEST n={t[0]:4d} mean={t[1]:+.4f} tot={t[2]:+6.2f} t={t[3]:+.1f}")
        print("  top features:", [(f, int(g)) for g, f in imp[:8]])
