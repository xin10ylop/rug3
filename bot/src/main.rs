//! rugbot: live dev-sniper for pump.fun "rugger" launches.
//!
//! Flow: watched funder sends a matching amount to a fresh wallet  -> wallet is ARMED
//!       armed wallet's create tx seen pre-execution (deshred)       -> BUY sent on all paths
//!       our TradeEvent seen (processed)                             -> POSITION opened with real fill
//!       curve updates / deployer sells / timers                     -> SELL sent; real proceeds journaled
//! Every decision is journaled (JSONL) so live P&L is computed only from trades that actually executed.

mod config;
mod pump;
mod send;
mod stream;
mod tx;

use std::collections::HashMap;
use std::str::FromStr;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use parking_lot::Mutex;
use rand::Rng;
use serde_json::json;
use solana_keypair::Keypair;
use solana_pubkey::Pubkey;
use solana_signer::Signer;
use tokio::sync::mpsc;

use config::Config;
use pump::{Curve, Statics, TradeAccounts, PUMP};
use stream::Ev;

const FEE_RATE: f64 = 0.0125; // 0.95% protocol + 0.30% creator (verified on-chain Oct 2026)

struct Journal {
    f: Mutex<std::io::BufWriter<std::fs::File>>,
}

impl Journal {
    fn new(path: &str) -> Self {
        let f = std::fs::OpenOptions::new().create(true).append(true).open(path).expect("journal");
        Self { f: Mutex::new(std::io::BufWriter::new(f)) }
    }
    fn log(&self, mut v: serde_json::Value) {
        use std::io::Write;
        let ts = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64();
        v["ts"] = json!(ts);
        let mut g = self.f.lock();
        let _ = writeln!(g, "{}", v);
        let _ = g.flush();
        println!("{}", v);
    }
}

#[derive(Clone, Debug)]
struct Armed {
    rule: String,
    until: Instant,
    buy_sol: f64,
}

#[derive(Debug)]
struct PendingBuy {
    mint: Pubkey,
    deployer: Pubkey,
    acc: TradeAccounts,
    sent: Instant,
    create_slot: u64,
    spend: u64,
    sig: [u8; 64],
    rule: String,
}

#[derive(Debug)]
struct Position {
    mint: Pubkey,
    deployer: Pubkey,
    acc: TradeAccounts,
    tokens: u64,
    cost: u64, // lamports incl. pump fees
    opened: Instant,
    create_slot: u64,
    fill_slot: u64,
    fill_idx: u64,
    peak_mult: f64,
    selling: Option<(Instant, u32)>, // (last sell sent, attempts)
    exit_reason: String,
    rule: String,
}

struct State {
    armed: HashMap<Pubkey, Armed>,
    pending: HashMap<Pubkey, PendingBuy>, // by mint
    positions: HashMap<Pubkey, Position>,
    curves: HashMap<Pubkey, (Curve, u64)>,
    slot: u64,
    day_pnl: i64,
    consec_losses: u32,
    buys_hour: Vec<Instant>,
    disabled: bool,
}

struct Bot {
    cfg: Config,
    kp: Keypair,
    me: Pubkey,
    uva: Pubkey,
    st: Statics,
    senders: send::Senders,
    bh: Arc<send::Blockhash>,
    j: Journal,
    s: Mutex<State>,
    tips: Vec<(Pubkey, u64)>,
    sell_tips: Vec<(Pubkey, u64)>,
    rules: Vec<(Pubkey, config::FunderRule)>,
}

fn now_ms() -> f64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64() * 1e3 }

impl Bot {
    fn live(&self) -> bool { self.cfg.mode == "live" }

    /// min tokens out such that our average entry is no worse than entering at `max_mcap` (SOL)
    fn min_tokens_for(&self, spend: u64, max_mcap: f64) -> u64 {
        let k = (pump::VSOL0 * pump::VTOK0) as f64;
        // at mcap M: vsol/vtok = M*1e9/1e15 and vsol*vtok = k
        let ratio = max_mcap * 1e9 / pump::SUPPLY_RAW;
        let vsol = (k * ratio).sqrt();
        let vtok = k / vsol;
        let c = Curve { vsol: vsol as u128, vtok: vtok as u128 };
        let net = (spend as f64 / (1.0 + FEE_RATE)) as u128;
        (c.buy_tokens(net) as f64 * 0.995) as u64
    }

