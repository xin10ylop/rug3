"""Decode pump.fun transactions (Triton getTransactionsForAddress / getTransaction JSON, encoding=json).

Extracts, per transaction:
  - tx context: slot, transactionIndex, blockTime, signer(s), fee, CU consumed, CU price/limit, tips
  - pump.fun instruction names (top-level and CPI)
  - events: CreateEvent, TradeEvent, CompleteEvent (decoded from the program's self-CPI event ixs,
    falling back to "Program data:" logs)

Layouts come from the official IDL (github.com/pump-fun/pump-public-docs idl/pump.json).
"""
from __future__ import annotations

import base64
import struct
from dataclasses import dataclass, field
from typing import Any

import base58

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMP_FEES = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
SYSTEM = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL = "So11111111111111111111111111111111111111112"

EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")  # anchor self-CPI event tag

IX_DISC = {
    bytes.fromhex("66063d1201daebea"): "buy",
    bytes.fromhex("38fc74089edfcd5f"): "buy_exact_sol_in",
    bytes.fromhex("b817ee6167c5d33d"): "buy_v2",
    bytes.fromhex("07051dc4f5176550"): "buy_v3",
    bytes.fromhex("c2ab1c46684d5b2f"): "buy_exact_quote_in_v2",
    bytes.fromhex("e1f7501ed5b38488"): "buy_exact_quote_in_v3",
    bytes.fromhex("33e685a4017f83ad"): "sell",
    bytes.fromhex("5df6823ce7e940b2"): "sell_v2",
    bytes.fromhex("1c92de7726c469d5"): "sell_v3",
    bytes.fromhex("181ec828051c0777"): "create",
    bytes.fromhex("d6904cec5f8b31b4"): "create_v2",
    bytes.fromhex("e19a7516d756f667"): "multi_hop_curve_swap",
    bytes.fromhex("9beae792ec9ea21e"): "migrate",
    bytes.fromhex("bbcb121fceedfe29"): "migrate_v2",
    bytes.fromhex("1416567bc61cdb84"): "collect_creator_fee",
    bytes.fromhex("cf118af204221338"): "collect_creator_fee_v2",
    bytes.fromhex("ea66c2cb96483ee5"): "extend_account",
}

EV_CREATE = bytes.fromhex("1b72a94ddeeb6376")
EV_TRADE = bytes.fromhex("bddb7fd34ee661ee")
EV_COMPLETE = bytes.fromhex("5f72619cd42e9808")

# Known tip accounts of transaction-landing services (partial list; unknown small
# SOL transfers in a trade tx are reported separately as "other_transfers").
TIP_ACCOUNTS: dict[str, str] = {}
for a in [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]:
    TIP_ACCOUNTS[a] = "jito"
for a in [
    "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE", "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
    "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta", "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn",
    "2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD", "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ",
    "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF", "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
    "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey", "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or",
]:
    TIP_ACCOUNTS[a] = "helius"
for a in [
    "TEMPaMeCRFAS9EKF53Jd6KpHxgL47uWLcpFArU1Fanq", "noz3jAjPiHuBPqiSPkkugaJDkJscPuRhYnSpbi8UvC4",
    "noz3str9KXfpKknefHji8L1mPgimezaiUyCHYMDv1GE", "noz6uoYCDijhu1V7cutCpwxNiSovEwLdRHPwmgCGDNo",
    "noz9EPNcT7WH6Sou3sr3GGjHQYVkN3DNirpbvDkv9YJ", "nozc5yT15LazbLTFVZzoNZCwjh3yUtW86LoUyqsBu4L",
    "nozFrhfnNGoyqwVuwPAW4aaGqempx4PU6g6D9CJMv7Z", "nozievPk7HyK1Rqy1MPJwVQ7qQg2QoJGyP71oeDwbsu",
    "noznbgwYnBLDHu8wcQVCEw6kDrXkPdKkydGJGNXGvL7", "nozNVWs5N8mgzuD3qigrCG2UoKxZttxzZ85pvAQVrbP",
    "nozpEGbwx4BcGp6pvEdAh1JoC2CQGZdU6HbNP1v2p6P", "nozrhjhkCr3zXT3BiT4WCodYCUFeQvcdUkM7MqhKqge",
    "nozrwQtWhEdrA6W8dkbt9gnUaMs52PdAv5byipnadq3", "nozUacTVWub3cL4mJmGCYjKZTnE9RbdY5AP46iQgbPJ",
    "nozWCyTPppJjRuw2fpzDhhWbW355fzosWSzrrMYB1Qk", "nozWNju6dY353eMkMqURqwQEoM3SFgEKC6psLCSfUne",
    "nozxNBgWohjR75vdspfxR5H9ceC7XXH99xpxhVGt3Bb",
]:
    TIP_ACCOUNTS[a] = "nozomi"
