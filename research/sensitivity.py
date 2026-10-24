import pandas as pd, numpy as np, gzip, orjson
from walkforward import walk, DELAY
from analyze import iter_trades, BF, BUY_TX_COST, SELL_TX_COST, FAIL_RATE, FAIL_COST
from sim import load_launch, simulate
from anatomy import bundle_end
df=pd.read_parquet(BF+'sim_24h.parquet').sort_values('s0').reset_index(drop=True)
sel_idx=set()
for fit in ['b2','b4']:
    tr=walk(df, f'{fit}|tp30_t30', 'k_fund_amt', 5, 0.005, DELAY['tp30_t30'])
    sel_idx|= {i for i,_ in tr}
mints=set(df.loc[list(sel_idx),'mint'])
print('selected launches (union of b2/b4-fitted fund_amt rule):', len(mints))
Ls={}
for d in iter_trades(BF+'trades_24h.jsonl.gz'):
    if d['mint'] in mints:
        L=load_launch(d)
        if L: Ls[d['mint']]=L
def run(d_buy=2, d_sell=2, size=0.25, tp=0.3, hold=142, extra=0):
    p=[]
    for m,L in Ls.items():
        r=simulate(L, buy_sol=size, d_buy=d_buy, where='end', tp=tp, max_hold_slots=hold, d_sell=d_sell)
        p.append((r.pnl_lamports - BUY_TX_COST - SELL_TX_COST - FAIL_RATE*FAIL_COST - extra)/1e9)
    p=np.array(p); return f"n={len(p)} mean={p.mean():+.4f} ({p.mean()/size*100:+.1f}%/trade) tot={p.sum():+.2f} t={p.mean()/(p.std(ddof=1)/np.sqrt(len(p))):+.1f}"
print('base (b2 entry, sell lands +2 slots, 0.25 SOL, TP+30%, 30s):', run())
for db in [1,3,4,6,10]: print(f'  entry slot +{db}:', run(d_buy=db))
for ds in [1,3,4,6]: print(f'  sell lands +{ds} slots:', run(d_sell=ds))
for sz in [0.1,0.5,1.0]: print(f'  size {sz} SOL:', run(size=sz))
for ex in [1_000_000, 3_000_000]: print(f'  +{ex/1e9} SOL extra cost per trade:', run(extra=ex))
for tp,hold in [(0.2,142),(0.5,142),(0.3,70),(0.3,285)]: print(f'  TP+{int(tp*100)}% hold {hold*0.212:.0f}s:', run(tp=tp,hold=hold))
