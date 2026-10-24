use serde::Deserialize;

use crate::send::SenderCfg;

#[derive(Debug, Clone, Deserialize)]
pub struct FunderRule {
    pub name: String,
    pub funder: String,
    pub min_lamports: u64,
    pub max_lamports: u64,
    #[serde(default = "yes")]
    pub fresh_only: bool,
    /// how long an armed deployer stays armed (seconds)
    #[serde(default = "ttl")]
    pub ttl_s: u64,
    /// per-rule size override (SOL)
    pub buy_sol: Option<f64>,
}

fn yes() -> bool { true }
fn reload_s() -> u64 { 60 }
fn probe_every() -> u64 { 5 }
fn probe_max() -> u64 { 200 }
fn ttl() -> u64 { 6 * 3600 }

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub mode: String, // "live" | "dry" | "probe" | "watch" (log every create with its gate fields; sends nothing)
    pub rpc_url: String,
    pub grpc_url: String,
    pub grpc_token: String,
    pub keypair_path: String,
    pub senders: Vec<SenderCfg>,
    pub journal_path: String,

    #[serde(default)]
    pub rules: Vec<FunderRule>,
    /// rules.json written by research/rulegen.py (hot-reloaded)
    #[serde(default)]
    pub rules_path: Option<String>,
    #[serde(default = "reload_s")]
    pub rules_reload_s: u64,
    #[serde(default)]
    pub deployers: Vec<String>,
    #[serde(default)]
    pub chain_follow: bool,

    // buy
    pub buy_sol: f64,
    pub buy_slippage: f64, // e.g. 0.25 => accept up to 25% fewer tokens than quoted at post-bundle state
    pub buy_cu_limit: u32,
    pub buy_cu_price: u64, // micro-lamports/CU
    pub tips: Vec<(String, u64)>, // (tip account, lamports) included in buy tx
    pub sell_tips: Vec<(String, u64)>,
    pub sell_cu_limit: u32,
    pub sell_cu_price: u64,
    /// skip creates whose pre-existing same-slot bundle we cannot see; max mcap (SOL) we accept at entry
    pub max_entry_mcap_sol: f64,

    // exits
    pub tp: f64,           // net return, 1.0 = +100%
    pub sl: Option<f64>,   // net loss, 0.5 = -50%
    pub max_hold_s: f64,
    pub trailing: Option<f64>,
    pub exit_on_deployer_sell: bool,

    // risk
    pub max_open: usize,
    pub daily_loss_limit_sol: f64,
    pub max_buys_per_hour: u32,
    pub consecutive_loss_disable: u32,
    pub control_skip_prob: f64, // A/B: fraction of triggers deliberately skipped (logged as control)
    /// probe mode: on 1 of every `probe_every` SOL-paired creates, send a no-trade tx with the buy's fee rank
    #[serde(default = "probe_every")]
    pub probe_every: u64,
    #[serde(default = "probe_max")]
    pub probe_max: u64,

    /// race gate (probe + live): act only if the create's slot leader is within `gate_max_leader_km` of this box
    /// (locations from research/leaders.py) and the create reached us within `gate_max_into_slot_ms` of its slot's start
    #[serde(default)]
    pub leaders_path: Option<String>,
    #[serde(default)]
    pub gate_max_leader_km: Option<f64>,
    #[serde(default)]
    pub gate_max_into_slot_ms: Option<f64>,
}

impl Config {
    pub fn load(path: &str) -> anyhow::Result<Self> {
        let s = std::fs::read_to_string(path)?;
        let mut c: Config = serde_json::from_str(&s)?;
        // allow ${ENV} substitution for secrets
        for f in [&mut c.rpc_url, &mut c.grpc_url, &mut c.grpc_token, &mut c.keypair_path] {
            if let Some(var) = f.strip_prefix("${").and_then(|v| v.strip_suffix('}')) {
                *f = std::env::var(var).unwrap_or_default();
            }
        }
        for s in c.senders.iter_mut() {
            if s.url.contains("${TRITON_RPC_URL}") {
                s.url = s.url.replace("${TRITON_RPC_URL}", &std::env::var("TRITON_RPC_URL").unwrap_or_default());
            }
        }
        Ok(c)
    }
}
