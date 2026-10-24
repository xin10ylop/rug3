# Rugger sniping on pump.fun: validation report (2026-10-24)

## Question

Do FOMO / F-Project's two methods have a net edge **in live execution**?

1. **Dev-sniping:** track rugger funding patterns (exchange hot wallet plus an exact amount, mother wallets, chains) and buy the deployer's next launch in block 0.
2. **Copy-trading:** copy the rugger's pump wallets.

"In live execution" means accounting for competition, transaction ordering, failed or missed entries, latency, slippage, fees and tips. Paper fills don't count.

## Data (all from Triton, cost ≈ $6 of credit)

- **Creates:** every pump.fun create for 72 h, 3 × ~47k launches, via `getTransactionsForAddress` on the mint-authority PDA.
- **Funding:** first funding transfer (source, exact lamports, time) for every deployer, ~33k wallets.
- **Trades:** the first 10 minutes of every successful trade, with **slot + in-block index**, signer, priority fee, tip and CU, for 37,669 candidate launches. Candidates are SOL-quoted, not mayhem, deployer ≤20 launches/day. Failed attempts in the first 15 slots are included.
- **Live recorder:** Yellowstone gRPC (Riptide), with processed transactions, the deshred (pre-execution) stream and slot timing.

## How the simulator avoids the "dry run scores races we never win" bias

- Our buy is **placed by a latency model**, never assumed first:
  - `b0start`: first after the bundle in the create slot (the race winner).
  - `b0end`: last in the create slot.
  - `b1`, `b2`, `b4`: end of slot +1/+2/+4.
- Everything that happened before that position (rivals included) happens before us.
- Later trades are replayed **on top of our position**, so our own price impact is carried forward.
- Exits trigger on what we would observe. **The sell lands 2 slots later, after the whole landing slot.** If a dump happens in between, we eat it.
- Costs: 1.25% pump fee per side (0.95% protocol + 0.30% creator, verified on-chain), buy and sell priority fees and tips, base fees, and failed-transaction costs.
- Every rule is selected **walk-forward**: it sees only outcomes known before the decision. Every selected set is **split by "rival sniper in slot 0"**, exactly as in your Oct 21–23 analysis.

## Findings

| test | result |
|---|---|
| snipe every launch | negative at every latency (−0.2% … −7.8%/trade) |
| bot-fingerprint selection, as race winner (`b0start`) | +30.7 SOL/day at 0.25 SOL, t = 7.3 … **but +30.6 of it is on launches where a rival sniper was in slot 0; +0.1 where none was** |
| same selection, last in slot 0 / slot 1 / slot 2 | −2.1 / −14.6 / −18.2 SOL/day |
| "follow the rivals" (enter after known snipers) | −4% … −10%/trade, t = −4 … −9 |
| momentum confirmation (enter after +20/50/100%) | −6% … −11%/trade |
| ML on pre-trade features (time-split test) | no positive out-of-sample edge at any realistic latency |
| (funder, exact amount) rule, latency-robust in-sample | +3.6%/trade, t = 1.7 on day 1 (flat across +1…+6 slot entry) |
| **same rule frozen, on 2 unseen days** | **−4.4% (d-3), −2.4% (d-2); 3-day total −1.7%/trade, t = −1.7** |
| 9 pre-specified variants on unseen days | 7 negative, 2 ≈ 0 |

**Rival speed (live recorder):** snipers that land in the create slot respond in a median **40 ms** after the create's shred (p25 23 ms, p90 108 ms). They pay median 0.000375 SOL priority (p90 0.009 SOL). Many use no relay tip at all, which suggests direct leader connections. Only 13.6% of launches have such slot-0 buys, and those are the profitable ones.

**Other facts that changed since the videos:**
- Slots are **~212 ms**, not 400 ms (283 slots per 60 s).
- About 45% of pump.fun transactions fail; the bots pay for their losses.
- New instructions: `buy_v3`, `sell_v3`, `create_v2` / Token-2022.
- Coins can now be quoted in pump coins, and there is a new mayhem mode.

## Conclusion

Over 3 days of full data, the edge in rugger-sniping is **speed, winner-take-all**. The profit sits in the ~14% of launches that ~40 ms snipers contest. Anyone landing 1–2 slots later is exit liquidity.

Every rule that avoids the race and looked profitable in-sample **failed out-of-sample**. That reproduces your Oct 21–23 finding with an independent method. **No capital should go into the rule-based strategy.**

The one hypothesis still worth testing is whether your Frankfurt box can win the slot-0 race on contested launches. It costs ~0.01 SOL to answer with probes (see RUNBOOK). If the race is winnable, the contested-launch segment was worth +13.8% per trade to the first buyer, which is large enough to pay for aggressive fees.

## Race probe from the Frankfurt box (2026-10-24, 15:00 UTC)