for a in ["HWEoBxYs7ssKuudEjzjmpfJVX7Dvi7wescFsVx2L5yoY", "95cfoy472fcQHaw4tPGBTKpn6ZQnfEPfBgDQx6gcRmRg"]:
    TIP_ACCOUNTS[a] = "bloxroute"
for a in [
    "Eb2KpSC8uMt9GmzyAEm5Eb1AAAgTjRaXWFjKyFXHZxF3", "FCjUJZ1qozm1e8romw216qyfQMaaWKxWsuySnumVCCNe",
    "ENxTEjSQ1YabmUpXAdCgevnHQ9MHdLv8tzFiuiYJqa13", "6rYLG55Q9RpsPGvqdPNJs4z5WTxJVatMB8zV3WJhs5EK",
    "Cix2bHfqPcKcM233mzxbLk14kSggUUiz2A87fJtGivXr",
]:
    TIP_ACCOUNTS[a] = "0slot"
for a in [
    "NextbLoCkVtMGcV47JzewQdvBpLqT9TxQFozQkN98pE", "NexTbLoCkWykbLuB1NkjXgFWkX9oAtcoagQegygXXA2",
    "NeXTBLoCKs9F1y5PJS9CKrFNNLU1keHW71rfh7KgA1X", "NexTBLockJYZ7QD7p2byrUa6df8ndV2WSd8GkbWqfbb",
    "neXtBLock1LeC67jYd1QdAa32kbVeubsfPNTJC1V5At", "nEXTBLockYgngeRmRrjDV31mGSekVPqZoMGhQEZtPVG",
    "NEXTbLoCkB51HpLBLojQfpyVAMorm3zzKg7w9NFdqid", "nextBLoCkPMgmG8ZgJtABeScP35qLa2AMCNKntAP7Xc",
]:
    TIP_ACCOUNTS[a] = "nextblock"
for a in [
    "astrazznxsGUhWShqgNtAdfrzP2G83DzcWVJDxwV9bF", "astra4uejePWneqNaJKuFFA8oonqCE1sqF6b45kDMZm",
    "astra9xWY93QyfG6yM8zwsKsRodscjQ2uU2HKNL5prk", "astraRVUuTHjpwEVvNBeQEgwYx9w9CFyfxjYoobCZhL",
]:
    TIP_ACCOUNTS[a] = "astralane"


class Reader:
    __slots__ = ("b", "o")

    def __init__(self, b: bytes, o: int = 0):
        self.b = b
        self.o = o

    def left(self) -> int:
        return len(self.b) - self.o

    def u8(self) -> int:
        v = self.b[self.o]
        self.o += 1
        return v

    def bool(self) -> bool:
        return self.u8() != 0

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.b, self.o)[0]
        self.o += 2
        return v

    def u64(self) -> int:
        v = struct.unpack_from("<Q", self.b, self.o)[0]
        self.o += 8
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.b, self.o)[0]
        self.o += 8
        return v

    def pk(self) -> str:
        v = self.b[self.o:self.o + 32]
        self.o += 32
        return base58.b58encode(v).decode()

    def string(self) -> str:
        n = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        v = self.b[self.o:self.o + n]
        self.o += n
        return v.decode("utf-8", "replace")


