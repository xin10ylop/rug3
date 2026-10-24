// Live recorder: every pump.fun transaction (processed, incl. failed) + pre-execution deshred stream + slot timing.
// Output: data/rec/{proc,deshred,slots}-YYYYMMDD-HH.jsonl.gz  (one JSON object per line, field t = local receive ms)
'use strict';
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const { performance } = require('perf_hooks');
const { default: Client, CommitmentLevel } = require('@triton-one/yellowstone-grpc');
const { decodeTx, decodeDeshred, PUMP } = require('./pumpdecode');

const ENDPOINT = process.env.GRPC_ENDPOINT || 'https://riptide.rpcpool.com:443';
const TOKEN = process.env.TRITON_X_TOKEN;
const OUT = process.env.REC_DIR || path.join(__dirname, '..', 'data', 'rec');
fs.mkdirSync(OUT, { recursive: true });

const now = () => performance.timeOrigin + performance.now();

class RotWriter {
  constructor(kind) { this.kind = kind; this.hour = null; this.gz = null; this.n = 0; }
  write(obj) {
    const d = new Date();
    const h = d.toISOString().slice(0, 13).replace(/[-T]/g, '');
    if (h !== this.hour) {
      if (this.gz) this.gz.end();
      this.hour = h;
      const f = path.join(OUT, `${this.kind}-${h.slice(0, 8)}-${h.slice(8, 10)}.jsonl.gz`);
      this.gz = zlib.createGzip({ level: 4 });
      this.gz.pipe(fs.createWriteStream(f, { flags: 'a' }));
    }
    this.gz.write(JSON.stringify(obj) + '\n');
    this.n++;
  }
  close() { if (this.gz) this.gz.end(); }
}
const W = { proc: new RotWriter('proc'), deshred: new RotWriter('deshred'), slots: new RotWriter('slots') };

let lastSlot = 0;
const baseReq = () => ({
  accounts: {}, slots: { s: { filterByCommitment: false, interslotUpdates: true } },
  transactions: { pump: { vote: false, accountInclude: [PUMP], accountExclude: [], accountRequired: [] } },
  transactionsStatus: {}, entry: {}, blocks: {}, blocksMeta: {}, accountsDataSlice: [], commitment: CommitmentLevel.PROCESSED,
});
const deshredReq = () => ({ deshredTransactions: { pump: { vote: false, accountInclude: [PUMP], accountExclude: [], accountRequired: [] } }, slots: {} });
const stats = { proc: 0, deshred: 0, slots: 0, errs: 0, reconnects: 0 };

async function runMain() {
  for (let attempt = 0; ; attempt++) {
    try {
      const c = new Client(ENDPOINT, TOKEN, { 'grpc.max_receive_message_length': 64 * 1024 * 1024 });
      await c.connect();
      const st = await c.subscribe();
      const done = new Promise((resolve) => { st.on('error', (e) => { stats.errs++; console.error('main stream error', e.message); resolve(); }); st.on('end', resolve); st.on('close', resolve); });
      st.on('data', (u) => {
        const t = now();
        try {
          if (u.transaction) {
            const rec = decodeTx(u.transaction.transaction, u.transaction.slot);
            rec.t = t;
            W.proc.write(rec);
            stats.proc++;
            if (rec.slot > lastSlot) lastSlot = rec.slot;
          } else if (u.slot) {
            W.slots.write({ t, slot: Number(u.slot.slot), st: u.slot.status, parent: u.slot.parent !== undefined ? Number(u.slot.parent) : undefined });
            stats.slots++;
          } else if (u.ping) {
            st.write(Object.assign({}, baseReq(), { ping: { id: 1 } }), () => { });
          }
        } catch (e) { stats.errs++; }
      });
      const req = baseReq();
      if (lastSlot) req.fromSlot = String(lastSlot);
      await new Promise((res, rej) => st.write(req, (e) => (e ? rej(e) : res())));
      const pinger = setInterval(() => { try { st.write(Object.assign({}, baseReq(), { ping: { id: 1 } }), () => { }); } catch (_) { } }, 10000);
      attempt = 0;
      await done;
      clearInterval(pinger);
    } catch (e) { console.error('main connect error', e.message); }
    stats.reconnects++;
    await new Promise((r) => setTimeout(r, Math.min(30000, 1000 * 2 ** Math.min(attempt, 5))));
  }
}

async function runDeshred() {
  for (let attempt = 0; ; attempt++) {
    try {
      const c = new Client(ENDPOINT, TOKEN, { 'grpc.max_receive_message_length': 64 * 1024 * 1024 });
      await c.connect();
      const ds = await c.subscribeDeshred();
      const done = new Promise((resolve) => { ds.on('error', (e) => { stats.errs++; console.error('deshred stream error', e.message); resolve(); }); ds.on('end', resolve); ds.on('close', resolve); });
      ds.on('data', (u) => {
        const t = now();
        try {
          if (u.deshredTransaction) {
            const d = u.deshredTransaction;
            const rec = decodeDeshred(d.transaction, d.slot);
            rec.t = t;
            W.deshred.write(rec);
            stats.deshred++;
          } else if (u.ping) {
            ds.write(Object.assign({}, deshredReq(), { ping: { id: 1 } }), () => { });
          }
        } catch (e) { stats.errs++; }
      });
      await new Promise((res, rej) => ds.write(deshredReq(), (e) => (e ? rej(e) : res())));
      const pinger = setInterval(() => { try { ds.write(Object.assign({}, deshredReq(), { ping: { id: 1 } }), () => { }); } catch (_) { } }, 10000);
      attempt = 0;
      await done;
      clearInterval(pinger);
    } catch (e) { console.error('deshred connect error', e.message); }
    stats.reconnects++;
    await new Promise((r) => setTimeout(r, Math.min(30000, 1000 * 2 ** Math.min(attempt, 5))));
  }
}

setInterval(() => {
  console.log(new Date().toISOString(), JSON.stringify(stats), 'lastSlot', lastSlot);
}, 60000);
process.on('SIGINT', () => { Object.values(W).forEach((w) => w.close()); setTimeout(() => process.exit(0), 500); });
process.on('SIGTERM', () => { Object.values(W).forEach((w) => w.close()); setTimeout(() => process.exit(0), 500); });

runMain();
runDeshred();