200 no-trade probes (compute budget plus a 10k-lamport Helius SWQoS tip, also sent via Triton Cascade). Each was sent 1.1 ms (p50) after the create showed up in the deshred stream.

**Probe bug (fixed):** 29 probes were byte-identical to an earlier probe built on the same blockhash. The network kept only the first copy, so the first evaluation counted those 29 against the wrong create. They produced the 17 "landed before the create" rows. Each probe is now unique (`cu_price + n`), and the evaluator drops duplicates.

Journal-based re-score on the box (`probe_eval.py`, duplicates dropped). 171 distinct probes, all landed. For 25, the create never executed (a real buy there just fails), which leaves 146:

| 146 probes next to an executed create | same slot as create | ahead of all slot-0 rivals (contested launches) |
|---|---|---|
| all | 64% | 36% (14/39) |
| leader < 100 km (Frankfurt) | 90% | 19% (3/16) |
| leader 100–1200 km (AMS, LON, …) | 71% | 53% (10/19) |
| leader ≥ 1200 km (US, Asia) | 21% | 25% (1/4) |
| create in first third of its block | 89% | 54% (7/13) |
| create in last third | 36% | 0% (0/4) |
| **leader < 1200 km and create in first half** | **96%** | **52% (11/21)** |

- **Ungated:** 36% contested wins is below the bar (≥ 60% same slot **and** ≥ 40% contested wins). No-go, as decided in advance.
- **Gated:** the gated row was found by slicing the same 146 probes, so it is a **hypothesis**. It needs a fresh gated run (`./scripts/probe.sh gated`), judged against a bar fixed now: **≥ 85% same slot and ≥ 40% ahead of all rivals on ≥ 30 contested launches.**
- **Likely cause:** the rivals sit in Frankfurt. When the leader is also in Frankfurt they beat us (19%). When the leader is in Amsterdam or London everyone is ~7–10 ms away, and we win more often than not. Under the gate, this is about 74% of stake (35% Frankfurt metro, 39% rest of Europe).

### What the measured odds are worth (simulator, fingerprint rule, out-of-sample days d-3 / d-2)

Costs are the measured path's: 0.00074 SOL per round trip. Contested launches use the measured win rate (won → `b0start`, lost in the same slot → `b0end`, later slot → `b1`). Uncontested launches use the measured same-slot rate.

| | trades/day | 0.10 SOL/trade | 0.25 SOL/trade |
|---|---|---|---|
| ungated (measured: 38% wins, 55% same slot on uncontested) | ~3,600 | −2.1 / −0.3 SOL/day | −1.4 / +3.4 SOL/day |
| gated (if 55% wins, 94% same slot hold up) | ~1,350 | +1.4 / +2.5 SOL/day | +5.0 / +7.8 SOL/day |
| gated, at 40% wins | ~1,350 | +0.6 / +1.5 SOL/day | +2.8 / +5.4 SOL/day |
| gated break-even win rate | | 31% / 17% | 22% / 8% |

**By trade size (frozen fingerprint list, as the bot uses it).** Each day is simulated with a list frozen *before* it (d-2 from d-3; d-1 from d-3+d-2). Gate pass 36%, 95% same slot on uncontested launches, and 3% of creates invisible. Each trade costs 0.00082 SOL: fees and tips, failed transactions, and buys on creates that fail. Our buy's own price impact is carried through the replay. About 1,200–1,350 trades/day at any size.

| per trade | SOL/day at 52% wins | at 40% wins | per trade (52%) | day p5 | intraday drawdown p95 |
|---|---|---|---|---|---|
| 0.05 | +0.5 / +0.7 | +0.2 / +0.4 | +0.7 / +1.1% | −0.2 / −0.3 | 0.8–0.9 |
| 0.10 | +2.0 / +2.2 | +1.4 / +1.6 | +1.5 / +1.9% | +0.5 / +0.7 | 1.3 |
| 0.25 | +6.1 / +6.6 | +4.5 / +5.1 | +1.8 / +2.2% | +1.8 / +2.5 | 2.7–3.2 |
| 0.50 | +11.3 / +12.6 | +8.1 / +9.6 | +1.7 / +2.1% | +3.8 / +4.1 | 5.8–6.0 |
| 1.00 | +15.5 / +19.8 | +9.3 / +14.0 | +1.2 / +1.7% | +1.5 / +2.1 | 13 |

- **Size:** the edge per trade peaks around 0.25 SOL. Below that, fixed costs dominate; above it, our own price impact does.
- **Untested above 0.5 SOL:** the replay assumes other traders act the same whatever our size. That assumption is weakest above 0.5 SOL, so don't size up past that without live data.

