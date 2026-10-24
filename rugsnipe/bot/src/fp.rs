//! Pre-execution bot fingerprint of a pump.fun create transaction (from the deshred stream).
//! Must equal research/features.py launch_keys "fp" for SOL-paired coins, which is computed after execution:
//!   round(dev SOL, 2) | min(priority fee // 10_000, 50) | first tip service | creator_fee_bps | cashback | #pump ixs
//! (creator_fee_bps is 0 and cashback False for every SOL-paired create; cashback was deprecated.)

use solana_pubkey::{pubkey, Pubkey};

use crate::pump::{COMPUTE_BUDGET, PUMP, SYSTEM, VSOL0, VTOK0};

pub struct TopIx<'a> {
    pub program: Pubkey,
    pub accounts: Vec<Pubkey>,
    pub data: &'a [u8],
}

const DISC_BUY: [u8; 8] = [0x66, 0x06, 0x3d, 0x12, 0x01, 0xda, 0xeb, 0xea];
const DISC_BUY_EXACT_SOL_IN: [u8; 8] = [0x38, 0xfc, 0x74, 0x08, 0x9e, 0xdf, 0xcd, 0x5f];
const DISC_BUY_V2: [u8; 8] = [0xb8, 0x17, 0xee, 0x61, 0x67, 0xc5, 0xd3, 0x3d];
const DISC_BUY_V3: [u8; 8] = [0x07, 0x05, 0x1d, 0xc4, 0xf5, 0x17, 0x65, 0x50];
const DISC_BUY_EXACT_QUOTE_IN_V2: [u8; 8] = [0xc2, 0xab, 0x1c, 0x46, 0x68, 0x4d, 0x5b, 0x2f];
const DISC_BUY_EXACT_QUOTE_IN_V3: [u8; 8] = [0xe1, 0xf7, 0x50, 0x1e, 0xd5, 0xb3, 0x84, 0x88];

/// (index of the buyer account, spends an exact SOL amount?) per buy instruction; indices verified on executed creates
fn buy_kind(disc: &[u8]) -> Option<(usize, bool)> {
    let d: [u8; 8] = disc.get(..8)?.try_into().ok()?;
    match d {
        DISC_BUY => Some((6, false)),
        DISC_BUY_EXACT_SOL_IN => Some((6, true)),
        DISC_BUY_V2 => Some((13, false)),
        DISC_BUY_EXACT_QUOTE_IN_V2 => Some((13, true)),
        DISC_BUY_V3 => Some((8, false)),
        DISC_BUY_EXACT_QUOTE_IN_V3 => Some((8, true)),
        _ => None,
    }
}

// Outer programs whose instructions are known (from executed creates) to run pump instructions by CPI; research/features.py
// counts those inner pump ixs, so we add them here. pump fees: sharing-config setup; rapid router: the dev buy.
const PUMP_FEES: Pubkey = pubkey!("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ");
const PFEE_INNER: [([u8; 8], usize); 2] = [([0xc3, 0x4e, 0x56, 0x4c, 0x6f, 0x34, 0xfb, 0xd5], 2), ([0xbd, 0x0d, 0x88, 0x63, 0xbb, 0xa4, 0xed, 0x23], 1)];
/// rapid router: tag 0 = buy_exact_quote_in_v2 by accounts[0] for the SOL amount at data[1..9] (verified on executed creates)
const RAPID: Pubkey = pubkey!("rapidV5DAKtecYzetwCNoxXWHpezjeg4hGt9BGiVcGW");

// builtins get a 3,000 CU default limit, every other instruction 200,000 (capped at 1.4M per tx)
const BUILTINS: [Pubkey; 4] = [SYSTEM, COMPUTE_BUDGET, pubkey!("AddressLookupTab1e1111111111111111111111111"),
    pubkey!("Ed25519SigVerify111111111111111111111111111")];

const REAL_TOK0: u128 = 793_100_000_000_000;