def _decode_trade(r: Reader) -> dict:
    ev = {
        "mint": r.pk(), "sol_amount": r.u64(), "token_amount": r.u64(), "is_buy": r.bool(),
        "user": r.pk(), "timestamp": r.i64(), "virtual_sol_reserves": r.u64(),
        "virtual_token_reserves": r.u64(), "real_sol_reserves": r.u64(), "real_token_reserves": r.u64(),
        "fee_recipient": r.pk(), "fee_basis_points": r.u64(), "fee": r.u64(), "creator": r.pk(),
        "creator_fee_basis_points": r.u64(), "creator_fee": r.u64(),
    }
    try:
        ev["track_volume"] = r.bool()
        ev["total_unclaimed_tokens"] = r.u64()
        ev["total_claimed_tokens"] = r.u64()
        ev["current_sol_volume"] = r.u64()
        ev["last_update_timestamp"] = r.i64()
        ev["ix_name"] = r.string()
        ev["mayhem_mode"] = r.bool()
        ev["cashback_fee_basis_points"] = r.u64()
        ev["cashback"] = r.u64()
        ev["buyback_fee_basis_points"] = r.u64()
        ev["buyback_fee"] = r.u64()
        n = struct.unpack_from("<I", r.b, r.o)[0]
        r.o += 4
        ev["shareholders"] = [(r.pk(), r.u16()) for _ in range(n)]
        ev["quote_mint"] = r.pk()
        ev["quote_amount"] = r.u64()
        ev["virtual_quote_reserves"] = r.u64()
        ev["real_quote_reserves"] = r.u64()
        ev["holder_rewards_bps"] = r.u64()
        ev["holder_rewards"] = r.u64()
        ev["creator_fee_unclaimed"] = r.u64()
    except (struct.error, IndexError):
        pass
    return ev


def _decode_create(r: Reader) -> dict:
    ev = {
        "name": r.string(), "symbol": r.string(), "uri": r.string(), "mint": r.pk(),
        "bonding_curve": r.pk(), "user": r.pk(), "creator": r.pk(), "timestamp": r.i64(),
        "virtual_token_reserves": r.u64(), "virtual_sol_reserves": r.u64(),
        "real_token_reserves": r.u64(), "token_total_supply": r.u64(),
    }
    try:
        ev["token_program"] = r.pk()
        ev["is_mayhem_mode"] = r.bool()
        ev["is_cashback_enabled"] = r.bool()
        ev["quote_mint"] = r.pk()
        ev["virtual_quote_reserves"] = r.u64()
        ev["creator_fee_bps"] = r.u64()
        ev["is_holder_reward"] = r.bool()
        ev["depth"] = r.u8()
    except (struct.error, IndexError):
        pass
    return ev


def _decode_complete(r: Reader) -> dict:
    return {"user": r.pk(), "mint": r.pk(), "bonding_curve": r.pk(), "timestamp": r.i64()}


def decode_event(data: bytes) -> tuple[str, dict] | None:
    disc = data[:8]
    r = Reader(data, 8)
    try:
        if disc == EV_TRADE:
            return "trade", _decode_trade(r)
        if disc == EV_CREATE:
            return "create", _decode_create(r)
        if disc == EV_COMPLETE:
            return "complete", _decode_complete(r)
    except (struct.error, IndexError, UnicodeDecodeError):
        return None
    return None


@dataclass
class TxInfo:
    sig: str
    slot: int
    tx_index: int | None
    block_time: int | None
    signer: str
    signers: list[str]
    ok: bool
    fee: int
    cu_consumed: int | None
    cu_price: int  # micro-lamports per CU
    cu_limit: int | None
    tips: list[tuple[str, int]]  # (service, lamports)
    pump_ixs: list[str]
    events: list[tuple[str, dict]] = field(default_factory=list)
    sys_transfers: list[tuple[str, str, int]] = field(default_factory=list)  # (from, to, lamports) top-level+inner
    version: Any = None

    @property
    def priority_fee_lamports(self) -> int:
        if not self.cu_price:
            return 0
        lim = self.cu_limit if self.cu_limit is not None else 200_000 * max(1, len(self.pump_ixs))
        return (self.cu_price * lim + 999_999) // 1_000_000

    @property
    def tip_lamports(self) -> int:
        return sum(t[1] for t in self.tips)


def _sys_transfer(data: bytes) -> int | None:
    # SystemInstruction::Transfer = 2 (u32 LE) + lamports u64
    if len(data) == 12 and data[:4] == b"\x02\x00\x00\x00":
        return struct.unpack_from("<Q", data, 4)[0]
    return None


