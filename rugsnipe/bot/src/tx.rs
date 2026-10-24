//! Minimal legacy transaction builder/serializer + ed25519 signing (no solana-sdk dependency).

use solana_keypair::Keypair;
use solana_pubkey::Pubkey;
use solana_signer::Signer;

#[derive(Clone, Debug)]
pub struct AccountMeta {
    pub pubkey: Pubkey,
    pub signer: bool,
    pub writable: bool,
}

impl AccountMeta {
    pub fn ro(pubkey: Pubkey) -> Self { Self { pubkey, signer: false, writable: false } }
    pub fn w(pubkey: Pubkey) -> Self { Self { pubkey, signer: false, writable: true } }
    pub fn ws(pubkey: Pubkey) -> Self { Self { pubkey, signer: true, writable: true } }
}

#[derive(Clone, Debug)]
pub struct Ix {
    pub program: Pubkey,
    pub accounts: Vec<AccountMeta>,
    pub data: Vec<u8>,
}

fn shortvec(out: &mut Vec<u8>, mut n: usize) {
    loop {
        let mut b = (n & 0x7f) as u8;
        n >>= 7;
        if n == 0 {
            out.push(b);
            return;
        }
        b |= 0x80;
        out.push(b);
    }
}

/// Compile + sign a legacy transaction. Returns (wire bytes, signature bytes).
pub fn build_signed(payer: &Keypair, ixs: &[Ix], blockhash: &[u8; 32]) -> (Vec<u8>, [u8; 64]) {
    let msg = compile(&payer.pubkey(), ixs, blockhash);
    let sig = payer.sign_message(&msg);
    let sig_bytes: [u8; 64] = sig.into();
    let mut wire = Vec::with_capacity(1 + 64 + msg.len());
    shortvec(&mut wire, 1);
    wire.extend_from_slice(&sig_bytes);
    wire.extend_from_slice(&msg);
    (wire, sig_bytes)
}

/// Unsigned wire tx (zero signature) for simulateTransaction with sigVerify=false.
pub fn build_unsigned(payer: &Pubkey, ixs: &[Ix], blockhash: &[u8; 32]) -> Vec<u8> {
    let msg = compile(payer, ixs, blockhash);
    let mut wire = Vec::with_capacity(1 + 64 + msg.len());
    shortvec(&mut wire, 1);
    wire.extend_from_slice(&[0u8; 64]);
    wire.extend_from_slice(&msg);
    wire
}

fn compile(payer_pk: &Pubkey, ixs: &[Ix], blockhash: &[u8; 32]) -> Vec<u8> {
    let payer_pk = *payer_pk;
    let mut keys: Vec<(Pubkey, bool, bool)> = vec![(payer_pk, true, true)];
    let mut upsert = |pk: Pubkey, s: bool, w: bool, keys: &mut Vec<(Pubkey, bool, bool)>| {
        if let Some(e) = keys.iter_mut().find(|e| e.0 == pk) {
            e.1 |= s;
            e.2 |= w;
        } else {
            keys.push((pk, s, w));
        }
    };
    for ix in ixs {
        for a in &ix.accounts {
            upsert(a.pubkey, a.signer, a.writable, &mut keys);
        }
        upsert(ix.program, false, false, &mut keys);
    }
    // order: writable signers, readonly signers, writable non-signers, readonly non-signers (payer stays first)
    let payer_entry = keys.remove(0);
    let mut ws: Vec<_> = keys.iter().filter(|k| k.1 && k.2).cloned().collect();
    let rs: Vec<_> = keys.iter().filter(|k| k.1 && !k.2).cloned().collect();
    let wn: Vec<_> = keys.iter().filter(|k| !k.1 && k.2).cloned().collect();
    let rn: Vec<_> = keys.iter().filter(|k| !k.1 && !k.2).cloned().collect();
    ws.insert(0, payer_entry);
    let num_signers = ws.len() + rs.len();
    let ordered: Vec<Pubkey> = ws.iter().chain(rs.iter()).chain(wn.iter()).chain(rn.iter()).map(|k| k.0).collect();
    let idx = |pk: &Pubkey| ordered.iter().position(|k| k == pk).unwrap() as u8;

    let mut msg = Vec::with_capacity(1024);
    msg.push(num_signers as u8);
    msg.push(rs.len() as u8);
    msg.push(rn.len() as u8);
    shortvec(&mut msg, ordered.len());
    for k in &ordered {
        msg.extend_from_slice(k.as_ref());
    }
    msg.extend_from_slice(blockhash);
    shortvec(&mut msg, ixs.len());
    for ix in ixs {
        msg.push(idx(&ix.program));
        shortvec(&mut msg, ix.accounts.len());
        for a in &ix.accounts {
            msg.push(idx(&a.pubkey));
        }
        shortvec(&mut msg, ix.data.len());
        msg.extend_from_slice(&ix.data);
    }
    assert_eq!(num_signers, 1, "only single-signer transactions are supported");
    msg
}