    async fn on_create(self: &Arc<Self>, slot: u64, sig: [u8; 64], info: pump::CreateInfo, recv: Instant) {
        let (rule, buy_sol) = {
            let mut s = self.s.lock();
            let a = s.armed.get(&info.user).cloned();
            let explicit = self.cfg.deployers.iter().any(|d| d == &info.user.to_string());
            match (a, explicit) {
                (Some(a), _) if a.until > Instant::now() => (a.rule, a.buy_sol),
                (_, true) => ("deployer".to_string(), self.cfg.buy_sol),
                _ => return,
            }
            .clone_with(&mut s)
        };
        let base = json!({"mint": info.mint.to_string(), "deployer": info.user.to_string(), "rule": rule,
            "create_slot": slot, "create_sig": bs58::encode(sig).into_string(), "v2": info.v2,
            "sol_paired": info.sol_paired, "mayhem": info.mayhem});
        if !info.sol_paired || info.mayhem {
            self.j.log(json!({"ev": "skip", "why": "not_sol_or_mayhem", "d": base}));
            return;
        }
        // risk gates
        {
            let mut s = self.s.lock();
            s.buys_hour.retain(|t| t.elapsed() < Duration::from_secs(3600));
            let why = if s.disabled { Some("disabled") }
                else if s.positions.len() + s.pending.len() >= self.cfg.max_open { Some("max_open") }
                else if s.buys_hour.len() as u32 >= self.cfg.max_buys_per_hour { Some("rate") }
                else if (s.day_pnl as f64) / 1e9 <= -self.cfg.daily_loss_limit_sol { Some("daily_loss") }
                else if s.pending.contains_key(&info.mint) || s.positions.contains_key(&info.mint) { Some("dup") }
                else { None };
            if let Some(w) = why {
                drop(s);
                self.j.log(json!({"ev": "skip", "why": w, "d": base}));
                return;
            }
            if rand::thread_rng().gen::<f64>() < self.cfg.control_skip_prob {
                drop(s);
                self.j.log(json!({"ev": "control_skip", "d": base}));
                return;
            }
            s.buys_hour.push(Instant::now());
        }
        let Some(bh) = self.bh.get() else {
            self.j.log(json!({"ev": "skip", "why": "no_blockhash", "d": base}));
            return;
        };
        let spend = (buy_sol * 1e9) as u64;
        let bi = rand::thread_rng().gen_range(0..8);
        let acc = TradeAccounts::new(&self.st, &self.me, &self.uva, &info.mint, &info.token_program, bi);
        let min_out = self.min_tokens_for(spend, self.cfg.max_entry_mcap_sol);
        let mut ixs = vec![pump::cu_limit(self.cfg.buy_cu_limit), pump::cu_price(self.cfg.buy_cu_price),
                           acc.create_user_ata_idempotent(), acc.buy_exact_sol_in_v3(spend, min_out)];
        for (t, l) in &self.tips { ixs.push(pump::transfer(&self.me, t, *l)); }
        let (wire, tsig) = tx::build_signed(&self.kp, &ixs, &bh);
        let build_us = recv.elapsed().as_micros();
        {
            let mut s = self.s.lock();
            s.pending.insert(info.mint, PendingBuy { mint: info.mint, deployer: info.user, acc, sent: Instant::now(),
                create_slot: slot, spend, sig: tsig, rule: rule.clone() });
        }
        if self.live() {
            let res = self.senders.send_all(&wire).await;
            self.j.log(json!({"ev": "buy_sent", "d": base, "sig": bs58::encode(tsig).into_string(), "spend": spend,
                "min_out": min_out, "build_us": build_us, "send": res, "since_recv_ms": recv.elapsed().as_secs_f64()*1e3,
                "wall_ms": now_ms()}));
        } else {
            self.j.log(json!({"ev": "dry_buy", "d": base, "spend": spend, "build_us": build_us}));
            self.s.lock().pending.remove(&info.mint);
        }
    }

