//! Leader schedule (slot -> leader identity, polled from RPC) and leader locations (research/leaders.py ->
//! leaders.json). Used to gate races to slots whose leader is close to this box.

use std::collections::HashMap;
use std::str::FromStr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::RwLock;
use serde_json::json;
use solana_pubkey::Pubkey;

#[derive(Clone, Debug, Default, serde::Deserialize)]
pub struct LeaderGeo {
    pub km: Option<f64>,
    #[serde(default)]
    pub city: Option<String>,
}

#[derive(serde::Deserialize)]
struct GeoFile {
    leaders: HashMap<String, LeaderGeo>,
}

pub struct Leaders {
    sched: RwLock<(u64, Vec<Pubkey>)>, // (first slot, leader per slot)
    geo: RwLock<HashMap<Pubkey, LeaderGeo>>,
}

impl Leaders {
    pub fn leader(&self, slot: u64) -> Option<Pubkey> {
        let g = self.sched.read();
        slot.checked_sub(g.0).and_then(|i| g.1.get(i as usize)).copied()
    }
    pub fn geo(&self, who: &Pubkey) -> LeaderGeo { self.geo.read().get(who).cloned().unwrap_or_default() }
    pub fn n_geo(&self) -> usize { self.geo.read().len() }
    pub fn has_schedule(&self) -> bool { !self.sched.read().1.is_empty() }
}

pub fn load_geo(path: &str) -> anyhow::Result<HashMap<Pubkey, LeaderGeo>> {
    let f: GeoFile = serde_json::from_str(&std::fs::read_to_string(path)?)?;
    Ok(f.leaders.into_iter().filter_map(|(k, v)| Pubkey::from_str(&k).ok().map(|k| (k, v))).collect())
}

async fn rpc(http: &reqwest::Client, url: &str, method: &str, params: serde_json::Value) -> Option<serde_json::Value> {
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
    let v: serde_json::Value = http.post(url).json(&body).send().await.ok()?.json().await.ok()?;
    v.get("result").cloned()
}

/// Polls the schedule every 30 s (1500 slots ahead, ~5 min) and reloads leader locations every 10 min.
pub fn spawn(rpc_url: String, geo_path: Option<String>) -> Arc<Leaders> {
    let l = Arc::new(Leaders { sched: RwLock::new((0, vec![])), geo: RwLock::new(HashMap::new()) });
    if let Some(p) = &geo_path {
        if let Ok(g) = load_geo(p) { *l.geo.write() = g; }
    }
    let l2 = l.clone();
    tokio::spawn(async move {
        let http = reqwest::Client::builder().timeout(Duration::from_secs(5)).build().unwrap();
        let mut geo_at = Instant::now();
        loop {
            if let Some(s) = rpc(&http, &rpc_url, "getSlot", json!([{"commitment": "processed"}])).await.and_then(|v| v.as_u64()) {
                let start = s.saturating_sub(50);
                if let Some(v) = rpc(&http, &rpc_url, "getSlotLeaders", json!([start, 1500])).await {
                    let ls: Vec<Pubkey> = v.as_array().map(|a| a.iter()
                        .filter_map(|x| x.as_str().and_then(|s| Pubkey::from_str(s).ok())).collect()).unwrap_or_default();
                    if !ls.is_empty() { *l2.sched.write() = (start, ls); }
                }
            }
            if let Some(p) = &geo_path {
                if geo_at.elapsed() > Duration::from_secs(600) {
                    match load_geo(p) {
                        Ok(g) => *l2.geo.write() = g,
                        Err(e) => eprintln!("leaders: cannot reload {p}: {e}"),
                    }
                    geo_at = Instant::now();
                }
            }
            tokio::time::sleep(Duration::from_secs(30)).await;
        }
    });
    l
}