fn u64_at(d: &[u8], o: usize) -> Option<u64> { Some(u64::from_le_bytes(d.get(o..o + 8)?.try_into().ok()?)) }

/// Python's str(round(lamports / 1e9, 2)) (exact ties, which need a lamport count ending in 5_000_000, round up)
fn sol_2dp(lamports: u64) -> String {
    let q = (lamports + 5_000_000) / 10_000_000;
    match (q % 100, q % 10) {
        (0, _) => format!("{}.0", q / 100),
        (c, 0) => format!("{}.{}", q / 100, c / 10),
        (c, _) => format!("{}.{:02}", q / 100, c),
    }
}

pub fn tip_service(to: &Pubkey) -> Option<&'static str> { TIP_ACCOUNTS.iter().find(|(a, _)| a == to).map(|(_, s)| *s) }

/// `v1_priority_fee`: Some(fee) for V1 messages (inline TransactionConfig), None for legacy/V0 (ComputeBudget ixs).
pub fn fingerprint(ixs: &[TopIx], deployer: &Pubkey, v1_priority_fee: Option<Option<u64>>) -> String {
    let (mut cu_limit, mut cu_price) = (None::<u64>, 0u64);
    let mut tip = None;
    let (mut n_pump, mut dev) = (0usize, 0u64);
    let (mut vsol, mut vtok) = (VSOL0, VTOK0);
    let mut real_tok = REAL_TOK0; // a buy is capped at the curve's remaining real tokens (curve completion)
    for ix in ixs {
        if ix.program == COMPUTE_BUDGET {
            match ix.data.first() {
                Some(2) if ix.data.len() >= 5 => cu_limit = Some(u32::from_le_bytes(ix.data[1..5].try_into().unwrap()) as u64),
                Some(3) if ix.data.len() >= 9 => cu_price = u64_at(ix.data, 1).unwrap_or(0),
                _ => {}
            }
        } else if ix.program == SYSTEM {
            if tip.is_none() && ix.data.len() >= 12 && ix.data[..4] == [2, 0, 0, 0] && ix.accounts.len() >= 2 {
                tip = tip_service(&ix.accounts[1]);
            }
        } else if ix.program == PUMP {
            n_pump += 1;
            let (Some((uidx, exact_in)), Some(a1)) = (buy_kind(ix.data), u64_at(ix.data, 8)) else { continue };
            let net = (a1 as u128) * 10_000 / 10_125;
            let want = if exact_in { net * vtok / (vsol + net) } else { a1 as u128 };
            let tok = want.min(real_tok);
            let sol = if exact_in && tok == want { net } else { tok * vsol / (vtok - tok) + 1 };
            vsol += sol;
            vtok -= tok;
            real_tok -= tok;
            if ix.accounts.get(uidx) == Some(deployer) { dev += sol as u64; }
        } else if ix.program == PUMP_FEES {
            n_pump += PFEE_INNER.iter().find(|(d, _)| ix.data.get(..8) == Some(&d[..])).map_or(0, |(_, n)| *n);
        } else if ix.program == RAPID {
            n_pump += 1;
            if let (Some(0), Some(a1)) = (ix.data.first(), u64_at(ix.data, 1)) {
                let net = (a1 as u128) * 10_000 / 10_125;
                let want = net * vtok / (vsol + net);
                let tok = want.min(real_tok);
                let sol = if tok == want { net } else { tok * vsol / (vtok - tok) + 1 };
                vsol += sol;
                vtok -= tok;
                real_tok -= tok;
                if ix.accounts.first() == Some(deployer) { dev += sol as u64; }
            }
        }
    }
    let prio = match v1_priority_fee {
        Some(f) => f.unwrap_or(0),
        None => {
            let limit = cu_limit.unwrap_or_else(|| ixs.iter()
                .map(|ix| if BUILTINS.contains(&ix.program) { 3_000 } else { 200_000 }).sum::<u64>().min(1_400_000));
            ((cu_price as u128 * limit as u128 + 999_999) / 1_000_000) as u64
        }
    };
    format!("{}|{}|{}|0|False|{}", sol_2dp(dev), (prio / 10_000).min(50), tip.unwrap_or("none"), n_pump)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dev_buy_capped_at_curve_completion() {
        // exact-token dev buy larger than the curve's real tokens costs only the completing amount (85.01 SOL, seen live)
        let dep = Pubkey::new_unique();
        let mut create = DISC_BUY.to_vec();
        create.extend_from_slice(&900_000_000_000_000u64.to_le_bytes());
        create.extend_from_slice(&u64::MAX.to_le_bytes());
        let mut accs = vec![Pubkey::new_unique(); 6];
        accs.push(dep);
        let ixs = [TopIx { program: PUMP, accounts: accs, data: &create }];
        assert!(fingerprint(&ixs, &dep, None).starts_with("85.01|0|none|0|False|1"));
    }

    #[test]
    fn rounding_matches_python() {
        assert_eq!(sol_2dp(0), "0.0");
        assert_eq!(sol_2dp(987_654_320), "0.99");
        assert_eq!(sol_2dp(1_000_000_000), "1.0");
        assert_eq!(sol_2dp(2_960_000_000), "2.96");
        assert_eq!(sol_2dp(100_000_000), "0.1");
        assert_eq!(sol_2dp(1_484_999_999), "1.48");
        assert_eq!(sol_2dp(4_938_271_604), "4.94");
        assert_eq!(sol_2dp(12_345_678_901), "12.35");
    }
}

