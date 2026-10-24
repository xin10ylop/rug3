"""Per-launch anatomy: bundle, early buyers by slot offset, post-bundle mcap, peak, dump."""
from __future__ import annotations

import collections
import gzip
import zlib

import orjson

from sim import VSOL0, VTOK0, load_launch, mcap_sol


def iter_launches(path: str):
    with gzip.open(path) as f:
        try:
            for line in f:
                try:
                    d = orjson.loads(line)
                except orjson.JSONDecodeError:
                    break
                L = load_launch(d)
                if L:
                    yield d, L
        except (EOFError, zlib.error, OSError):
            return


def bundle_end(L) -> int:
    """Last tx idx of the contiguous run of txs starting at the create tx (Jito bundle) in slot s0."""
    idxs = sorted({r["idx"] for r in L.rows if r["slot"] == L.s0 and r["idx"] is not None})
    end = L.i0
    for x in idxs:
        if x == end or x == end + 1:
            end = x
        elif x > end + 1:
            break
    return end


def summarize(L) -> dict:
    be = bundle_end(L)
    s0 = L.s0
    dev = sum(r["sol"] for r in L.rows if r["slot"] == s0 and r["idx"] == L.i0 and r["buy"]) / 1e9
    bund = [r for r in L.rows if r["slot"] == s0 and L.i0 <= (r["idx"] or 0) <= be and r["buy"]]
    bund_sol = sum(r["sol"] for r in bund) / 1e9
    bund_wallets = len({r["user"] for r in bund})
    after_b = [r for r in L.rows if (r["slot"], r["idx"] or 0) > (s0, be)]
    # post-bundle state
    pb = [r for r in L.rows if (r["slot"], r["idx"] or 0) <= (s0, be)]
    vsol, vtok = (pb[-1]["vsol"], pb[-1]["vtok"]) if pb else (VSOL0, VTOK0)
    pb_mcap = mcap_sol(vsol, vtok)
    early = collections.Counter()
    early_sol = collections.Counter()
    for r in after_b:
        off = r["slot"] - s0
        if off <= 5 and r["buy"]:
            early[off] += 1
            early_sol[off] += r["sol"]
    peak = pb_mcap
    peak_off = 0
    for r in L.rows:
        m = mcap_sol(r["vsol"], r["vtok"])
        if m > peak:
            peak, peak_off = m, r["slot"] - s0
    last = mcap_sol(L.rows[-1]["vsol"], L.rows[-1]["vtok"])
    # biggest single-slot drop after the peak (dump)
    by_slot = collections.OrderedDict()
    for r in L.rows:
        by_slot[r["slot"]] = mcap_sol(r["vsol"], r["vtok"])
    prev = None
    dump = 0.0
    dump_off = None
    for s, m in by_slot.items():
        if prev is not None and prev > 0:
            d = 1 - m / prev
            if d > dump:
                dump, dump_off = d, s - s0
        prev = m
    return dict(mint=L.mint, user=L.user, s0=s0, n=len(L.rows), nfail=len(L.fails), dev=dev, bund_sol=bund_sol,
                bund_w=bund_wallets, pb_mcap=pb_mcap, peak=peak, peak_off=peak_off, peak_x=peak / pb_mcap,
                last=last, dump=dump, dump_off=dump_off,
                **{f"b{k}": early.get(k, 0) for k in range(6)},
                **{f"bs{k}": early_sol.get(k, 0) / 1e9 for k in range(6)})


if __name__ == "__main__":
    import sys

    import numpy as np
    import pandas as pd
    rows = [summarize(L) for _, L in iter_launches(sys.argv[1])]
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(len(df), "launches")
    print(df[["n", "nfail", "dev", "bund_sol", "bund_w", "pb_mcap", "peak_x", "peak_off", "dump", "b0", "b1", "b2",
              "bs0", "bs1"]].describe(percentiles=[.5, .75, .9, .95, .99]).round(2).T)
    for q in [1.5, 2, 3, 5]:
        print(f"share with peak >= {q}x post-bundle within 10 min: {(df.peak_x >= q).mean():.3f}")
    df.to_parquet(sys.argv[2]) if len(sys.argv) > 2 else None
