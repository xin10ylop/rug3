//! Transaction submission to several landing paths in parallel + blockhash cache.

use std::sync::Arc;
use std::time::{Duration, Instant};

use base64::Engine;
use parking_lot::RwLock;
use serde_json::json;

#[derive(Clone, Debug, serde::Deserialize)]
pub struct SenderCfg {
    pub name: String,
    /// "jsonrpc" (sendTransaction, base64, skipPreflight) or "raw" (POST bytes, Triton /sendtx)
    pub kind: String,
    pub url: String,
}

pub struct Senders {
    http: reqwest::Client,
    cfgs: Vec<SenderCfg>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct SendResult {
    pub name: String,
    pub ok: bool,
    pub ms: f64,
    pub detail: String,
}

impl Senders {
    pub fn new(cfgs: Vec<SenderCfg>) -> Self {
        let http = reqwest::Client::builder()
            .pool_idle_timeout(Duration::from_secs(90))
            .pool_max_idle_per_host(8)
            .tcp_nodelay(true)
            .timeout(Duration::from_secs(5))
            .build()
            .expect("http client");
        Self { http, cfgs }
    }

    /// Keep TLS connections hot (call every few seconds).
    pub async fn warm(&self) {
        for c in &self.cfgs {
            let _ = self.http.get(&c.url).send().await;
        }
    }

    pub async fn send_all(&self, wire: &[u8]) -> Vec<SendResult> {
        let b64 = base64::engine::general_purpose::STANDARD.encode(wire);
        let futs = self.cfgs.iter().map(|c| {
            let http = self.http.clone();
            let b64 = b64.clone();
            let raw = wire.to_vec();
            let c = c.clone();
            async move {
                let t0 = Instant::now();
                let res = if c.kind == "raw" {
                    http.post(&c.url).header("content-type", "application/octet-stream").body(raw).send().await
                } else {
                    let body = json!({"jsonrpc":"2.0","id":1,"method":"sendTransaction",
                        "params":[b64, {"encoding":"base64","skipPreflight":true,"maxRetries":0}]});
                    http.post(&c.url).json(&body).send().await
                };
                let ms = t0.elapsed().as_secs_f64() * 1e3;
                match res {
                    Ok(r) => {
                        let st = r.status();
                        let txt = r.text().await.unwrap_or_default();
                        let ok = st.is_success() && !txt.contains("\"error\"");
                        SendResult { name: c.name, ok, ms, detail: txt.chars().take(200).collect() }
                    }
                    Err(e) => SendResult { name: c.name, ok: false, ms, detail: e.to_string() },
                }
            }
        });
        futures::future::join_all(futs).await
    }
}

pub struct Blockhash {
    pub inner: RwLock<([u8; 32], Instant)>,
}

impl Blockhash {
    pub fn get(&self) -> Option<[u8; 32]> {
        let g = self.inner.read();
        if g.1.elapsed() < Duration::from_secs(20) { Some(g.0) } else { None }
    }
}

pub fn spawn_blockhash_poller(rpc_url: String) -> Arc<Blockhash> {
    let bh = Arc::new(Blockhash { inner: RwLock::new(([0u8; 32], Instant::now() - Duration::from_secs(3600))) });
    let bh2 = bh.clone();
    tokio::spawn(async move {
        let http = reqwest::Client::builder().timeout(Duration::from_secs(3)).build().unwrap();
        loop {
            let body = json!({"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash","params":[{"commitment":"confirmed"}]});
            if let Ok(r) = http.post(&rpc_url).json(&body).send().await {
                if let Ok(v) = r.json::<serde_json::Value>().await {
                    if let Some(s) = v["result"]["value"]["blockhash"].as_str() {
                        if let Ok(b) = bs58::decode(s).into_vec() {
                            if b.len() == 32 {
                                let mut a = [0u8; 32];
                                a.copy_from_slice(&b);
                                *bh2.inner.write() = (a, Instant::now());
                            }
                        }
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(1000)).await;
        }
    });
    bh
}
