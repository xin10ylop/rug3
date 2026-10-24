// Minimal pump.fun decoder for Yellowstone gRPC transaction updates (layouts from the official IDL).
'use strict';
const bs58 = require('bs58').default || require('bs58');

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const COMPUTE_BUDGET = 'ComputeBudget111111111111111111111111111111';
const SYSTEM = '11111111111111111111111111111111';
const EVENT_IX_TAG = 'e445a52e51cb9a1d';

const IX = {
  '66063d1201daebea': 'buy', '38fc74089edfcd5f': 'buy_exact_sol_in', 'b817ee6167c5d33d': 'buy_v2',
  '07051dc4f5176550': 'buy_v3', 'c2ab1c46684d5b2f': 'buy_exact_quote_in_v2', 'e1f7501ed5b38488': 'buy_exact_quote_in_v3',
  '33e685a4017f83ad': 'sell', '5df6823ce7e940b2': 'sell_v2', '1c92de7726c469d5': 'sell_v3',
  '181ec828051c0777': 'create', 'd6904cec5f8b31b4': 'create_v2', 'e19a7516d756f667': 'multi_hop_curve_swap',
  '9beae792ec9ea21e': 'migrate', 'bbcb121fceedfe29': 'migrate_v2', 'ea66c2cb96483ee5': 'extend_account', '623691610246ad2b': 'distribute_fee_to_holders', '20f6bf3408c949ba': 'sweep_creator_fee', 'cf118af204221338': 'collect_creator_fee_v2', '1416567bc61cdb84': 'collect_creator_fee', 'a572670079cef751': 'distribute_creator_fees', 'ffcb134ff444089f': 'distribute_creator_fees_v2', '577c34bf3426d6e8': 'migrate_bonding_curve_creator', '0830be07b644b7e5': 'sweep_protocol_fee', '253a237ebe35e4c5': 'claim_cashback', '7af3cc415e741d37': 'claim_cashback_v2',
};
const EV_CREATE = '1b72a94ddeeb6376', EV_TRADE = 'bddb7fd34ee661ee', EV_COMPLETE = '5f72619cd42e9808';

const TIPS = {};
const addTips = (svc, list) => list.forEach((a) => { TIPS[a] = svc; });
addTips('jito', ['96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5', 'HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe', 'Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY', 'ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49', 'DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh', 'ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt', 'DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL', '3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT']);
addTips('helius', ['4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE', 'D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ', '9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta', '5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn', '2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD', '2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ', 'wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF', '3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT', '4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey', '4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or']);
addTips('nozomi', ['TEMPaMeCRFAS9EKF53Jd6KpHxgL47uWLcpFArU1Fanq', 'noz3jAjPiHuBPqiSPkkugaJDkJscPuRhYnSpbi8UvC4', 'noz3str9KXfpKknefHji8L1mPgimezaiUyCHYMDv1GE', 'noz6uoYCDijhu1V7cutCpwxNiSovEwLdRHPwmgCGDNo', 'noz9EPNcT7WH6Sou3sr3GGjHQYVkN3DNirpbvDkv9YJ', 'nozc5yT15LazbLTFVZzoNZCwjh3yUtW86LoUyqsBu4L', 'nozFrhfnNGoyqwVuwPAW4aaGqempx4PU6g6D9CJMv7Z', 'nozievPk7HyK1Rqy1MPJwVQ7qQg2QoJGyP71oeDwbsu', 'noznbgwYnBLDHu8wcQVCEw6kDrXkPdKkydGJGNXGvL7', 'nozNVWs5N8mgzuD3qigrCG2UoKxZttxzZ85pvAQVrbP', 'nozpEGbwx4BcGp6pvEdAh1JoC2CQGZdU6HbNP1v2p6P', 'nozrhjhkCr3zXT3BiT4WCodYCUFeQvcdUkM7MqhKqge', 'nozrwQtWhEdrA6W8dkbt9gnUaMs52PdAv5byipnadq3', 'nozUacTVWub3cL4mJmGCYjKZTnE9RbdY5AP46iQgbPJ', 'nozWCyTPppJjRuw2fpzDhhWbW355fzosWSzrrMYB1Qk', 'nozWNju6dY353eMkMqURqwQEoM3SFgEKC6psLCSfUne', 'nozxNBgWohjR75vdspfxR5H9ceC7XXH99xpxhVGt3Bb']);
addTips('bloxroute', ['HWEoBxYs7ssKuudEjzjmpfJVX7Dvi7wescFsVx2L5yoY', '95cfoy472fcQHaw4tPGBTKpn6ZQnfEPfBgDQx6gcRmRg']);
addTips('0slot', ['Eb2KpSC8uMt9GmzyAEm5Eb1AAAgTjRaXWFjKyFXHZxF3', 'FCjUJZ1qozm1e8romw216qyfQMaaWKxWsuySnumVCCNe', 'ENxTEjSQ1YabmUpXAdCgevnHQ9MHdLv8tzFiuiYJqa13', '6rYLG55Q9RpsPGvqdPNJs4z5WTxJVatMB8zV3WJhs5EK', 'Cix2bHfqPcKcM233mzxbLk14kSggUUiz2A87fJtGivXr']);
addTips('nextblock', ['NextbLoCkVtMGcV47JzewQdvBpLqT9TxQFozQkN98pE', 'NexTbLoCkWykbLuB1NkjXgFWkX9oAtcoagQegygXXA2', 'NeXTBLoCKs9F1y5PJS9CKrFNNLU1keHW71rfh7KgA1X', 'NexTBLockJYZ7QD7p2byrUa6df8ndV2WSd8GkbWqfbb', 'neXtBLock1LeC67jYd1QdAa32kbVeubsfPNTJC1V5At', 'nEXTBLockYgngeRmRrjDV31mGSekVPqZoMGhQEZtPVG', 'NEXTbLoCkB51HpLBLojQfpyVAMorm3zzKg7w9NFdqid', 'nextBLoCkPMgmG8ZgJtABeScP35qLa2AMCNKntAP7Xc']);
addTips('astralane', ['astrazznxsGUhWShqgNtAdfrzP2G83DzcWVJDxwV9bF', 'astra4uejePWneqNaJKuFFA8oonqCE1sqF6b45kDMZm', 'astra9xWY93QyfG6yM8zwsKsRodscjQ2uU2HKNL5prk', 'astraRVUuTHjpwEVvNBeQEgwYx9w9CFyfxjYoobCZhL']);

