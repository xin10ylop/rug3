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
fn ttl() -> u64 { 6 * 3600 }

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub mode: String, // "live" | "dry"
    pub rpc_url: String,
    pub grpc_url: String,
    pub grpc_token: String,
    pub keypair_path: String,
    pub senders: Vec<SenderCfg>,
    pub journal_path: String,

    pub rules: Vec<FunderRule>,
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
