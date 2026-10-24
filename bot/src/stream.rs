//! Yellowstone gRPC streams (Triton Riptide): deshred (pre-execution) + processed transactions + slots.
//! Emits compact events on a channel; reconnects forever with backoff.

use std::collections::HashMap;
use std::time::Duration;

use futures::{SinkExt, StreamExt};
use solana_pubkey::Pubkey;
use tokio::sync::mpsc;
use yellowstone_grpc_client::{ClientTlsConfig, GeyserGrpcClient};
use yellowstone_grpc_proto::geyser::{
    subscribe_update::UpdateOneof, subscribe_update_deshred, CommitmentLevel, SubscribeDeshredRequest,
    SubscribeRequest, SubscribeRequestFilterDeshredTransactions, SubscribeRequestFilterSlots,
    SubscribeRequestFilterTransactions, SubscribeRequestPing,
};

use crate::pump::{parse_create, parse_trade_event, CreateInfo, EVENT_IX_TAG, PUMP};

#[derive(Debug)]
pub enum Ev {
    /// create seen pre-execution (deshred)
    Create { slot: u64, sig: [u8; 64], info: CreateInfo, recv: std::time::Instant },
    /// executed pump trade (processed)
    Trade { slot: u64, idx: u64, sig: [u8; 64], signer: Pubkey, ok: bool, mint: Pubkey, sol: u64, tok: u64,
        is_buy: bool, user: Pubkey, vsol: u64, vtok: u64 },
    /// executed tx we care about that failed (our own, typically)
    Failed { slot: u64, sig: [u8; 64], signer: Pubkey },
    /// SOL transfer out of a watched funder (processed)
    Funding { slot: u64, funder: Pubkey, to: Pubkey, lamports: u64, to_pre_balance: u64 },
    Slot { slot: u64, status: i32 },
}

fn keys_of(msg: &yellowstone_grpc_proto::prelude::Message, lw: &[Vec<u8>], lr: &[Vec<u8>]) -> Vec<Pubkey> {
    msg.account_keys.iter().chain(lw.iter()).chain(lr.iter())
        .filter_map(|k| Pubkey::try_from(k.as_slice()).ok()).collect()
}

fn sig64(v: &[u8]) -> [u8; 64] {
    let mut s = [0u8; 64];
    if v.len() == 64 { s.copy_from_slice(v); }
    s
}

pub async fn run_deshred(endpoint: String, token: String, tx: mpsc::UnboundedSender<Ev>) {
    let mut backoff = 1u64;
    loop {
        let r: anyhow::Result<()> = async {
            let mut client = GeyserGrpcClient::build_from_shared(endpoint.clone())?
                .x_token(Some(token.clone()))?
                .tls_config(ClientTlsConfig::new().with_native_roots())?
                .tcp_nodelay(true)
                .http2_adaptive_window(true)
                .initial_connection_window_size(8 * 1024 * 1024)
                .initial_stream_window_size(4 * 1024 * 1024)
                .connect()
                .await?;
            let filt = HashMap::from([("pump".to_string(), SubscribeRequestFilterDeshredTransactions {
                vote: Some(false), account_include: vec![PUMP.to_string()], account_exclude: vec![], account_required: vec![],
                ..Default::default()
            })]);
            let req = SubscribeDeshredRequest { deshred_transactions: filt.clone(), ..Default::default() };
            let (mut sink, mut stream) = client.subscribe_deshred_with_request(Some(req)).await?;
            backoff = 1;
            while let Some(msg) = stream.next().await {
                let msg = msg?;
                match msg.update_oneof {
                    Some(subscribe_update_deshred::UpdateOneof::DeshredTransaction(u)) => {
                        let recv = std::time::Instant::now();
                        let Some(info) = u.transaction.as_ref() else { continue };
                        let Some(t) = info.transaction.as_ref() else { continue };
                        let Some(m) = t.message.as_ref() else { continue };
                        let keys = keys_of(m, &info.loaded_writable_addresses, &info.loaded_readonly_addresses);
                        for ix in &m.instructions {
                            if keys.get(ix.program_id_index as usize) != Some(&PUMP) || ix.data.len() < 8 { continue; }
                            let accs: Vec<Pubkey> = ix.accounts.iter().filter_map(|&i| keys.get(i as usize).copied()).collect();
                            if let Some(ci) = parse_create(&ix.data, &accs) {
                                let _ = tx.send(Ev::Create { slot: u.slot, sig: sig64(&info.signature), info: ci, recv });
                            }
                        }
                    }
                    Some(subscribe_update_deshred::UpdateOneof::Ping(_)) => {
                        let _ = sink.send(SubscribeDeshredRequest { deshred_transactions: filt.clone(),
                            ping: Some(SubscribeRequestPing { id: 1 }), ..Default::default() }).await;
                    }
                    _ => {}
                }
            }
            Ok(())
        }.await;
        eprintln!("deshred stream ended: {:?}; reconnect in {}s", r.err(), backoff);
        tokio::time::sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(30);
    }
}

