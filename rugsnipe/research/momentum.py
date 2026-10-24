"""Momentum entries: enter after price reaches k x post-bundle (observed at slot end), land d slots later."""
import sys
import numpy as np, pandas as pd
from analyze import iter_trades, BF, BUY_TX_COST, SELL_TX_COST
from sim import load_launch, simulate, mcap_sol
from anatomy import bundle_end

def first_cross(L, k, max_off):
    be=bundle_end(L); pb=None
    for r in L.rows:
        if (r['slot'], r['idx'] or 0) <= (L.s0, be):
            pb=mcap_sol(r['vsol'],r['vtok']); continue
        if pb is None: pb=mcap_sol(30_000_000_000,1_073_000_000_000_000)
        off=r['slot']-L.s0
        if off>max_off: return None
        if mcap_sol(r['vsol'],r['vtok'])>=k*pb: return off
    return None

rows=[]
for d in iter_trades(BF+'trades_24h.jsonl.gz'):
    L=load_launch(d)
    if not L: continue
    for k in (1.2,1.5,2.0):
        off=first_cross(L,k,400)
        if off is None: continue
        for tp,hold in ((0.3,150),(0.5,300),(1.0,900)):
            res=simulate(L, buy_sol=0.25, d_buy=off+2, where='end', tp=tp, max_hold_slots=hold, d_sell=2)
            rows.append(dict(k=k, tp=tp, hold=hold, pnl=(res.pnl_lamports-BUY_TX_COST-SELL_TX_COST)/1e9))
x=pd.DataFrame(rows)
g=x.groupby(['k','tp','hold']).pnl.agg(n='count', mean='mean', total='sum', win=lambda s:(s>0).mean())
print(g.round(4))
