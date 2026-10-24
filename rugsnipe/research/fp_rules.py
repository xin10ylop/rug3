"""Armed bot fingerprints for the live trigger, using the rule exactly as validated walk-forward (oos day split):
a fingerprint is armed when it has >= 3 simulated outcomes and the mean of its last 30 b0start|tp30_t30 outcomes
is >= 0. Also lists heavy deployers (> 20 SOL-paired launches on the most recent day), whom the simulation excluded.

usage: python3 fp_rules.py [--out PATH] [TAG ...]   (chronological; default d3 d2 24h; default out data/live/fp_rules.json)
The box has no simulation data, so the shipped list lives in bot/configs/fp_rules.json (public chain data only).
"""
import collections
import gzip
import json
import sys
import time

import orjson
import pandas as pd

from paths import BF, LIVE

N_MIN, LAST_N, M_MIN = 3, 30, 0.0
COL = "b0start|tp30_t30"
SOLQ = "11111111111111111111111111111111"


def main():
    args = sys.argv[1:]
    out = LIVE + "fp_rules.json"
    if args[:1] == ["--out"]:
        out, args = args[1], args[2:]
    tags = args or ["d3", "d2", "24h"]
    df = pd.concat([pd.read_parquet(BF + f"sim_{t}.parquet", columns=["s0", "k_fp", COL]) for t in tags])
    df = df.dropna(subset=["k_fp"]).sort_values("s0")
    st = df.groupby("k_fp", sort=False)[COL].agg(n="size", mean_last=lambda v: v.iloc[-LAST_N:].mean())
    armed = st[(st.n >= N_MIN) & (st.mean_last >= M_MIN)]
    creates = (orjson.loads(l) for l in gzip.open(BF + f"creates_{tags[-1]}.jsonl.gz"))
    per = collections.Counter(r["user"] for r in creates if r.get("quote") == SOLQ and not r.get("mayhem"))
    heavy = sorted(u for u, v in per.items() if v > 20)
    json.dump(dict(generated_at=time.time(), source=tags, rule=f"n>={N_MIN} and mean(last {LAST_N} {COL})>={M_MIN}",
                   data_end_slot=int(df.s0.max()), armed=sorted(armed.index), heavy_deployers=heavy),
              open(out, "w"))
    share = df.k_fp.isin(armed.index).mean()
    print(f"{len(armed)} of {len(st)} fingerprints armed (cover {share:.0%} of simulated launches); "
          f"{len(heavy)} heavy deployers -> {out}")


if __name__ == "__main__":
    main()
