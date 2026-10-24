"""Backfill every pump.fun create in a slot range (Triton getTransactionsForAddress on the mint-authority PDA).

usage: python3 backfill_creates.py HOURS OUT.jsonl.gz
"""
from __future__ import annotations

import asyncio
import gzip
import sys
import time

import orjson

from pumpdecode import MINT_AUTHORITY, parse_tx
from rpc import Rpc

WINDOW = 2000


def row_from_tx(t: dict) -> list[dict]:
    ti = parse_tx(t)
    creates = [e for n, e in ti.events if n == "create"]
    trades = [e for n, e in ti.events if n == "trade"]
    nsig = t["transaction"]["message"]["header"]["numRequiredSignatures"]
    out = []
    for c in creates:
        tr = [x for x in trades if x["mint"] == c["mint"]]
        out.append({
            "slot": ti.slot, "idx": ti.tx_index, "bt": ti.block_time, "sig": ti.sig, "ok": ti.ok,
            "signer": ti.signer, "nsig": nsig, "user": c["user"], "creator": c["creator"], "mint": c["mint"],
            "bc": c["bonding_curve"], "name": c["name"][:64], "symbol": c["symbol"][:32], "uri": c["uri"][:200],
            "token_program": c.get("token_program"), "mayhem": c.get("is_mayhem_mode"),
            "cashback": c.get("is_cashback_enabled"), "quote": c.get("quote_mint"),
            "cfee_bps": c.get("creator_fee_bps"), "depth": c.get("depth"),
            "vsol0": c["virtual_sol_reserves"], "vtok0": c["virtual_token_reserves"],
            "dev_buys": [(x["user"], x["sol_amount"], x["token_amount"], x["virtual_sol_reserves"],
                          x["virtual_token_reserves"], x.get("ix_name")) for x in tr],
            "fee": ti.fee, "prio": ti.fee - 5000 * nsig, "cu": ti.cu_consumed, "tips": ti.tips,
            "ixs": ti.pump_ixs,
        })
    return out


async def main():
    hours = float(sys.argv[1])
    out_path = sys.argv[2]
    end_ago = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0  # window ends this many hours ago
    async with Rpc(rps=80, concurrency=40) as rpc:
        tip = await rpc.call("getSlot", [{"commitment": "finalized"}])
        s1 = tip - int(end_ago * 3600 / 0.212)
        s0 = s1 - int(hours * 3600 / 0.212)
        print(f"slots {s0}..{s1} ({s1 - s0} slots)", flush=True)
        windows = [(a, min(a + WINDOW, s1)) for a in range(s0, s1, WINDOW)]
        rows: list[dict] = []
        t0 = time.time()
        done = 0

        async def run(w):
            nonlocal done
            async for t in rpc.txs_for_address(MINT_AUTHORITY, slot_gte=w[0], slot_lt=w[1], status="succeeded"):
                try:
                    rows.extend(row_from_tx(t))
                except Exception as e:  # keep going; count failures
                    print("parse error", t.get("slot"), e, flush=True)
            done += 1
            if done % 10 == 0:
                print(f"{done}/{len(windows)} windows, {len(rows)} creates, {rpc.calls} calls, "
                      f"{rpc.bytes / 1e6:.0f} MB, {time.time() - t0:.0f}s", flush=True)

        await asyncio.gather(*(run(w) for w in windows))
        rows.sort(key=lambda r: (r["slot"], r["idx"] or 0))
        with gzip.open(out_path, "wb") as f:
            for r in rows:
                f.write(orjson.dumps(r) + b"\n")
        print(f"wrote {len(rows)} creates; calls={rpc.calls} bytes={rpc.bytes / 1e6:.0f}MB errors={rpc.errors}")


if __name__ == "__main__":
    asyncio.run(main())
