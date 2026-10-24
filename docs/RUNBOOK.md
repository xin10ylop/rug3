# Runbook: Frankfurt box

All components read secrets from the environment. Never commit them.

```bash
# 0. one-time setup (Ubuntu 22.04+)
sudo apt-get install -y build-essential pkg-config libssl-dev python3-pip nodejs npm
curl https://sh.rustup.rs -sSf | sh -s -- -y && . ~/.cargo/env
git clone -b claude/eloquent-heisenberg-tiqzf0 https://github.com/xin10ylop/rug3 ~/rug3 && cd ~/rug3
pip install aiohttp orjson base58 numpy pandas pyarrow lightgbm scikit-learn
(cd bot && cargo build --release)
(cd recorder && npm install)

cat > ~/rug3/.env <<'EOF'
TRITON_RPC_URL=https://<your-endpoint>.mainnet.rpcpool.com/<token>
TRITON_X_TOKEN=<token>
RUGBOT_KEYPAIR=/root/rugbot-wallet.json
EOF
chmod 600 ~/rug3/.env
```

**Wallet:** use a fresh, dedicated hot wallet (`solana-keygen new -o /root/rugbot-wallet.json`) holding only what the step needs.

## Step 1: race-capability probe (~20 min, ~0.01 SOL)

This answers the only open question: can this box land in the create slot ahead of the 40 ms snipers?

```bash
cd ~/rug3 && set -a && . ./.env && set +a
./bot/target/release/rugbot bot/configs/probe.json          # 200 probes, SWQoS + Triton Cascade
./bot/target/release/rugbot bot/configs/probe_max.json      # optional: 60 probes via Helius "Max" (0.001 SOL tip each)
cd research && python3 probe_eval.py /root/rug3/data/journal_probe.jsonl <WALLET_PUBKEY>
```

A probe is a no-trade transaction: compute budget plus tip. It carries the **same fee rank and paths as a real buy**, is sent the instant a create appears pre-execution, and doesn't touch any curve.

**Decision rule** (contested = launches where rivals bought in slot 0):
- We beat **all** slot-0 rivals in ≥ 40% of contested launches, **and** land in the create slot ≥ 60% overall → go to Step 2 (race mode needs wiring; send me the probe journal).
- Otherwise → **stop rugger-sniping.** The data shows no edge for a non-winner.

## Step 2 (only if the probe says yes): micro-size race test

Build the race mode: fingerprint-keyed triggers at maximum speed and an aggressive fee cap. Then run 0.02 SOL buys with `control_skip_prob` 0.25 and evaluate with
`python3 research/live_eval.py <journal> <wallet>`. It scores **only executed trades**, from on-chain balance deltas, and splits results by rival presence. Scale only if live PnL on executed trades is positive after ≥ 40 fills.

## Components

| component | command | notes |
|---|---|---|
| recorder | `node recorder/record.js` | every pump.fun tx, processed + deshred + slots → `data/rec/` (~3 GB/day) |
| backfill + sim | `research/backfill_*.py`, `research/analyze.py <tag>` | ~20 min/day of history, ~$2 of Triton credit |
| rule generator | `python3 research/rulegen.py --seed …` | rolling walk-forward rules → `data/live/rules.json` (the out-of-sample test rejected these rules; kept for research) |
| bot | `rugbot <config.json>` | modes: `dry` (no sends), `probe`, `live`; kill-switches: daily loss limit, consecutive losses, max open, buys/hour |
| evaluators | `research/probe_eval.py`, `research/live_eval.py` | ground truth from chain, never from paper |
