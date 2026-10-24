"""Early, journal-free read of probe landings (approximate: matches each probe to the latest preceding
SOL-paired non-mayhem create). The exact evaluation is probe_eval.py on the box (uses the journal)."""
import asyncio, collections, sys
import numpy as np
from pumpdecode import MINT_AUTHORITY, parse_tx
from rpc import Rpc
W = sys.argv[1]
SOLQ = '11111111111111111111111111111111'

async def main():
    async with Rpc(rps=25, concurrency=8) as rpc:
        probes = []
        async for t in rpc.txs_for_address(W, order='asc', limit=100, max_pages=5, status='any'):
            ti = parse_tx(t)
            if ti.signer == W and not ti.pump_ixs:
                probes.append((ti.slot, ti.tx_index, ti.sig, ti.ok))
        if not probes:
            print('no probes yet'); return
        s0, s1 = probes[0][0] - 40, probes[-1][0] + 1
        creates = []
        async for t in rpc.txs_for_address(MINT_AUTHORITY, order='asc', limit=100, max_pages=50, slot_gte=s0, slot_lt=s1, status='succeeded'):
            ti = parse_tx(t)
            for n, e in ti.events:
                if n == 'create' and e.get('quote_mint') == SOLQ and not e.get('is_mayhem_mode'):
                    creates.append((ti.slot, ti.tx_index, e['mint'], e['user']))
        creates.sort()
        res = []
        for (ps, pi, sig, ok) in probes:
            prev = [c for c in creates if (c[0], c[1]) < (ps, pi)]
            if not prev: continue
            c = prev[-1]
            res.append(dict(gap=ps - c[0], pidx=pi, cidx=c[1], mint=c[2], dep=c[3], pslot=ps, cslot=c[0]))
        # rivals for same-slot landings
        async def rivals(r):
            seq = []
            async for t in rpc.txs_for_address(r['mint'], order='asc', limit=60, max_pages=1, slot_gte=r['cslot'], slot_lt=r['cslot'] + 3):
                ti = parse_tx(t)
                buys = any(n == 'trade' and e['is_buy'] for n, e in ti.events)
                seq.append((ti.slot, ti.tx_index or 0, buys, ti.signer, ti.ok))
            end = r['cidx']
            for x in sorted(x for x in seq if x[0] == r['cslot'] and x[1] > r['cidx']):
                if x[1] == end + 1: end = x[1]
                else: break
            riv = [x for x in seq if x[0] == r['cslot'] and x[1] > end and x[2] and x[4] and x[3] != r['dep']]
            r['rivals0'] = len(riv)
            r['ahead'] = sum(1 for x in riv if (r['gap'] > 0) or x[1] < r['pidx'])
        await asyncio.gather(*(rivals(r) for r in res))
    gaps = collections.Counter(r['gap'] for r in res)
    print(f"{len(probes)} probes landed; matched {len(res)} to creates")
    print('landing slot - create slot:', dict(sorted(gaps.items())[:12]))
    same = [r for r in res if r['gap'] == 0]
    print(f"same slot as create: {len(same)} ({len(same)/max(len(res),1):.0%})" + (f"; in-block index after create p50 {np.median([r['pidx']-r['cidx'] for r in same]):.0f}" if same else ''))
    con = [r for r in res if r['rivals0'] > 0]
    if con:
        win = sum(1 for r in con if r['ahead'] == 0)
        print(f"contested (rival buys in create slot): {len(con)}; ahead of all rivals in {win} ({win/len(con):.0%})")

asyncio.run(main())
