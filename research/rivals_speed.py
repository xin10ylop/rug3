"""From recorder data: how fast do slot-0 snipers respond to a create? (deshred receive-time gap + index gap)"""
import gzip, glob, orjson, collections, numpy as np, zlib

def rows(kind):
    for f in sorted(glob.glob(f'/home/user/rug3/data/rec/{kind}-*.jsonl.gz')):
        try:
            with gzip.open(f) as fh:
                for l in fh:
                    try: yield orjson.loads(l)
                    except Exception: break
        except (EOFError, zlib.error, OSError): pass

ds = {}
for r in rows('deshred'): ds[r['sig']] = r['t']
creates = {}; buys = collections.defaultdict(list)
for r in rows('proc'):
    for e in r['ev']:
        if e['ev']=='create' and e.get('quote')=='11111111111111111111111111111111' and not e.get('mayhem'):
            creates[e['mint']] = (r['slot'], r['idx'], r['sig'], r['signer'])
        elif e['ev']=='trade' and e['buy']:
            buys[e['mint']].append((r['slot'], r['idx'], r['sig'], e['user'], r['signer'], int(e['sol']), r['fee']-5000*r['nsig'], sum(t[1] for t in r.get('tips') or []), (r.get('tips') or [[None]])[0][0], r['ok']))
gaps=[]; idxgaps=[]; same=0; tot=0; prios=[]; tipsvc=collections.Counter(); per_launch=[]
for m,(s0,i0,sig0,dev) in creates.items():
    if sig0 not in ds: continue
    t0 = ds[sig0]
    bl = sorted(b for b in buys.get(m,[]) if b[0]==s0 and b[1] > i0)
    # exclude contiguous bundle (idx i0+1, i0+2, ... consecutive)
    end=i0; out=[]
    for b in bl:
        if b[1] in (end, end+1) and not out: end=b[1]; continue
        out.append(b)
    tot+=1
    if out: same+=1
    for b in out:
        if b[2] in ds and b[4]!=dev:
            gaps.append(ds[b[2]]-t0); idxgaps.append(b[1]-i0); prios.append(b[6]); tipsvc[b[8]]+=1
print(f'launches with create seen in deshred: {tot}; with non-bundle buys in the create slot: {same} ({same/max(tot,1):.1%})')
g=np.array(gaps)
if len(g):
    print('deshred receive-time gap create -> rival slot-0 buy (ms): p10 %.0f p25 %.0f p50 %.0f p75 %.0f p90 %.0f'%tuple(np.percentile(g,[10,25,50,75,90])))
    print('tx-index gap: p10 %d p50 %d p90 %d'%tuple(np.percentile(idxgaps,[10,50,90])))
    print('rival prio fee (lamports): p10 %d p50 %d p90 %d'%tuple(np.percentile(prios,[10,50,90])))
    print('tip services of slot-0 rivals:', tipsvc.most_common(8))