    fn on_trade(self: &Arc<Self>, slot: u64, idx: u64, sig: [u8; 64], signer: Pubkey, mint: Pubkey, sol: u64, tok: u64,
                is_buy: bool, user: Pubkey, vsol: u64, vtok: u64) -> Option<Pubkey> {
        let mut s = self.s.lock();
        let tracked = s.pending.contains_key(&mint) || s.positions.contains_key(&mint);
        if !tracked { return None; }
        s.curves.insert(mint, (Curve { vsol: vsol as u128, vtok: vtok as u128 }, slot));
        // our own fill?
        if signer == self.me && is_buy {
            if let Some(p) = s.pending.remove(&mint) {
                let cost = (sol as f64 * (1.0 + FEE_RATE)) as u64;
                let n_before = 0; // computed offline from the recorder
                self.j.log(json!({"ev": "buy_fill", "mint": mint.to_string(), "sig": bs58::encode(sig).into_string(),
                    "slot": slot, "idx": idx, "slot_off": slot as i64 - p.create_slot as i64, "tokens": tok, "sol": sol,
                    "cost": cost, "entry_mcap": Curve { vsol: vsol as u128, vtok: vtok as u128 }.mcap_sol(),
                    "latency_ms": p.sent.elapsed().as_secs_f64()*1e3, "rule": p.rule, "n_before": n_before}));
                s.positions.insert(mint, Position { mint, deployer: p.deployer, acc: p.acc, tokens: tok, cost,
                    opened: Instant::now(), create_slot: p.create_slot, fill_slot: slot, fill_idx: idx, peak_mult: 0.0,
                    selling: None, exit_reason: String::new(), rule: p.rule });
            }
            return None;
        }
        if signer == self.me && !is_buy {
            if let Some(p) = s.positions.remove(&mint) {
                let proceeds = (sol as f64 * (1.0 - FEE_RATE)) as i64;
                let pnl = proceeds - p.cost as i64;
                s.day_pnl += pnl;
                if pnl < 0 { s.consec_losses += 1 } else { s.consec_losses = 0 }
                if s.consec_losses >= self.cfg.consecutive_loss_disable { s.disabled = true; }
                self.j.log(json!({"ev": "sell_fill", "mint": mint.to_string(), "sig": bs58::encode(sig).into_string(),
                    "slot": slot, "idx": idx, "proceeds": proceeds, "cost": p.cost, "pnl": pnl, "reason": p.exit_reason,
                    "hold_s": p.opened.elapsed().as_secs_f64(), "slot_off": slot as i64 - p.create_slot as i64,
                    "peak_mult": p.peak_mult, "day_pnl": s.day_pnl, "disabled": s.disabled, "rule": p.rule}));
            }
            return None;
        }
        // other trade on a held mint: evaluate exits
        let cfg = &self.cfg;
        let curve = Curve { vsol: vsol as u128, vtok: vtok as u128 };
        if let Some(p) = s.positions.get_mut(&mint) {
            if p.selling.is_some() { return None; }
            let value = curve.sell_sol(p.tokens as u128) as f64 * (1.0 - FEE_RATE);
            let mult = value / p.cost as f64;
            if mult > p.peak_mult { p.peak_mult = mult; }
            let reason = if mult - 1.0 >= cfg.tp { Some("tp") }
                else if cfg.sl.map(|sl| mult - 1.0 <= -sl).unwrap_or(false) { Some("sl") }
                else if cfg.trailing.map(|t| p.peak_mult - 1.0 >= t * 0.5 && mult <= p.peak_mult * (1.0 - t)).unwrap_or(false) { Some("trail") }
                else if cfg.exit_on_deployer_sell && !is_buy && user == p.deployer { Some("deployer_sell") }
                else { None };
            if let Some(r) = reason {
                p.exit_reason = r.to_string();
                return Some(mint);
            }
        }
        None
    }

