"""Where is each Solana leader? Validator identity -> TPU/gossip IP -> geolocation (ip-api.com, free tier)
-> great-circle km from this box (default Frankfurt). Writes data/live/leaders.json for the bot's race gate.

usage: python3 leaders.py [--origin LAT,LON]

Why: the probe showed that same-slot landing and race wins depend on where the slot's leader is
(we land in the create slot 96% of the time when the leader is in Europe and the create is early in its slot,
22% when the leader is in the US/Asia).
"""
import argparse
import asyncio
import json
import math
import time
import urllib.request

from paths import LIVE
from rpc import Rpc

FRANKFURT = (50.11, 8.68)


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def node_ip(n):
    return (n.get("tpuQuic") or n.get("tpu") or n.get("gossip") or "").rsplit(":", 1)[0]


def geolocate(ips):
    out = {}
    for i in range(0, len(ips), 100):
        req = urllib.request.Request("http://ip-api.com/batch?fields=query,status,country,city,lat,lon,as",
                                     data=json.dumps(ips[i:i + 100]).encode(), headers={"Content-Type": "application/json"})
        for g in json.load(urllib.request.urlopen(req, timeout=20)):
            if g.get("status") == "success":
                out[g["query"]] = g
        if i + 100 < len(ips):
            time.sleep(4.5)  # free tier: 15 batch requests per minute
    return out


async def build(origin=FRANKFURT, path=LIVE + "leaders.json"):
    async with Rpc(rps=5) as rpc:
        va = await rpc.call("getVoteAccounts", [])
        nodes = await rpc.call("getClusterNodes", [])
    stake = {v["nodePubkey"]: v["activatedStake"] for v in va["current"] + va["delinquent"]}
    ip = {n["pubkey"]: node_ip(n) for n in nodes if n["pubkey"] in stake}
    geo = geolocate(sorted(set(ip.values()) - {""}))
    leaders = {}
    for k, s in stake.items():
        g = geo.get(ip.get(k, ""))
        leaders[k] = dict(stake=s, ip=ip.get(k), city=g["city"] if g else None, country=g["country"] if g else None,
                          asn=g.get("as") if g else None, km=round(km(origin, (g["lat"], g["lon"])), 1) if g else None)
    json.dump(dict(generated_at=time.time(), origin=list(origin), leaders=leaders), open(path, "w"))
    tot = sum(stake.values()) or 1
    share = lambda f: sum(v["stake"] for v in leaders.values() if f(v["km"])) / tot
    print(f"{len(leaders)} validators, {len(geo)} located -> {path}")
    print(f"stake (= share of leader slots): <100 km {share(lambda d: d is not None and d < 100):.0%} | "
          f"100-1200 km {share(lambda d: d is not None and 100 <= d < 1200):.0%} | "
          f">=1200 km {share(lambda d: d is not None and d >= 1200):.0%} | unknown {share(lambda d: d is None):.0%}")
    return leaders


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default=f"{FRANKFURT[0]},{FRANKFURT[1]}", help="LAT,LON of this box")
    a = ap.parse_args()
    asyncio.run(build(tuple(float(x) for x in a.origin.split(","))))