/// processed transactions: all pump txs (curve tracking + our fills) and txs of watched funders.
pub async fn run_processed(endpoint: String, token: String, funders: Vec<Pubkey>, me: Pubkey,
                           tx: mpsc::UnboundedSender<Ev>) {
    let funder_set: std::collections::HashSet<Pubkey> = funders.iter().copied().collect();
    let mut backoff = 1u64;
    loop {
        let r: anyhow::Result<()> = async {
            let mut client = GeyserGrpcClient::build_from_shared(endpoint.clone())?
                .x_token(Some(token.clone()))?
                .tls_config(ClientTlsConfig::new().with_native_roots())?
                .tcp_nodelay(true)
                .http2_adaptive_window(true)
                .initial_connection_window_size(16 * 1024 * 1024)
                .initial_stream_window_size(8 * 1024 * 1024)
                .connect()
                .await?;
            let mut txf = HashMap::from([("pump".to_string(), SubscribeRequestFilterTransactions {
                vote: Some(false), failed: None, signature: None, account_include: vec![PUMP.to_string()],
                account_exclude: vec![], account_required: vec![], ..Default::default()
            })]);
            if !funders.is_empty() {
                txf.insert("funders".to_string(), SubscribeRequestFilterTransactions {
                    vote: Some(false), failed: Some(false), signature: None,
                    account_include: funders.iter().map(|p| p.to_string()).collect(),
                    account_exclude: vec![], account_required: vec![], ..Default::default()
                });
            }
            let req = SubscribeRequest {
                transactions: txf,
                slots: HashMap::from([("s".to_string(), SubscribeRequestFilterSlots {
                    filter_by_commitment: Some(false), interslot_updates: Some(false), ..Default::default() })]),
                commitment: Some(CommitmentLevel::Processed as i32),
                ..Default::default()
            };
            let (mut sink, mut stream) = client.subscribe_with_request(Some(req.clone())).await?;
            backoff = 1;
            while let Some(msg) = stream.next().await {
                let msg = msg?;
                match msg.update_oneof {
                    Some(UpdateOneof::Transaction(u)) => {
                        let slot = u.slot;
                        let Some(ti) = u.transaction else { continue };
                        let Some(t) = ti.transaction.as_ref() else { continue };
                        let Some(m) = t.message.as_ref() else { continue };
                        let Some(meta) = ti.meta.as_ref() else { continue };
                        let keys = keys_of(m, &meta.loaded_writable_addresses, &meta.loaded_readonly_addresses);
                        let signer = keys.first().copied().unwrap_or_default();
                        let ok = meta.err.is_none();
                        let sig = sig64(&ti.signature);
                        if !ok {
                            if signer == me { let _ = tx.send(Ev::Failed { slot, sig, signer }); }
                            continue;
                        }
                        // funding transfers out of watched funders (top-level + inner system transfers)
                        if !funder_set.is_empty() {
                            let sys = Pubkey::default();
                            let mut check = |prog: Option<&Pubkey>, accs: &[u8], data: &[u8]| {
                                if prog == Some(&sys) && data.len() == 12 && data[..4] == [2, 0, 0, 0] && accs.len() >= 2 {
                                    if let (Some(from), Some(to)) = (keys.get(accs[0] as usize), keys.get(accs[1] as usize)) {
                                        if funder_set.contains(from) {
                                            let lam = u64::from_le_bytes(data[4..12].try_into().unwrap());
                                            let pre = meta.pre_balances.get(accs[1] as usize).copied().unwrap_or(u64::MAX);
                                            let _ = tx.send(Ev::Funding { slot, funder: *from, to: *to, lamports: lam, to_pre_balance: pre });
                                        }
                                    }
                                }
                            };
                            for ix in &m.instructions { check(keys.get(ix.program_id_index as usize), &ix.accounts, &ix.data); }
                            for inn in &meta.inner_instructions {
                                for ix in &inn.instructions { check(keys.get(ix.program_id_index as usize), &ix.accounts, &ix.data); }
                            }
                        }
                        // pump trade events (self-CPI)
                        for inn in &meta.inner_instructions {
                            for ix in &inn.instructions {
                                if keys.get(ix.program_id_index as usize) != Some(&PUMP) || ix.data.len() < 16 || ix.data[..8] != EVENT_IX_TAG { continue; }
                                if let Some((mint, sol, tok, is_buy, user, vsol, vtok)) = parse_trade_event(&ix.data[8..]) {
                                    let _ = tx.send(Ev::Trade { slot, idx: ti.index, sig, signer, ok, mint, sol, tok, is_buy, user, vsol, vtok });
                                }
                            }
                        }
                    }
                    Some(UpdateOneof::Slot(s)) => { let _ = tx.send(Ev::Slot { slot: s.slot, status: s.status }); }
                    Some(UpdateOneof::Ping(_)) => {
                        let mut p = req.clone();
                        p.ping = Some(SubscribeRequestPing { id: 1 });
                        let _ = sink.send(p).await;
                    }
                    _ => {}
                }
            }
            Ok(())
        }.await;
        eprintln!("processed stream ended: {:?}; reconnect in {}s", r.err(), backoff);
        tokio::time::sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(30);
    }
}