    async fn sell(self: &Arc<Self>, mint: Pubkey, aggressive: bool) {
        let (acc, tokens, quote, attempts) = {
            let mut s = self.s.lock();
            let curve = s.curves.get(&mint).map(|c| c.0);
            let Some(p) = s.positions.get_mut(&mint) else { return };
            let att = p.selling.map(|x| x.1).unwrap_or(0) + 1;
            p.selling = Some((Instant::now(), att));
            let q = curve.map(|c| c.sell_sol(p.tokens as u128) as f64 * (1.0 - FEE_RATE)).unwrap_or(0.0);
            (p.acc.clone(), p.tokens, q, att)
        };
        let Some(bh) = self.bh.get() else { return };
        let min_out = if aggressive || attempts > 1 { 0 } else { (quote * 0.6) as u64 };
        let mut ixs = vec![pump::cu_limit(self.cfg.sell_cu_limit), pump::cu_price(self.cfg.sell_cu_price),
                           acc.sell_v3(tokens, min_out), acc.close_user_ata()];
        for (t, l) in &self.sell_tips { ixs.push(pump::transfer(&self.me, t, *l)); }
        let (wire, tsig) = tx::build_signed(&self.kp, &ixs, &bh);
        if self.live() {
            let res = self.senders.send_all(&wire).await;
            self.j.log(json!({"ev": "sell_sent", "mint": mint.to_string(), "sig": bs58::encode(tsig).into_string(),
                "tokens": tokens, "min_out": min_out, "quote": quote, "attempt": attempts, "send": res}));
        }
    }

    async fn on_timer(self: &Arc<Self>) {
        let mut to_sell = vec![];
        {
            let mut s = self.s.lock();
            let now = Instant::now();
            s.armed.retain(|_, a| a.until > now);
            // buys not seen within 10s: missed / dropped
            let missed: Vec<Pubkey> = s.pending.iter().filter(|(_, p)| p.sent.elapsed() > Duration::from_secs(10)).map(|(m, _)| *m).collect();
            for m in missed {
                if let Some(p) = s.pending.remove(&m) {
                    self.j.log(json!({"ev": "buy_miss", "mint": m.to_string(), "sig": bs58::encode(p.sig).into_string(), "rule": p.rule}));
                }
            }
            for p in s.positions.values_mut() {
                match p.selling {
                    None if p.opened.elapsed().as_secs_f64() >= self.cfg.max_hold_s => {
                        p.exit_reason = "time".into();
                        to_sell.push((p.mint, false));
                    }
                    Some((t, att)) if t.elapsed() > Duration::from_millis(2500) && att < 8 => to_sell.push((p.mint, true)),
                    _ => {}
                }
            }
        }
        for (m, aggr) in to_sell { self.sell(m, aggr).await; }
    }
}

trait CloneWith { fn clone_with(self, s: &mut State) -> (String, f64); }
impl CloneWith for (String, f64) { fn clone_with(self, _s: &mut State) -> (String, f64) { self } }