class R {
  constructor(b, o = 0) { this.b = Buffer.from(b.buffer ? b : Buffer.from(b)); this.o = o; }
  u8() { return this.b[this.o++]; }
  bool() { return this.u8() !== 0; }
  u16() { const v = this.b.readUInt16LE(this.o); this.o += 2; return v; }
  u32() { const v = this.b.readUInt32LE(this.o); this.o += 4; return v; }
  u64() { const v = this.b.readBigUInt64LE(this.o); this.o += 8; return v.toString(); }
  i64() { const v = this.b.readBigInt64LE(this.o); this.o += 8; return Number(v); }
  pk() { const v = this.b.subarray(this.o, this.o + 32); this.o += 32; return bs58.encode(v); }
  str() { const n = this.u32(); const v = this.b.subarray(this.o, this.o + n).toString('utf8'); this.o += n; return v; }
}

function decodeEvent(data) {
  const disc = Buffer.from(data.subarray(0, 8)).toString('hex');
  const r = new R(data, 8);
  try {
    if (disc === EV_TRADE) {
      const e = { ev: 'trade', mint: r.pk(), sol: r.u64(), tok: r.u64(), buy: r.bool(), user: r.pk(), ts: r.i64(),
        vsol: r.u64(), vtok: r.u64(), rsol: r.u64(), rtok: r.u64(), fee_recipient: r.pk(), fee_bps: r.u64(), fee: r.u64(),
        creator: r.pk(), cfee_bps: r.u64(), cfee: r.u64() };
      try {
        r.bool(); r.u64(); r.u64(); r.u64(); r.i64();
        e.ix = r.str(); e.mayhem = r.bool(); e.cb_bps = r.u64(); e.cb = r.u64(); e.bb_bps = r.u64(); e.bb = r.u64();
        const n = r.u32(); for (let i = 0; i < n; i++) { r.pk(); r.u16(); }
        e.quote = r.pk(); e.qamt = r.u64(); e.vq = r.u64(); e.rq = r.u64();
      } catch (_) { /* older/shorter layout */ }
      return e;
    }
    if (disc === EV_CREATE) {
      const e = { ev: 'create', name: r.str(), symbol: r.str(), uri: r.str(), mint: r.pk(), bc: r.pk(), user: r.pk(),
        creator: r.pk(), ts: r.i64(), vtok: r.u64(), vsol: r.u64(), rtok: r.u64(), supply: r.u64() };
      try { e.token_program = r.pk(); e.mayhem = r.bool(); e.cashback = r.bool(); e.quote = r.pk(); e.vq = r.u64(); e.cfee_bps = r.u64(); e.holder_reward = r.bool(); e.depth = r.u8(); } catch (_) { }
      return e;
    }
    if (disc === EV_COMPLETE) return { ev: 'complete', user: r.pk(), mint: r.pk(), bc: r.pk(), ts: r.i64() };
  } catch (_) { return null; }
  return null;
}

function ixArgs(name, data) {
  // returns compact args for trade ixs (all start with two u64 after the 8-byte disc)
  try {
    const b = Buffer.from(data);
    if (b.length < 24) return undefined;
    const a = b.readBigUInt64LE(8).toString(), c = b.readBigUInt64LE(16).toString();
    if (name.startsWith('buy_exact')) return { spend: a, min_out: c };
    if (name.startsWith('buy')) return { amount: a, max_cost: c };
    if (name.startsWith('sell')) return { amount: a, min_out: c };
  } catch (_) { }
  return undefined;
}

