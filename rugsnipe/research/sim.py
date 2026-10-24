"""Execution-realistic simulation of buying a pump.fun launch and exiting, on exact historical trade sequences.

Key rules (to avoid paper-fill bias):
  * We never assume we are first. Our buy is inserted at a position implied by a latency model:
    landing slot = create_slot + d_buy, and within that slot either after ALL historical txs ("end")
    or right after the create's bundle ("start", only meaningful for d_buy == 0).
  * Everything that historically happened before our insertion point happened before us (rivals included).
  * After our buy, every later historical trade is re-applied on top of our position (buys keep their SOL,
    sells keep their tokens), so our own price impact is carried forward.
  * Exits trigger on what we would observe (end of each slot), and the sell lands d_sell slots LATER,
    after all historical txs of the landing slot (pessimistic: a same-slot dump executes before us).
  * Costs: 1.25% pump fee each side (actual bps from events), buy prio+tip, sell prio+tip, base fees,
    and failed-attempt costs are charged by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass

SOL = 1_000_000_000
SUPPLY_RAW = 1_000_000_000_000_000
VSOL0 = 30 * SOL
VTOK0 = 1_073_000_000_000_000


def mcap_sol(vsol: int, vtok: int) -> float:
    return vsol / vtok * SUPPLY_RAW / SOL


def curve_buy(vsol: int, vtok: int, sol_net: int) -> tuple[int, int, int]:
    k = vsol * vtok
    nvsol = vsol + sol_net
    nvtok = -(-k // nvsol)
    return vtok - nvtok, nvsol, nvtok


def curve_sell(vsol: int, vtok: int, tok_in: int) -> tuple[int, int, int]:
    k = vsol * vtok
    nvtok = vtok + tok_in
    nvsol = -(-k // nvtok)
    return vsol - nvsol, nvsol, nvtok


@dataclass
class Launch:
    mint: str
    user: str
    s0: int
    i0: int
    rows: list  # successful trades only, chain order
    fails: list  # failed attempts (chain order)


def load_launch(d: dict) -> Launch | None:
    rows = [r for r in d["rows"] if r.get("ok") == 1]
    fails = [r for r in d["rows"] if r.get("ok") == 0]
    if not rows or "error" in (d["rows"][0] if d["rows"] else {}):
        return None
    rows.sort(key=lambda r: (r["slot"], r["idx"] or 0))
    return Launch(d["mint"], d["user"], d["create_slot"], d["create_idx"] or 0, rows, fails)


def state_before(launch: Launch, pos: int) -> tuple[int, int]:
    if pos == 0:
        return VSOL0, VTOK0
    r = launch.rows[pos - 1]
    return r["vsol"], r["vtok"]


def insertion_index(launch: Launch, d_buy: int, where: str = "end", bundle_end_idx: int | None = None,
                    at_idx: int | None = None) -> int:
    """Index into launch.rows where our buy is inserted. where='at': our actual landing (slot s0+d_buy, index at_idx)."""
    land = launch.s0 + d_buy
    if where == "at":
        for i, r in enumerate(launch.rows):
            if (r["slot"], r["idx"] or 0) > (land, at_idx):
                return i
        return len(launch.rows)
    if where == "end":
        for i, r in enumerate(launch.rows):
            if r["slot"] > land:
                return i
        return len(launch.rows)
    # 'start' : right after bundle (same slot) or at start of landing slot
    for i, r in enumerate(launch.rows):
        if r["slot"] > launch.s0 + d_buy or (r["slot"] == land and d_buy == 0 and bundle_end_idx is not None
                                             and (r["idx"] or 0) > bundle_end_idx) or \
                (r["slot"] == land and d_buy > 0):
            return i
    return len(launch.rows)


@dataclass
class SimResult:
    entered: bool
    entry_mcap: float = 0.0
    tokens: int = 0
    cost_lamports: int = 0  # SOL paid incl. pump fees (excl. tx costs)
    proceeds_lamports: int = 0  # SOL received net of pump fees
    exit_reason: str = ""
    exit_slot_off: int = 0
    peak_mult: float = 0.0  # best observable net multiple during hold
    pnl_lamports: int = 0  # proceeds - cost (excl. tx costs)


def simulate(launch: Launch, *, buy_sol: float, d_buy: int, where: str = "end", bundle_end_idx: int | None = None,
             tp: float = 1.0, sl: float | None = None, max_hold_slots: int = 2830, d_sell: int = 2,
             fee_rate: float = 0.0125, max_entry_mcap: float | None = None, trailing: float | None = None,
             skip_followers_slots: int = 0, sniper_set: set | None = None,
             exit_on_big_sell_frac: float | None = None, at_idx: int | None = None) -> SimResult:
    """Simulate one round trip. tp/sl are net returns (1.0 = +100%)."""
    ins = insertion_index(launch, d_buy, where, bundle_end_idx, at_idx)
    vsol, vtok = state_before(launch, ins)
    em = mcap_sol(vsol, vtok)
    if max_entry_mcap is not None and em > max_entry_mcap:
        return SimResult(False, entry_mcap=em, exit_reason="entry_mcap")
    cost = int(buy_sol * SOL)
    sol_net = int(cost / (1 + fee_rate))
    tokens, vsol, vtok = curve_buy(vsol, vtok, sol_net)
    our_slot = launch.s0 + d_buy

    def value(vs, vt):
        gross, _, _ = curve_sell(vs, vt, tokens)
        return int(gross * (1 - fee_rate))

    peak = 0.0
    trigger_slot = None
    reason = ""
    rows = launch.rows
    i = ins
    n = len(rows)
    cur_slot = our_slot
    hwm = 0.0
    # walk forward slot by slot; observe state at end of each slot
    while i < n:
        s = rows[i]["slot"]
        if s - our_slot > max_hold_slots:
            break
        # apply all trades of slot s
        while i < n and rows[i]["slot"] == s:
            r = rows[i]
            if skip_followers_slots and r["buy"] and sniper_set is not None and r["user"] in sniper_set \
                    and s - launch.s0 <= skip_followers_slots and i >= ins:
                i += 1
                continue  # pessimistic: rival snipers react to our buy and do not buy
            if r["buy"]:
                _, vsol, vtok = curve_buy(vsol, vtok, r["sol"])
            else:
                big = exit_on_big_sell_frac is not None and r["tok"] >= exit_on_big_sell_frac * (VTOK0 - vtok)
                _, vsol, vtok = curve_sell(vsol, vtok, r["tok"])
                if big and trigger_slot is None and s > our_slot:
                    trigger_slot, reason = s, "big_sell"
            i += 1
        cur_slot = s
        if trigger_slot is None and s >= our_slot:
            mult = value(vsol, vtok) / cost
            peak = max(peak, mult)
            hwm = max(hwm, mult)
            if mult - 1 >= tp:
                trigger_slot, reason = s, "tp"
            elif sl is not None and mult - 1 <= -sl and s > our_slot:
                trigger_slot, reason = s, "sl"
            elif trailing is not None and hwm - 1 >= trailing * 0.5 and mult <= hwm * (1 - trailing):
                trigger_slot, reason = s, "trail"
        if trigger_slot is not None:
            # our sell lands at trigger_slot + d_sell, after all historical txs of that slot
            land = trigger_slot + d_sell
            while i < n and rows[i]["slot"] <= land:
                r = rows[i]
                if r["buy"]:
                    _, vsol, vtok = curve_buy(vsol, vtok, r["sol"])
                else:
                    _, vsol, vtok = curve_sell(vsol, vtok, r["tok"])
                i += 1
            proceeds = value(vsol, vtok)
            return SimResult(True, em, tokens, cost, proceeds, reason, land - launch.s0, peak, proceeds - cost)
    # time exit (or data end): sell at last known state (+ d_sell slots of further trades if any)
    proceeds = value(vsol, vtok)
    return SimResult(True, em, tokens, cost, proceeds, "time", cur_slot - launch.s0, peak, proceeds - cost)
