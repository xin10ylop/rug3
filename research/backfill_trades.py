"""Backfill the first N minutes of trades (incl. failed attempts) for candidate launches.

usage: python3 backfill_trades.py creates.jsonl.gz OUT.jsonl.gz [minutes=10] [max_deployer_launches=20]
Each output line = one launch: {"mint", "bc", "create_slot", "rows": [...]}; each row is one trade event or
one failed attempt, in exact chain order (slot, transactionIndex).
"""
from __future__ import annotations

import asyncio
import collections
import gzip
import struct
import sys
import time

import base58
import orjson

from pumpdecode import PUMP, IX_DISC, parse_tx
from rpc import Rpc

SOLQ = "11111111111111111111111111111111"
SLOT_S = 0.212


def failed_attempt(t: dict, mint: str):
    """Describe a failed tx's pump instruction (name + args) without execution data."""
    msg = t["transaction"]["message"]
    meta = t["meta"]
    la = meta.get("loadedAddresses") or {}
    allk = list(msg["accountKeys"]) + list(la.get("writable", [])) + list(la.get("readonly", []))
    for ix in msg["instructions"]:
        if allk[ix["programIdIndex"]] != PUMP:
            continue
        b = base58.b58decode(ix["data"])
        name = IX_DISC.get(b[:8], "?" + b[:8].hex())
        a1 = struct.unpack_from("<Q", b, 8)[0] if len(b) >= 16 else None
        a2 = struct.unpack_from("<Q", b, 16)[0] if len(b) >= 24 else None
        return name, a1, a2
    return None, None, None  # via aggregator / bot program


async def main():
    creates_path, out_path = sys.argv[1], sys.argv[2]
    minutes = float(sys.argv[3]) if len(sys.argv) > 3 else 10
    max_dl = int(sys.argv[4]) if len(sys.argv) > 4 else 20
    C = [orjson.loads(l) for l in gzip.open(creates_path)]
    nl = collections.Counter(c["user"] for c in C)
    cand = [c for c in C if c["quote"] == SOLQ and not c["mayhem"] and nl[c["user"]] <= max_dl]
    nslots = int(minutes * 60 / SLOT_S)
    print(f"{len(cand)} launches, window {nslots} slots", flush=True)
    t0 = time.time()
    done = 0
    fout = gzip.open(out_path, "wb")

    async with Rpc(rps=85, concurrency=48) as rpc:
        async def one(c):
            nonlocal done
            rows = []
            try:
                txs = []
                async for t in rpc.txs_for_address(c["bc"], order="asc", limit=100, slot_gte=c["slot"],
                                                   slot_lt=c["slot"] + nslots, status="succeeded", max_pages=8):
                    txs.append(t)
                async for t in rpc.txs_for_address(c["bc"], order="asc", limit=100, slot_gte=c["slot"],
                                                   slot_lt=c["slot"] + 15, status="failed", max_pages=1):
                    txs.append(t)
                for t in txs:
                    nsig = t["transaction"]["message"]["header"]["numRequiredSignatures"]
                    ti = parse_tx(t)
                    base = {"slot": ti.slot, "idx": ti.tx_index, "sig": ti.sig[:16], "signer": ti.signer,
                            "prio": ti.fee - 5000 * nsig, "cu": ti.cu_consumed, "tips": ti.tip_lamports or None,
                            "tipsvc": ti.tips[0][0] if ti.tips else None, "bt": ti.block_time}
                    if not ti.ok:
                        name, a1, a2 = failed_attempt(t, c["mint"])
                        rows.append(dict(base, ok=0, ixn=name, a1=a1, a2=a2))
                        continue
                    trades = [e for n, e in ti.events if n == "trade" and e["mint"] == c["mint"]]
                    if not trades:
                        continue
                    for e in trades:
                        rows.append(dict(base, ok=1, user=e["user"], buy=int(e["is_buy"]), sol=e["sol_amount"],
                                         tok=e["token_amount"], vsol=e["virtual_sol_reserves"],
                                         vtok=e["virtual_token_reserves"], fee=e["fee"], cfee=e["creator_fee"],
                                         ixn=e.get("ix_name"), cb=e.get("cashback") or None))
            except Exception as ex:
                rows.append({"error": str(ex)[:200]})
            fout.write(orjson.dumps({"mint": c["mint"], "bc": c["bc"], "user": c["user"], "create_slot": c["slot"],
                                     "create_idx": c["idx"], "rows": rows}) + b"\n")
            done += 1
            if done % 1000 == 0:
                print(f"{done}/{len(cand)} launches, {rpc.calls} calls, {rpc.bytes / 1e6:.0f} MB, "
                      f"{time.time() - t0:.0f}s", flush=True)

        q: asyncio.Queue = asyncio.Queue()
        for c in cand:
            q.put_nowait(c)

        async def worker():
            while not q.empty():
                await one(q.get_nowait())

        await asyncio.gather(*(worker() for _ in range(48)))
        print(f"done: calls={rpc.calls} bytes={rpc.bytes / 1e6:.0f}MB errors={rpc.errors}", flush=True)
    fout.close()


if __name__ == "__main__":
    asyncio.run(main())