/// `rugbot simtx <payer> <mint> <token_program> <buy|sell> <amount> <blockhash_b58>`: print an unsigned
/// base64 tx for simulateTransaction(sigVerify=false) to validate accounts/CU against live curves.
fn simtx(args: &[String]) -> anyhow::Result<()> {
    use base64::Engine;
    let payer = Pubkey::from_str(&args[2])?;
    let mint = Pubkey::from_str(&args[3])?;
    let tp = Pubkey::from_str(&args[4])?;
    let amount: u64 = args[6].parse()?;
    let bh: [u8; 32] = bs58::decode(&args[7]).into_vec()?.try_into().map_err(|_| anyhow::anyhow!("bh"))?;
    let st = Statics::new();
    let uva = pump::pda(&[b"user_volume_accumulator", payer.as_ref()], &PUMP);
    let acc = TradeAccounts::new(&st, &payer, &uva, &mint, &tp, 0);
    let ixs = if args[5] == "buy" {
        vec![pump::cu_limit(200_000), pump::cu_price(1000), acc.create_user_ata_idempotent(), acc.buy_exact_sol_in_v3(amount, 1)]
    } else {
        vec![pump::cu_limit(200_000), pump::cu_price(1000), acc.sell_v3(amount, 0)]
    };
    let wire = tx::build_unsigned(&payer, &ixs, &bh);
    println!("{}", base64::engine::general_purpose::STANDARD.encode(&wire));
    eprintln!("bytes={} bonding_curve={} ata={} uva={}", wire.len(), acc.bonding_curve, acc.assoc_base_user, uva);
    Ok(())
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let argv: Vec<String> = std::env::args().collect();
    if argv.get(1).map(|s| s.as_str()) == Some("simtx") {
        return simtx(&argv);
    }
    let path = std::env::args().nth(1).unwrap_or_else(|| "config.json".into());
    let cfg = Config::load(&path)?;
    let kp_bytes: Vec<u8> = serde_json::from_str(&std::fs::read_to_string(&cfg.keypair_path)?)?;
    let kp = Keypair::try_from(kp_bytes.as_slice()).map_err(|e| anyhow::anyhow!("keypair: {e}"))?;
    let me = kp.pubkey();
    let st = Statics::new();
    let uva = pump::pda(&[b"user_volume_accumulator", me.as_ref()], &PUMP);
    let tips = cfg.tips.iter().map(|(a, l)| (Pubkey::from_str(a).unwrap(), *l)).collect();
    let sell_tips = cfg.sell_tips.iter().map(|(a, l)| (Pubkey::from_str(a).unwrap(), *l)).collect();
    let rules: Vec<(Pubkey, config::FunderRule)> = cfg.rules.iter().map(|r| (Pubkey::from_str(&r.funder).unwrap(), r.clone())).collect();
    let funders: Vec<Pubkey> = { let mut v: Vec<Pubkey> = rules.iter().map(|r| r.0).collect(); v.sort(); v.dedup(); v };
    let bh = send::spawn_blockhash_poller(cfg.rpc_url.clone());
    let senders = send::Senders::new(cfg.senders.clone());
    let j = Journal::new(&cfg.journal_path);
    j.log(json!({"ev": "start", "me": me.to_string(), "mode": cfg.mode, "rules": cfg.rules.len(), "funders": funders.len()}));
    let bot = Arc::new(Bot { cfg: cfg.clone(), kp, me, uva, st, senders, bh, j, tips, sell_tips, rules,
        s: Mutex::new(State { armed: HashMap::new(), pending: HashMap::new(), positions: HashMap::new(), curves: HashMap::new(),
            slot: 0, day_pnl: 0, consec_losses: 0, buys_hour: vec![], disabled: false }) });

    let (tx, mut rx) = mpsc::unbounded_channel::<Ev>();
    tokio::spawn(stream::run_deshred(cfg.grpc_url.clone(), cfg.grpc_token.clone(), tx.clone()));
    tokio::spawn(stream::run_processed(cfg.grpc_url.clone(), cfg.grpc_token.clone(), funders, me, tx.clone()));
    { let b = bot.clone(); tokio::spawn(async move { loop { b.senders.warm().await; tokio::time::sleep(Duration::from_secs(15)).await; } }); }
    { let b = bot.clone(); tokio::spawn(async move { loop { tokio::time::sleep(Duration::from_millis(200)).await; b.on_timer().await; } }); }

    while let Some(ev) = rx.recv().await {
        match ev {
            Ev::Create { slot, sig, info, recv } => {
                let b = bot.clone();
                tokio::spawn(async move { b.on_create(slot, sig, info, recv).await });
            }
            Ev::Trade { slot, idx, sig, signer, ok: _, mint, sol, tok, is_buy, user, vsol, vtok } => {
                if let Some(m) = bot.on_trade(slot, idx, sig, signer, mint, sol, tok, is_buy, user, vsol, vtok) {
                    let b = bot.clone();
                    tokio::spawn(async move { b.sell(m, false).await });
                }
            }
            Ev::Funding { slot, funder, to, lamports, to_pre_balance } => {
                for (f, r) in &bot.rules {
                    if *f == funder && lamports >= r.min_lamports && lamports <= r.max_lamports && (!r.fresh_only || to_pre_balance == 0) {
                        let mut s = bot.s.lock();
                        s.armed.insert(to, Armed { rule: r.name.clone(), until: Instant::now() + Duration::from_secs(r.ttl_s),
                            buy_sol: r.buy_sol.unwrap_or(bot.cfg.buy_sol) });
                        drop(s);
                        bot.j.log(json!({"ev": "arm", "rule": r.name, "wallet": to.to_string(), "lamports": lamports, "slot": slot}));
                    }
                }
            }
            Ev::Failed { slot, sig, signer: _ } => {
                bot.j.log(json!({"ev": "our_tx_failed", "slot": slot, "sig": bs58::encode(sig).into_string()}));
            }
            Ev::Slot { slot, .. } => { let mut s = bot.s.lock(); if slot > s.slot { s.slot = slot; } }
        }
    }
    Ok(())
}