def parse_tx(t: dict) -> TxInfo:
    """t: one element of getTransactionsForAddress(full, json) data, or a getTransaction result."""
    tx = t["transaction"]
    meta = t["meta"]
    msg = tx["message"]
    keys = list(msg["accountKeys"])
    if keys and isinstance(keys[0], dict):  # jsonParsed
        keys = [k["pubkey"] for k in keys]
    la = meta.get("loadedAddresses") or {}
    allk = keys + list(la.get("writable", [])) + list(la.get("readonly", []))
    nsig = msg["header"]["numRequiredSignatures"]

    cu_price = 0
    cu_limit = None
    tips: list[tuple[str, int]] = []
    pump_ixs: list[str] = []
    events: list[tuple[str, dict]] = []
    transfers: list[tuple[str, str, int]] = []

    def handle_ix(prog: str, accs: list[int], data: bytes, inner: bool):
        nonlocal cu_price, cu_limit
        if prog == COMPUTE_BUDGET and not inner:
            if data[:1] == b"\x03" and len(data) >= 9:
                cu_price = struct.unpack_from("<Q", data, 1)[0]
            elif data[:1] == b"\x02" and len(data) >= 5:
                cu_limit = struct.unpack_from("<I", data, 1)[0]
        elif prog == SYSTEM:
            lam = _sys_transfer(data)
            if lam is not None and len(accs) >= 2:
                frm, to = allk[accs[0]], allk[accs[1]]
                transfers.append((frm, to, lam))
                svc = TIP_ACCOUNTS.get(to)
                if svc:
                    tips.append((svc, lam))
        elif prog == PUMP:
            if inner and data[:8] == EVENT_IX_TAG:
                ev = decode_event(data[8:])
                if ev:
                    events.append(ev)
            else:
                name = IX_DISC.get(data[:8])
                pump_ixs.append(name or ("unknown:" + data[:8].hex()))

    for ix in msg["instructions"]:
        prog = allk[ix["programIdIndex"]]
        handle_ix(prog, ix["accounts"], base58.b58decode(ix["data"]), False)
    for inn in meta.get("innerInstructions") or []:
        for ix in inn["instructions"]:
            prog = allk[ix["programIdIndex"]]
            handle_ix(prog, ix["accounts"], base58.b58decode(ix["data"]), True)

    if not events:  # fall back to logs
        for line in meta.get("logMessages") or []:
            if line.startswith("Program data: "):
                try:
                    ev = decode_event(base64.b64decode(line[14:]))
                except Exception:
                    ev = None
                if ev:
                    events.append(ev)

    return TxInfo(
        sig=tx["signatures"][0], slot=t["slot"], tx_index=t.get("transactionIndex"),
        block_time=t.get("blockTime"), signer=keys[0], signers=keys[:nsig],
        ok=meta.get("err") is None, fee=meta.get("fee", 0), cu_consumed=meta.get("computeUnitsConsumed"),
        cu_price=cu_price, cu_limit=cu_limit, tips=tips, pump_ixs=pump_ixs, events=events,
        sys_transfers=transfers, version=t.get("version"),
    )


# ---------------------------------------------------------------- bonding curve math
def buy_tokens_out(vsol: int, vtok: int, sol_in_net: int) -> int:
    """Tokens out for a net (post-fee) SOL amount into the constant-product virtual curve."""
    k = vsol * vtok
    new_vsol = vsol + sol_in_net
    new_vtok = -(-k // new_vsol)  # ceil
    return vtok - new_vtok


def sell_sol_out(vsol: int, vtok: int, tok_in: int) -> int:
    """Gross SOL out (before fees) for selling tok_in tokens."""
    return (tok_in * vsol) // (vtok + tok_in)


def price_sol_per_token(vsol: int, vtok: int) -> float:
    return vsol / vtok  # lamports per raw token unit (6 decimals) == SOL per whole token * 1e3


def mcap_sol(vsol: int, vtok: int, supply: int = 1_000_000_000_000_000) -> float:
    return vsol / vtok * supply / 1e9