/// Tip accounts of landing services (same table as research/pumpdecode.py TIP_ACCOUNTS).
pub const TIP_ACCOUNTS: &[(Pubkey, &str)] = &[
    (pubkey!("96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5"), "jito"),
    (pubkey!("HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe"), "jito"),
    (pubkey!("Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY"), "jito"),
    (pubkey!("ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49"), "jito"),
    (pubkey!("DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh"), "jito"),
    (pubkey!("ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt"), "jito"),
    (pubkey!("DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL"), "jito"),
    (pubkey!("3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT"), "jito"),
    (pubkey!("4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE"), "helius"),
    (pubkey!("D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ"), "helius"),
    (pubkey!("9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta"), "helius"),
    (pubkey!("5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn"), "helius"),
    (pubkey!("2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD"), "helius"),
    (pubkey!("2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ"), "helius"),
    (pubkey!("wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF"), "helius"),
    (pubkey!("3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT"), "helius"),
    (pubkey!("4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey"), "helius"),
    (pubkey!("4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or"), "helius"),
    (pubkey!("TEMPaMeCRFAS9EKF53Jd6KpHxgL47uWLcpFArU1Fanq"), "nozomi"),
    (pubkey!("noz3jAjPiHuBPqiSPkkugaJDkJscPuRhYnSpbi8UvC4"), "nozomi"),
    (pubkey!("noz3str9KXfpKknefHji8L1mPgimezaiUyCHYMDv1GE"), "nozomi"),
    (pubkey!("noz6uoYCDijhu1V7cutCpwxNiSovEwLdRHPwmgCGDNo"), "nozomi"),
    (pubkey!("noz9EPNcT7WH6Sou3sr3GGjHQYVkN3DNirpbvDkv9YJ"), "nozomi"),
    (pubkey!("nozc5yT15LazbLTFVZzoNZCwjh3yUtW86LoUyqsBu4L"), "nozomi"),
    (pubkey!("nozFrhfnNGoyqwVuwPAW4aaGqempx4PU6g6D9CJMv7Z"), "nozomi"),
    (pubkey!("nozievPk7HyK1Rqy1MPJwVQ7qQg2QoJGyP71oeDwbsu"), "nozomi"),
    (pubkey!("noznbgwYnBLDHu8wcQVCEw6kDrXkPdKkydGJGNXGvL7"), "nozomi"),
    (pubkey!("nozNVWs5N8mgzuD3qigrCG2UoKxZttxzZ85pvAQVrbP"), "nozomi"),
    (pubkey!("nozpEGbwx4BcGp6pvEdAh1JoC2CQGZdU6HbNP1v2p6P"), "nozomi"),
    (pubkey!("nozrhjhkCr3zXT3BiT4WCodYCUFeQvcdUkM7MqhKqge"), "nozomi"),
    (pubkey!("nozrwQtWhEdrA6W8dkbt9gnUaMs52PdAv5byipnadq3"), "nozomi"),
    (pubkey!("nozUacTVWub3cL4mJmGCYjKZTnE9RbdY5AP46iQgbPJ"), "nozomi"),
    (pubkey!("nozWCyTPppJjRuw2fpzDhhWbW355fzosWSzrrMYB1Qk"), "nozomi"),
    (pubkey!("nozWNju6dY353eMkMqURqwQEoM3SFgEKC6psLCSfUne"), "nozomi"),
    (pubkey!("nozxNBgWohjR75vdspfxR5H9ceC7XXH99xpxhVGt3Bb"), "nozomi"),
    (pubkey!("HWEoBxYs7ssKuudEjzjmpfJVX7Dvi7wescFsVx2L5yoY"), "bloxroute"),
    (pubkey!("95cfoy472fcQHaw4tPGBTKpn6ZQnfEPfBgDQx6gcRmRg"), "bloxroute"),
    (pubkey!("Eb2KpSC8uMt9GmzyAEm5Eb1AAAgTjRaXWFjKyFXHZxF3"), "0slot"),
    (pubkey!("FCjUJZ1qozm1e8romw216qyfQMaaWKxWsuySnumVCCNe"), "0slot"),
    (pubkey!("ENxTEjSQ1YabmUpXAdCgevnHQ9MHdLv8tzFiuiYJqa13"), "0slot"),
    (pubkey!("6rYLG55Q9RpsPGvqdPNJs4z5WTxJVatMB8zV3WJhs5EK"), "0slot"),
    (pubkey!("Cix2bHfqPcKcM233mzxbLk14kSggUUiz2A87fJtGivXr"), "0slot"),
    (pubkey!("NextbLoCkVtMGcV47JzewQdvBpLqT9TxQFozQkN98pE"), "nextblock"),
    (pubkey!("NexTbLoCkWykbLuB1NkjXgFWkX9oAtcoagQegygXXA2"), "nextblock"),
    (pubkey!("NeXTBLoCKs9F1y5PJS9CKrFNNLU1keHW71rfh7KgA1X"), "nextblock"),
    (pubkey!("NexTBLockJYZ7QD7p2byrUa6df8ndV2WSd8GkbWqfbb"), "nextblock"),
    (pubkey!("neXtBLock1LeC67jYd1QdAa32kbVeubsfPNTJC1V5At"), "nextblock"),
    (pubkey!("nEXTBLockYgngeRmRrjDV31mGSekVPqZoMGhQEZtPVG"), "nextblock"),
    (pubkey!("NEXTbLoCkB51HpLBLojQfpyVAMorm3zzKg7w9NFdqid"), "nextblock"),
    (pubkey!("nextBLoCkPMgmG8ZgJtABeScP35qLa2AMCNKntAP7Xc"), "nextblock"),
    (pubkey!("astrazznxsGUhWShqgNtAdfrzP2G83DzcWVJDxwV9bF"), "astralane"),
    (pubkey!("astra4uejePWneqNaJKuFFA8oonqCE1sqF6b45kDMZm"), "astralane"),
    (pubkey!("astra9xWY93QyfG6yM8zwsKsRodscjQ2uU2HKNL5prk"), "astralane"),
    (pubkey!("astraRVUuTHjpwEVvNBeQEgwYx9w9CFyfxjYoobCZhL"), "astralane"),
];