**Real-time gate calibration (124 creates, watch mode):** "ms since the slot's first pump tx reached us" tracks the create's final position in its block (corr 0.89; block midpoint ≈ 60–75 ms). Of creates seen ≤ 60 ms into their slot, 93% are in the first half of the block, and this captures 87% of first-half creates. The gated config therefore uses `gate_max_into_slot_ms = 60` with `gate_max_leader_km = 1200`.

**Intraday drawdown (gated, 200 resampled days):** at 0.10 SOL/trade, median 0.7–0.85 SOL and p95 1.2–1.6 SOL. At 0.25 SOL/trade, median 1.5–1.9 SOL and p95 2.7–3.0 SOL. A 1 SOL bankroll therefore covers the live verification test (0.02–0.05 SOL/trade), not production.

## Live trigger (bot), validated before any real trade

- **Fingerprint before execution:** the bot computes the deployer's fingerprint from the create transaction in the deshred stream. It covers the dev-buy SOL for six buy variants, a router (`rapidV5D…`), curve completion, v1 inline priority fees and default CU limits.
- **Match rate:** on 137 fresh creates it matched the simulator's post-execution fingerprint (research/features.py) **136 times (99.3%)**. The one miss, an 85 SOL curve-completing dev buy, is fixed. The rules were learned on 520 other creates (99.8% in a Python port).
- **Blind spots:**
  - About 3% of SOL-paired launches are created inside another program (`6Vo3245e…`). The bot never sees those creates and skips them.
  - 12–19% of creates seen before execution fail on chain. A buy triggered by one fails too, at ~0.0005 SOL.
- **Rule:** `bot/configs/fp_rules.json` uses the walk-forward rule frozen at the end of the data (≤ 2 h before the test). That is 601 armed fingerprints covering 39% of launches, minus 102 heavy deployers. In the persistence test, a list frozen on day −3 kept 95% of the walk-forward value on day −2 and 55% on day −1. Rebuild it daily from fresh data (`research/fp_rules.py`).
- **Dry run of `live_gated.json` (7 min, no sends):** 28 fingerprint triggers, 10 passed the race gate (~1.4 buys/min). The SIGTERM drain stopped cleanly.

## Gated probe (fresh data, 16:20 UTC): gate confirmed

200 probes: 97% same slot, ahead of all rivals on 42% of 77 contested launches. The bar was ≥ 85% and ≥ 40% of ≥ 30. The 52% seen when slicing the first run regressed to 42%. The Frankfurt-vs-rest-of-Europe split did not replicate (49% vs 36%).

**Evaluation bug (fixed 16:40 UTC):** the per-mint transaction fetch in `probe_eval` and `live_eval` included failed transactions. On hot launches, failed attempts outnumber trades up to 1000:1, so the page cap cut off the real trades. The backfill was not affected: it fetched succeeded transactions only. With complete data, the simulator reproduces all 10 live trades exactly (one paid a one-time ~0.0013 SOL account rent). The 42% above came from the buggy fetch. Re-score it with `./scripts/eval_probe.sh data/journal_probe_gated.jsonl`; missed rivals can only lower it.

## First live trades (2026-10-24, ~15:48 UTC, 0.03 SOL each): 10 of 10 lost

10 round trips in 5.7 minutes, all gated and all on armed fingerprints. **All 10 lost: −0.065 SOL in total, −21.7% per trade.** Every fill landed in the create slot, first after the dev bundle, and all 4 contested launches were won.

| same armed rule, chain data, complete trades | n | sim as first, per trade | winners | outside buys in first 30 s (p50) |
|---|---|---|---|---|
| last 75 min, not traded, all | 261 | −0.0% | 28% | 1.12 SOL |
| last 75 min, not traded, gate pass | 97 | +1.3% | 27% | 0.75 SOL |
| same 5.7 min, not traded, all | 17 | +8.0% | 53% | 2.71 SOL |
| same 5.7 min, not traded, gate pass | 6 | +5.1% | 50% | 1.79 SOL |
| **traded by us** (= live, exactly) | **10** | **−21.7%** | **0%** | **0.22 SOL** |

- **The pattern:** 9 of our 10 trades pass the same gate as the untraded group, and they did far worse. On the launches we bought, insiders sold sooner (median 8 vs 17 slots after the create), and outside buyers mostly stayed away.
- **Most likely explanation:** taking the first-buyer spot changes what follows. A replay of other traders' recorded behaviour cannot show that, which is the Oct 21–23 failure again.
- **Not proven:** n=10, and the bot's selection is not random relative to the untraded group.
- **Decisive test:** `live_gated.json` now skips **50%** of triggers at random. Skipped triggers are simulated; traded ones are measured on chain. `live_eval` prints a PRESENCE TEST and a verdict at ≥ 30 traded and ≥ 20 skipped. **Stop for good if traded < skipped by more than 2 standard errors.** Expected cost if the effect is real: ~0.2 SOL.
