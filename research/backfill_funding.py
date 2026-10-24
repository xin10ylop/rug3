"""For every creator in a creates file: first inbound funding, wallet freshness, and last outbound transfers.

usage: python3 backfill_funding.py creates.jsonl.gz out.jsonl.gz
"""
from __future__ import annotations

import asyncio
import gzip
import struct
import sys
import time

import base58
import orjson

from pumpdecode import PUMP, SYSTEM
from rpc import Rpc


def sol_moves(t: dict, who: str):
    """Yield (direction, counterparty, lamports, kind) for SOL moves touching `who` in tx t."""
    msg = t["transaction"]["message"]
    meta = t["meta"]
    la = meta.get("loadedAddresses") or {}
    allk = list(msg["accountKeys"]) + list(la.get("writable", [])) + list(la.get("readonly", []))
    ixs = [(allk[i["programIdIndex"]], i["accounts"], i["data"]) for i in msg["instructions"]]
    for inn in meta.get("innerInstructions") or []:
        ixs += [(allk[i["programIdIndex"]], i["accounts"], i["data"]) for i in inn["instructions"]]
    found = False
    for prog, accs, data in ixs:
        if prog != SYSTEM or len(accs) < 2:
            continue
        b = base58.b58decode(data)
        if len(b) < 4:
            continue
        kind = struct.unpack_from("<I", b, 0)[0]
        if kind == 2 and len(b) >= 12:  # Transfer
            lam = struct.unpack_from("<Q", b, 4)[0]
            frm, to = allk[accs[0]], allk[accs[1]]
        elif kind == 0 and len(b) >= 12:  # CreateAccount(lamports, space, owner)
            lam = struct.unpack_from("<Q", b, 4)[0]
            frm, to = allk[accs[0]], allk[accs[1]]
        elif kind == 11 and len(b) >= 12 and len(accs) >= 3:  # TransferWithSeed
            lam = struct.unpack_from("<Q", b, 4)[0]
            frm, to = allk[accs[0]], allk[accs[2]]
        else:
            continue
        if to == who and frm != who:
            found = True
            yield ("in", frm, lam, kind)
        elif frm == who and to != who:
            found = True
            yield ("out", to, lam, kind)
    if not found:
        # fallback: balance delta (e.g. program-mediated funding)
        try:
            i = allk.index(who)
        except ValueError:
            return
        d = meta["postBalances"][i] - meta["preBalances"][i]
        if d > 0:
            deltas = [(meta["postBalances"][j] - meta["preBalances"][j], allk[j]) for j in range(len(meta["preBalances"]))]
            src = min(deltas)[1]
            yield ("in?", src, d, -1)


def touches_pump(t: dict) -> bool:
    msg = t["transaction"]["message"]
    la = t["meta"].get("loadedAddresses") or {}
    allk = list(msg["accountKeys"]) + list(la.get("writable", [])) + list(la.get("readonly", []))
    return PUMP in allk


async def main():
    creates_path, out_path = sys.argv[1], sys.argv[2]
    creates = [orjson.loads(l) for l in gzip.open(creates_path)]
    first_create: dict[str, int] = {}
    for c in creates:
        first_create.setdefault(c["user"], c["slot"])
    creators = list(first_create)
    print(f"{len(creators)} unique creators", flush=True)
    out: list[dict] = []
    t0 = time.time()

    async with Rpc(rps=85, concurrency=48) as rpc:
        async def one(cr: str):
            rec = {"creator": cr, "first_create_slot": first_create[cr]}
            try:
                first: list[dict] = []
                async for t in rpc.txs_for_address(cr, order="asc", limit=12, max_pages=1):
                    first.append(t)
                rec["n_first"] = len(first)
                rec["first_slot"] = first[0]["slot"] if first else None
                rec["first_bt"] = first[0].get("blockTime") if first else None
                fund = []
                pre_create_txs = 0
                for t in first:
                    if t["slot"] < first_create[cr] or (t["slot"] == first_create[cr] and not touches_pump(t)):
                        pre_create_txs += 1
                    for d, cp, lam, kind in sol_moves(t, cr):
                        if d.startswith("in"):
                            fund.append((cp, lam, t["slot"], t.get("blockTime"), t.get("transactionIndex"), d))
                rec["pre_create_txs"] = pre_create_txs  # among first 12
                rec["fund"] = fund[:4]
                last: list[dict] = []
                async for t in rpc.txs_for_address(cr, order="desc", limit=10, max_pages=1):
                    last.append(t)
                outs = []
                for t in last:
                    for d, cp, lam, kind in sol_moves(t, cr):
                        if d == "out":
                            outs.append((cp, lam, t["slot"], t.get("blockTime")))
                rec["last_slot"] = last[0]["slot"] if last else None
                rec["outs"] = outs[:6]
            except Exception as e:
                rec["error"] = str(e)[:200]
            out.append(rec)
            if len(out) % 2000 == 0:
                print(f"{len(out)}/{len(creators)} creators, {rpc.calls} calls, {rpc.bytes / 1e6:.0f} MB, "
                      f"{time.time() - t0:.0f}s", flush=True)

        # bounded fan-out
        q: asyncio.Queue = asyncio.Queue()
        for c in creators:
            q.put_nowait(c)

        async def worker():
            while not q.empty():
                await one(q.get_nowait())

        await asyncio.gather(*(worker() for _ in range(48)))
        print(f"done: calls={rpc.calls} bytes={rpc.bytes / 1e6:.0f}MB errors={rpc.errors}", flush=True)

    with gzip.open(out_path, "wb") as f:
        for r in out:
            f.write(orjson.dumps(r) + b"\n")


if __name__ == "__main__":
    asyncio.run(main())
