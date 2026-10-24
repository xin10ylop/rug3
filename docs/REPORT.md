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
