# Runbook: Frankfurt box (isolated from your other bots)

Everything lives in **one directory**: its own build, Python venv, wallet, data and logs. Nothing is installed system-wide, except per-user rustup if `cargo` is missing. No ports are opened.

## Install (once, ~5 min)

```bash
git clone -b claude/eloquent-heisenberg-tiqzf0 https://github.com/xin10ylop/rug3 ~/rugsnipe-repo
cd ~/rugsnipe-repo/rugsnipe
./scripts/install.sh            # builds bot/, creates .venv/, data/, .env (template) and a NEW wallet.json
nano .env                       # TRITON_RPC_URL=...  TRITON_X_TOKEN=...
```

The installer prints `rugsnipe wallet address: <ADDRESS>`. Send **0.1 SOL** to that address.

The probe needs ≥ 0.03 SOL. A full SWQoS run costs ~0.006 SOL; the optional `max` run costs ~0.06 SOL.

`wallet.json` is created on this box only (0600 permissions, git-ignored). Back it up. It's a dedicated hot wallet: don't reuse your other bots' wallets.

## Step 1: race-capability probe (~20 min)

```bash
./scripts/probe.sh              # 200 no-trade probes via Helius SWQoS + Triton Cascade; exits by itself and prints the VERDICT
./scripts/probe.sh max          # optional: 60 probes via Helius "Max" routing (0.001 SOL tip each)
```

A probe is compute budget plus a tip, sent the instant a create is seen pre-execution. It uses the same fee rank and paths as a real buy and doesn't touch any curve.

The verdict compares where it lands against the create and the rival snipers.

**Continue** only if we land in the create slot in ≥ 60% of probes **and** are ahead of all slot-0 rivals in ≥ 40% of contested launches. Otherwise, don't trade rugger-sniping.

**Result on 2026-10-24: ungated no-go** (56–65% same slot, 33–38% contested wins). There is a promising sub-case: the slot leader is in Europe and the create comes early in its slot. See docs/REPORT.md.

## Step 1b: gated probe (~40 min, ~0.006 SOL)

```bash
cd ~/rugsnipe-repo && git pull && cd rugsnipe && (cd bot && cargo build --release)
./scripts/eval_probe.sh          # re-evaluate the first run without the duplicate probes (no SOL)
./scripts/probe.sh gated         # locates leaders, then probes only gated creates
```

The gate checks two things:

- the create's slot leader is < 1200 km from the box (`data/live/leaders.json`, rebuilt by `research/leaders.py` every 6 h)
- the create reached us ≤ 60 ms after its slot's first pump transaction (`gate_max_into_slot_ms`, about the first half of the block)

**Continue to a live micro test** only if the gated run shows ≥ 85% same slot **and** ahead of all rivals on ≥ 40% of ≥ 30 contested launches. The evaluator prints the verdict.

## Step 2: gated live micro test (only after Step 1b passes; ~2 h)

Fund the wallet to **≥ 0.4 SOL**. Positions are 0.03 SOL, at most 3 open, and the bot stops buying for the day at −0.25 SOL.

```bash
./scripts/live.sh                # starts in the background, PID in data/live.pid
tail -f data/logs/live_stdout.log | grep -E '"ev":"(buy_sent|buy_fill|sell_fill|skip)"'
./scripts/eval_live.sh           # any time: on-chain PnL, race outcome per fill, sim vs live
./scripts/stop_live.sh           # stops buying, lets open positions exit (<= ~60 s), then quits
```

The trigger is the validated walk-forward rule:

- **Armed fingerprint:** the deployer's create-tx fingerprint is in `bot/configs/fp_rules.json` (601 fingerprints).
- **Not a heavy deployer:** the deployer launched < 20 SOL-paired coins in the last 24 h, and isn't on yesterday's heavy list.
- **Race gate:** as in Step 1b.
- **Exit:** +30% net, or after 30 s.
- **Control group:** 20% of triggers are skipped on purpose and simulated.

**Pass criteria, fixed before the test (judged on ≥ 60 fills):**

1. ≥ 80% of fills land in the create slot.
2. Ahead of all rivals on ≥ 40% of ≥ 25 contested fills.
3. Live minus "sim at our exact position" averages within ±0.3% of position size per trade.

At 0.03 SOL the fixed costs (~0.0007 SOL per round trip, ~2.3%) are expected to make PnL slightly negative, so PnL is reported but not used as a pass or fail at this size. **If the test passes:** set `buy_sol` to 0.1, fund about 3 SOL, and set `daily_loss_limit_sol` to 0.6. Judge on 1–2 days of on-chain PnL.

## Shared-box notes

- The probe opens one gRPC stream (pre-execution creates only) and polls one blockhash per second.
- Helius Sender keyless is limited to 1 req/s per IP. If your other bots use it keyless from the same IP, keep `probe_every` ≥ 3, or add an API key.
- Triton rate limits are per IP (1,200 req/10 s), and bandwidth is billed per token, both shared with your other bots.
- Logs: `data/logs/`. Journals: `data/journal_*.jsonl`. Remove everything with `rm -rf ~/rugsnipe-repo`.

## Other components (research; not needed for the probe)

| component | command |
|---|---|
| recorder | `cd recorder && npm install && node record.js` (every pump.fun tx → `data/rec/`, ~3 GB/day) |
| backfill + sim | `./scripts/install.sh --research`, then `research/backfill_*.py`, `research/analyze.py <tag>` |
| rule generator | `.venv/bin/python research/rulegen.py --seed …` (the out-of-sample test rejected these rules) |
| live evaluator | `.venv/bin/python research/live_eval.py <journal> <wallet>` (on-chain ground truth only) |