// Decode a Yellowstone SubscribeUpdateTransactionInfo-like object into a compact record.
function decodeTx(ti, slot) {
  const msg = ti.transaction.message;
  const meta = ti.meta || {};
  const keys = msg.accountKeys.map((k) => bs58.encode(k));
  const all = keys.concat((meta.loadedWritableAddresses || []).map((k) => bs58.encode(k)), (meta.loadedReadonlyAddresses || []).map((k) => bs58.encode(k)));
  const nsig = msg.header.numRequiredSignatures;
  const rec = { slot: Number(slot), idx: Number(ti.index), sig: bs58.encode(ti.signature), signer: keys[0], nsig,
    ok: !meta.err, fee: Number(meta.fee || 0), cu: Number(meta.computeUnitsConsumed || 0), cuP: 0, cuL: null,
    ixs: [], ev: [], tips: [] };
  if (nsig > 1) rec.signers = keys.slice(0, nsig);
  if (msg.config) { rec.v1 = { prio: msg.config.priorityFee !== undefined ? Number(msg.config.priorityFee) : undefined, cuL: msg.config.computeUnitLimit !== undefined ? Number(msg.config.computeUnitLimit) : undefined }; }
  const handle = (prog, accs, data, inner) => {
    if (prog === COMPUTE_BUDGET && !inner) {
      if (data[0] === 3 && data.length >= 9) rec.cuP = Number(Buffer.from(data).readBigUInt64LE(1));
      else if (data[0] === 2 && data.length >= 5) rec.cuL = Buffer.from(data).readUInt32LE(1);
    } else if (prog === SYSTEM) {
      if (data.length === 12 && data[0] === 2 && data[1] === 0 && data[2] === 0 && data[3] === 0 && accs.length >= 2) {
        const to = all[accs[1]];
        const svc = TIPS[to];
        if (svc) rec.tips.push([svc, Number(Buffer.from(data).readBigUInt64LE(4))]);
      }
    } else if (prog === PUMP) {
      const disc = Buffer.from(data.subarray(0, 8)).toString('hex');
      if (inner && disc === EVENT_IX_TAG) {
        const e = decodeEvent(data.subarray(8));
        if (e) rec.ev.push(e);
      } else {
        const name = IX[disc] || ('?' + disc);
        const ix = { n: name, inner: inner || undefined };
        const args = ixArgs(name, data);
        if (args) ix.a = args;
        if (name === 'create' || name === 'create_v2') ix.mint = all[accs[0]];
        else if (name === 'buy' || name === 'buy_exact_sol_in' || name.startsWith('sell') && name.length === 4) ix.mint = all[accs[2]];
        rec.ixs.push(ix);
      }
    }
  };
  for (const ix of msg.instructions) handle(all[ix.programIdIndex], ix.accounts, ix.data, false);
  for (const inn of meta.innerInstructions || []) for (const ix of inn.instructions) handle(all[ix.programIdIndex], ix.accounts, ix.data, true);
  if (!rec.tips.length) delete rec.tips;
  return rec;
}

// Decode a deshred (pre-execution) transaction: no meta, just intent.
function decodeDeshred(info, slot) {
  const msg = info.transaction.message;
  const keys = msg.accountKeys.map((k) => bs58.encode(k));
  const all = keys.concat((info.loadedWritableAddresses || []).map((k) => bs58.encode(k)), (info.loadedReadonlyAddresses || []).map((k) => bs58.encode(k)));
  const rec = { slot: Number(slot), sig: bs58.encode(info.signature), signer: keys[0], ixs: [] };
  let cuP = 0;
  for (const ix of msg.instructions) {
    const prog = all[ix.programIdIndex];
    const data = ix.data;
    if (prog === COMPUTE_BUDGET && data[0] === 3 && data.length >= 9) cuP = Number(Buffer.from(data).readBigUInt64LE(1));
    if (prog === PUMP) {
      const disc = Buffer.from(data.subarray(0, 8)).toString('hex');
      const name = IX[disc] || ('?' + disc);
      const o = { n: name };
      if (name === 'create' || name === 'create_v2') o.mint = all[ix.accounts[0]];
      else if (name === 'buy' || name === 'buy_exact_sol_in' || name === 'sell') o.mint = all[ix.accounts[2]];
      const args = ixArgs(name, data);
      if (args) o.a = args;
      rec.ixs.push(o);
    }
  }
  if (cuP) rec.cuP = cuP;
  if (msg.config && msg.config.priorityFee !== undefined) rec.prio = Number(msg.config.priorityFee);
  return rec;
}

module.exports = { decodeTx, decodeDeshred, decodeEvent, PUMP, TIPS };
