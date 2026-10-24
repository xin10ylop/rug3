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
