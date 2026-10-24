//! pump.fun bonding-curve instruction builders (v3 trade ixs, 17 accounts) + curve math + create parsing.
//! Layouts: github.com/pump-fun/pump-public-docs (idl/pump.json, docs/instructions/TRADE_V3.md).

use solana_pubkey::{pubkey, Pubkey};

use crate::tx::{AccountMeta, Ix};

pub const PUMP: Pubkey = pubkey!("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");
pub const FEE_CONFIG: Pubkey = pubkey!("8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt");
pub const TOKEN: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
pub const TOKEN_2022: Pubkey = pubkey!("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
pub const ATA_PROGRAM: Pubkey = pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");
pub const SYSTEM: Pubkey = pubkey!("11111111111111111111111111111111");
pub const WSOL: Pubkey = pubkey!("So11111111111111111111111111111111111111112");
pub const COMPUTE_BUDGET: Pubkey = pubkey!("ComputeBudget111111111111111111111111111111");

pub const BUYBACK_FEE_RECIPIENTS: [Pubkey; 8] = [
    pubkey!("5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD"),
    pubkey!("9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7"),
    pubkey!("GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL"),
    pubkey!("3BpXnfJaUTiwXnJNe7Ej1rcbzqTTQUvLShZaWazebsVR"),
    pubkey!("5cjcW9wExnJJiqgLjq7DEG75Pm6JBgE1hNv4B2vHXUW6"),
    pubkey!("EHAAiTxcdDwQ3U4bU6YcMsQGaekdzLS3B5SmYo46kJtL"),
    pubkey!("5eHhjP8JaYkz83CWwvGU2uMUXefd3AazWGx4gpcuEEYD"),
    pubkey!("A7hAgCzFw14fejgCp387JUJRMNyz4j89JKnhtKU8piqW"),
];

const DISC_BUY_EXACT_QUOTE_IN_V3: [u8; 8] = [0xe1, 0xf7, 0x50, 0x1e, 0xd5, 0xb3, 0x84, 0x88];
const DISC_SELL_V3: [u8; 8] = [0x1c, 0x92, 0xde, 0x77, 0x26, 0xc4, 0x69, 0xd5];
pub const DISC_CREATE: [u8; 8] = [0x18, 0x1e, 0xc8, 0x28, 0x05, 0x1c, 0x07, 0x77];
pub const DISC_CREATE_V2: [u8; 8] = [0xd6, 0x90, 0x4c, 0xec, 0x5f, 0x8b, 0x31, 0xb4];
pub const EVENT_IX_TAG: [u8; 8] = [0xe4, 0x45, 0xa5, 0x2e, 0x51, 0xcb, 0x9a, 0x1d];
pub const EV_TRADE: [u8; 8] = [0xbd, 0xdb, 0x7f, 0xd3, 0x4e, 0xe6, 0x61, 0xee];

pub const VSOL0: u128 = 30_000_000_000;
pub const VTOK0: u128 = 1_073_000_000_000_000;
pub const SUPPLY_RAW: f64 = 1e15;

pub fn pda(seeds: &[&[u8]], program: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(seeds, program).0
}

pub fn ata(owner: &Pubkey, mint: &Pubkey, token_program: &Pubkey) -> Pubkey {
    pda(&[owner.as_ref(), token_program.as_ref(), mint.as_ref()], &ATA_PROGRAM)
}

/// Static per-wallet / per-mint account set for v3 trades on a SOL-paired coin.
#[derive(Clone, Debug)]
pub struct TradeAccounts {
    pub global: Pubkey,
    pub mint: Pubkey,
    pub base_token_program: Pubkey,
    pub bonding_curve: Pubkey,
    pub assoc_base_bc: Pubkey,
    pub assoc_quote_bc: Pubkey,
    pub user: Pubkey,
    pub assoc_base_user: Pubkey,
    pub assoc_quote_user: Pubkey,
    pub user_volume_acc: Pubkey,
    pub buyback: Pubkey,
    pub event_authority: Pubkey,
}

pub struct Statics {
    pub global: Pubkey,
    pub event_authority: Pubkey,
}

impl Statics {
    pub fn new() -> Self {
        Self { global: pda(&[b"global"], &PUMP), event_authority: pda(&[b"__event_authority"], &PUMP) }
    }
}

impl TradeAccounts {
    pub fn new(st: &Statics, user: &Pubkey, uva: &Pubkey, mint: &Pubkey, base_token_program: &Pubkey, buyback_i: usize) -> Self {
        let bonding_curve = pda(&[b"bonding-curve", mint.as_ref()], &PUMP);
        Self {
            global: st.global,
            mint: *mint,
            base_token_program: *base_token_program,
            bonding_curve,
            assoc_base_bc: ata(&bonding_curve, mint, base_token_program),
            assoc_quote_bc: ata(&bonding_curve, &WSOL, &TOKEN),
            user: *user,
            assoc_base_user: ata(user, mint, base_token_program),
            assoc_quote_user: ata(user, &WSOL, &TOKEN),
            user_volume_acc: *uva,
            buyback: BUYBACK_FEE_RECIPIENTS[buyback_i % 8],
            event_authority: st.event_authority,
        }
    }

    fn metas(&self) -> Vec<AccountMeta> {
        vec![
            AccountMeta::ro(self.global),
            AccountMeta::ro(self.mint),
            AccountMeta::ro(WSOL),
            AccountMeta::ro(self.base_token_program),
            AccountMeta::ro(TOKEN),
            AccountMeta::w(self.bonding_curve),
            AccountMeta::w(self.assoc_base_bc),
            AccountMeta::w(self.assoc_quote_bc),
            AccountMeta::ws(self.user),
            AccountMeta::w(self.assoc_base_user),
            AccountMeta::w(self.assoc_quote_user),
            AccountMeta::w(self.user_volume_acc),
            AccountMeta::ro(FEE_CONFIG),
            AccountMeta::w(self.buyback),
            AccountMeta::ro(SYSTEM),
            AccountMeta::ro(self.event_authority),
            AccountMeta::ro(PUMP),
        ]
    }

    pub fn buy_exact_sol_in_v3(&self, spend_lamports: u64, min_tokens_out: u64) -> Ix {
        let mut data = Vec::with_capacity(24);
        data.extend_from_slice(&DISC_BUY_EXACT_QUOTE_IN_V3);
        data.extend_from_slice(&spend_lamports.to_le_bytes());
        data.extend_from_slice(&min_tokens_out.to_le_bytes());
        Ix { program: PUMP, accounts: self.metas(), data }
    }

    pub fn sell_v3(&self, amount: u64, min_sol_out: u64) -> Ix {
        let mut data = Vec::with_capacity(24);
        data.extend_from_slice(&DISC_SELL_V3);
        data.extend_from_slice(&amount.to_le_bytes());
        data.extend_from_slice(&min_sol_out.to_le_bytes());
        Ix { program: PUMP, accounts: self.metas(), data }
    }

    pub fn create_user_ata_idempotent(&self) -> Ix {
        Ix {
            program: ATA_PROGRAM,
            accounts: vec![
                AccountMeta::ws(self.user),
                AccountMeta::w(self.assoc_base_user),
                AccountMeta::ro(self.user),
                AccountMeta::ro(self.mint),
                AccountMeta::ro(SYSTEM),
                AccountMeta::ro(self.base_token_program),
            ],
            data: vec![1],
        }
    }

    /// Close our (now empty) token account and recover its rent.
    pub fn close_user_ata(&self) -> Ix {
        Ix {
            program: self.base_token_program,
            accounts: vec![AccountMeta::w(self.assoc_base_user), AccountMeta::w(self.user), AccountMeta::ws(self.user)],
            data: vec![9],
        }
    }
}

pub fn cu_limit(units: u32) -> Ix {
    let mut d = vec![2u8];
    d.extend_from_slice(&units.to_le_bytes());
    Ix { program: COMPUTE_BUDGET, accounts: vec![], data: d }
}

pub fn cu_price(micro_lamports: u64) -> Ix {
    let mut d = vec![3u8];
    d.extend_from_slice(&micro_lamports.to_le_bytes());
    Ix { program: COMPUTE_BUDGET, accounts: vec![], data: d }
}

pub fn transfer(from: &Pubkey, to: &Pubkey, lamports: u64) -> Ix {
    let mut d = vec![2u8, 0, 0, 0];
    d.extend_from_slice(&lamports.to_le_bytes());
    Ix { program: SYSTEM, accounts: vec![AccountMeta::ws(*from), AccountMeta::w(*to)], data: d }
}

// ------------------------------------------------------------------ curve math (constant product on virtual reserves)
#[derive(Clone, Copy, Debug)]
pub struct Curve {
    pub vsol: u128,
    pub vtok: u128,
}

impl Curve {
    pub fn initial() -> Self { Self { vsol: VSOL0, vtok: VTOK0 } }
    pub fn mcap_sol(&self) -> f64 { self.vsol as f64 / self.vtok as f64 * SUPPLY_RAW / 1e9 }
    /// tokens out for a net (post-fee) SOL amount
    pub fn buy_tokens(&self, sol_net: u128) -> u128 {
        let k = self.vsol * self.vtok;
        let nvsol = self.vsol + sol_net;
        let nvtok = (k + nvsol - 1) / nvsol;
        self.vtok.saturating_sub(nvtok)
    }
    /// gross SOL out for selling tokens
    pub fn sell_sol(&self, tok: u128) -> u128 {
        let k = self.vsol * self.vtok;
        let nvtok = self.vtok + tok;
        let nvsol = (k + nvtok - 1) / nvtok;
        self.vsol.saturating_sub(nvsol)
    }
}

// ------------------------------------------------------------------ create instruction parsing (pre-execution)
#[derive(Clone, Debug)]
pub struct CreateInfo {
    pub mint: Pubkey,
    pub user: Pubkey,
    pub creator: Pubkey,
    pub token_program: Pubkey,
    pub sol_paired: bool,
    pub mayhem: bool,
    pub v2: bool,
}

fn read_str(d: &[u8], o: &mut usize) -> Option<()> {
    if *o + 4 > d.len() { return None; }
    let n = u32::from_le_bytes(d[*o..*o + 4].try_into().ok()?) as usize;
    *o += 4 + n;
    if *o > d.len() { None } else { Some(()) }
}

/// Parse a pump create / create_v2 instruction given resolved account keys of the instruction.
pub fn parse_create(data: &[u8], accounts: &[Pubkey]) -> Option<CreateInfo> {
    if data.len() < 8 { return None; }
    let disc: [u8; 8] = data[..8].try_into().ok()?;
    let v2 = disc == DISC_CREATE_V2;
    if !v2 && disc != DISC_CREATE { return None; }
    let mut o = 8;
    read_str(data, &mut o)?;
    read_str(data, &mut o)?;
    read_str(data, &mut o)?;
    if o + 32 > data.len() { return None; }
    let creator = Pubkey::try_from(&data[o..o + 32]).ok()?;
    o += 32;
    if v2 {
        let mayhem = data.get(o).copied().unwrap_or(0) != 0;
        // create_v2 accounts: 0 mint, 5 user, 7 token_program; >16 accounts => quote-mint remaining accounts
        if accounts.len() < 16 { return None; }
        Some(CreateInfo { mint: accounts[0], user: accounts[5], creator, token_program: accounts[7],
            sol_paired: accounts.len() == 16, mayhem, v2 })
    } else {
        // legacy create: 0 mint, 7 user, 9 token_program
        if accounts.len() < 14 { return None; }
        Some(CreateInfo { mint: accounts[0], user: accounts[7], creator, token_program: TOKEN, sol_paired: true,
            mayhem: false, v2 })
    }
}

/// Decode a TradeEvent payload (after the 8-byte EVENT_IX_TAG): returns (mint, sol, tok, is_buy, user, vsol, vtok).
pub fn parse_trade_event(d: &[u8]) -> Option<(Pubkey, u64, u64, bool, Pubkey, u64, u64)> {
    if d.len() < 8 + 32 + 8 + 8 + 1 + 32 + 8 + 8 + 8 || d[..8] != EV_TRADE { return None; }
    let mut o = 8;
    let mint = Pubkey::try_from(&d[o..o + 32]).ok()?; o += 32;
    let sol = u64::from_le_bytes(d[o..o + 8].try_into().ok()?); o += 8;
    let tok = u64::from_le_bytes(d[o..o + 8].try_into().ok()?); o += 8;
    let is_buy = d[o] != 0; o += 1;
    let user = Pubkey::try_from(&d[o..o + 32]).ok()?; o += 32;
    o += 8; // timestamp
    let vsol = u64::from_le_bytes(d[o..o + 8].try_into().ok()?); o += 8;
    let vtok = u64::from_le_bytes(d[o..o + 8].try_into().ok()?);
    Some((mint, sol, tok, is_buy, user, vsol, vtok))
}
