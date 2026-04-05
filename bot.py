# Solana Memecoin Sniper Bot - LIVE MODE READY

import asyncio
import aiohttp
import time
import json
import logging
import sys
import signal
import statistics
import os
import base64
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import deque, defaultdict

# Load .env file (WALLET_PRIVATE_KEY, HELIUS_RPC_URL)

try:
from dotenv import load_dotenv
load_dotenv()
except ImportError:
pass # set env vars manually if dotenv not installed

# Solana wallet signing requires: pip install solders

try:
from solders.keypair import Keypair
from solders.pubkey import Pubkey
SOLDERS_OK = True
except ImportError:
SOLDERS_OK = False
Keypair = None
Pubkey = None

#

# LIVE MODE CONFIG read from .env

#

LIVE_MODE = os.getenv(‘LIVE_MODE’, ‘false’).lower() == ‘true’
RPC_URL = os.getenv(‘HELIUS_RPC_URL’, ‘https://api.mainnet-beta.solana.com’)
WALLET_KEY = os.getenv(‘WALLET_PRIVATE_KEY’, ‘’) # base58 private key

# WSOL mint (wrapped SOL) used as input token for Jupiter swaps

WSOL_MINT = “So11111111111111111111111111111111111111112”

# Jupiter v6 quote + swap API

JUPITER_QUOTE = “https://quote-api.jup.ag/v6/quote”
JUPITER_SWAP = “https://quote-api.jup.ag/v6/swap”

#

# CONFIG tune these to adjust behavior

#

CFG = {
# Capital in LIVE_MODE this is auto-set from your real wallet SOL balance
“capital”: 1.0, # USD (overridden live by wallet balance)
“risk_per_trade”: 0.15, # 15% per trade
“max_open”: 5, # Max concurrent positions

```
# Entry
"min_score": 65, # Dynamic, adjusts automatically
"min_liquidity": 5_000, # USD
"min_volume_5m": 500, # USD
"min_tx_5m": 5, # Min tx in 5m (instant dead-token filter)
"min_buy_ratio": 0.45, # Must have 45%+ buys to even score
"age_sweet_spot_min": 0.5, # 30s min age (avoid instant rugs)
"age_sweet_spot_max": 60.0, # 60min max for "new launch" bonus

# Exit tiered take profit (scale out instead of all-at-once)
"take_profit_t1": 0.12, # Scale out 40% of position at 12%
"take_profit_t2": 0.20, # Scale out another 40% at 20%
"take_profit_t3": 0.35, # Exit remaining 20% at 35%
"stop_loss": 0.10, # 10%
"trailing_stop": 0.06, # 6% from peak (tighter than before)
"timeout_secs": 75, # Faster timeout: 75s flat
"min_move_pct": 0.03, # "flat" = less than 3% move

# Risk
"daily_loss_limit": 0.05, # Halt if down 5% on day
"cooldown_secs": 15, # After a loss (faster recovery)
"slippage": 0.015, # 1.5% simulated slippage

# Speed
"scan_interval": 1.0, # Faster: every 1s
"exit_check_interval": 0.25, # 4x per second exit checks
"score_history": 40, # More history = smarter signals
"status_interval": 10, # Seconds between status prints

# Thresholds
"liq_pull_pct": -0.12, # Tighter liq pull trigger
"sell_pressure_max": 0.32, # Buy ratio floor
"smart_wallet_min_wr": 0.65, # Min win rate to be "smart"
```

}

#

# LOGGING

#

log = logging.getLogger(“Sniper”)
log.setLevel(logging.INFO)
fmt = logging.Formatter(”%(asctime)s.%(msecs)03d %(message)s”, “%H:%M:%S”)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
fh = logging.FileHandler(“sim_trades.log”)
fh.setFormatter(fmt)
log.addHandler(sh)
log.addHandler(fh)

#

# WALLET MANAGER loads keypair, tracks real SOL balance

#

class WalletManager:
def **init**(self):
self.keypair = None
self.pub = None
self.balance = 0.0 # SOL balance
self.sol_price= 0.0 # USD per SOL (fetched at startup)

```
if not LIVE_MODE:
return

if not SOLDERS_OK:
print("\n solders not installed. Run: pip install solders")
sys.exit(1)

if not WALLET_KEY:
print("\n WALLET_PRIVATE_KEY not set in .env file")
sys.exit(1)

try:
import base58
raw = base58.b58decode(WALLET_KEY)
self.keypair = Keypair.from_bytes(raw)
self.pub = self.keypair.pubkey()
log.info(f" Wallet loaded: {str(self.pub)[:8]}...{str(self.pub)[-4:]}")
except Exception as e:
print(f"\n Failed to load wallet: {e}")
print(" Make sure WALLET_PRIVATE_KEY is the base58 private key from Phantom.")
sys.exit(1)

async def fetch_sol_balance(self, session: aiohttp.ClientSession) -> float:
"""Fetch real SOL balance from RPC."""
if not self.pub:
return 0.0
try:
payload = {
"jsonrpc": "2.0", "id": 1,
"method": "getBalance",
"params": [str(self.pub), {"commitment": "confirmed"}]
}
async with session.post(RPC_URL, json=payload,
timeout=aiohttp.ClientTimeout(total=5)) as r:
data = await r.json()
lamports = data.get("result", {}).get("value", 0)
self.balance = lamports / 1_000_000_000 # lamports SOL
return self.balance
except Exception as e:
log.warning(f"Balance fetch failed: {e}")
return self.balance

async def fetch_sol_price(self, session: aiohttp.ClientSession) -> float:
"""Fetch SOL/USD price from Jupiter pricing API."""
try:
url = f"https://price.jup.ag/v6/price?ids={WSOL_MINT}"
async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
data = await r.json()
self.sol_price = data["data"][WSOL_MINT]["price"]
return self.sol_price
except Exception:
return self.sol_price or 150.0 # fallback

def balance_usd(self) -> float:
return self.balance * self.sol_price

def usd_to_lamports(self, usd: float) -> int:
if self.sol_price <= 0:
return 0
sol = usd / self.sol_price
return int(sol * 1_000_000_000)
```

#

# JUPITER EXECUTOR real swap via Jupiter v6 API

#

class JupiterExecutor:
“””
Executes real token swaps via Jupiter aggregator.
Only active when LIVE_MODE=true.
All trades are capped at 15% of wallet balance = micro amounts.
“””
def **init**(self, wallet: WalletManager, session: aiohttp.ClientSession):
self.wallet = wallet
self.session = session
# Track token mints we currently hold: addr -> lamports received
self.holdings: Dict[str, int] = {}

```
async def buy(self, snap: "Snap", size_usd: float) -> Optional[str]:
"""
Buy `size_usd` worth of token at snap.addr using SOL.
Returns tx signature on success, None on failure.
"""
if not LIVE_MODE:
return "SIM_TX"

lamports = self.wallet.usd_to_lamports(size_usd)
if lamports < 1000:
log.warning(f" Position too small: {lamports} lamports")
return None

# Hard safety cap: never spend more than 15% of balance in lamports
max_lamports = int(self.wallet.balance * 1_000_000_000 * CFG["risk_per_trade"])
lamports = min(lamports, max_lamports)

# 1. Get quote: SOL token
quote = await self._get_quote(WSOL_MINT, snap.addr, lamports, slippage_bps=150)
if not quote:
return None

# Record expected output for sell reference
out_amount = int(quote.get("outAmount", 0))
self.holdings[snap.addr] = out_amount

# 2. Get swap transaction
tx_b64 = await self._get_swap_tx(quote)
if not tx_b64:
return None

# 3. Sign and send
sig = await self._sign_and_send(tx_b64)
if sig:
log.info(f" TX: https://solscan.io/tx/{sig}")
return sig

async def sell(self, snap: "Snap", fraction: float = 1.0) -> Optional[str]:
"""
Sell `fraction` of held token back to SOL.
fraction=1.0 = full exit, 0.4 = partial (tiered TP)
"""
if not LIVE_MODE:
return "SIM_TX"

held = self.holdings.get(snap.addr, 0)
if held <= 0:
return None

sell_amount = int(held * fraction)
if sell_amount < 100:
return None

# 1. Get quote: token SOL
quote = await self._get_quote(snap.addr, WSOL_MINT, sell_amount, slippage_bps=200)
if not quote:
return None

# Update remaining holdings
self.holdings[snap.addr] = held - sell_amount
if fraction >= 0.99:
self.holdings.pop(snap.addr, None)

# 2. Swap tx + sign + send
tx_b64 = await self._get_swap_tx(quote)
if not tx_b64:
return None

sig = await self._sign_and_send(tx_b64)
if sig:
log.info(f" TX: https://solscan.io/tx/{sig}")
return sig

async def _get_quote(self, input_mint: str, output_mint: str,
amount: int, slippage_bps: int = 150) -> Optional[dict]:
params = {
"inputMint": input_mint,
"outputMint": output_mint,
"amount": str(amount),
"slippageBps": str(slippage_bps),
"onlyDirectRoutes": "false",
"asLegacyTransaction": "false",
}
try:
async with self.session.get(JUPITER_QUOTE, params=params,
timeout=aiohttp.ClientTimeout(total=4)) as r:
if r.status == 200:
return await r.json()
log.warning(f" Quote failed: {r.status}")
except Exception as e:
log.warning(f" Quote error: {e}")
return None

async def _get_swap_tx(self, quote: dict) -> Optional[str]:
payload = {
"quoteResponse": quote,
"userPublicKey": str(self.wallet.pub),
"wrapAndUnwrapSol": True,
"computeUnitPriceMicroLamports": 100_000, # priority fee for speed
"asLegacyTransaction": False,
}
try:
async with self.session.post(JUPITER_SWAP, json=payload,
timeout=aiohttp.ClientTimeout(total=5)) as r:
if r.status == 200:
data = await r.json()
return data.get("swapTransaction")
log.warning(f" Swap tx failed: {r.status} {await r.text()}")
except Exception as e:
log.warning(f" Swap tx error: {e}")
return None

async def _sign_and_send(self, tx_b64: str) -> Optional[str]:
"""Deserialize versioned tx, sign with keypair, send via RPC."""
try:
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned

tx_bytes = base64.b64decode(tx_b64)
tx = VersionedTransaction.from_bytes(tx_bytes)

# Sign
msg_bytes = to_bytes_versioned(tx.message)
sig = self.wallet.keypair.sign_message(msg_bytes)
signed_tx = VersionedTransaction.populate(tx.message, [sig])
signed_b64= base64.b64encode(bytes(signed_tx)).decode()

# Send
payload = {
"jsonrpc": "2.0", "id": 1,
"method": "sendTransaction",
"params": [
signed_b64,
{
"encoding": "base64",
"skipPreflight": True, # faster
"preflightCommitment": "confirmed",
"maxRetries": 3,
}
]
}
async with self.session.post(RPC_URL, json=payload,
timeout=aiohttp.ClientTimeout(total=6)) as r:
data = await r.json()
if "result" in data:
return data["result"] # tx signature
log.warning(f" Send failed: {data.get('error')}")
except Exception as e:
log.warning(f" Sign/send error: {e}")
return None
```

#

# DATA STRUCTURES

#

@dataclass
class Snap:
addr: str
symbol: str
price: float
liq: float
vol5m: float
vol1h: float
buys5m: int
sells5m: int
age_min: float
mc: float
lp_locked: bool = False
mint_ok: bool = False
top10_pct: float = 50.0
ts: float = field(default_factory=time.time)

```
@property
def buy_ratio(self) -> float:
t = self.buys5m + self.sells5m
return self.buys5m / t if t else 0.5

@property
def tx5m(self) -> int:
return self.buys5m + self.sells5m
```

@dataclass
class Trade:
addr: str
symbol: str
entry: float
score: float
size: float # USD position
t_open: float
signals: Dict
peak: float = 0.0
exit_price: float = 0.0
exit_time: float = 0.0
exit_why: str = “”
pnl: float = 0.0
pnl_pct: float = 0.0
closed: bool = False
# Tiered TP tracking
t1_hit: bool = False # 12% TP: sold 40%
t2_hit: bool = False # 20% TP: sold another 40%
remaining: float = 1.0 # Fraction of original size still open

```
def __post_init__(self):
self.peak = self.entry

def partial_exit(self, price: float, fraction: float, slippage: float, label: str) -> float:
"""Scale out a fraction of position, returns realized PnL."""
eff = price * (1 - slippage)
sold_size = self.size * fraction
pnl_slice = sold_size * ((eff - self.entry) / self.entry)
self.pnl += pnl_slice
self.remaining -= fraction
self.size -= sold_size
log.info(
f" SCALE {self.symbol:<8} {label:<10} "
f"+{(eff/self.entry-1)*100:.1f}% realized=${pnl_slice:+.2f} "
f"rem={self.remaining*100:.0f}%"
)
return pnl_slice

def close(self, price: float, reason: str, slippage: float) -> float:
eff = price * (1 - slippage)
self.exit_price = eff
self.exit_time = time.time()
self.exit_why = reason
final_pnl = self.size * ((eff - self.entry) / self.entry)
self.pnl += final_pnl
self.pnl_pct = self.pnl / (self.size / self.remaining) # vs original size
self.closed = True
return self.pnl
```

#

# SHARED STATE (single object passed everywhere)

#

class State:
def **init**(self, wallet: “WalletManager” = None):
# In live mode, capital = real wallet USD balance
start = wallet.balance_usd() if (wallet and LIVE_MODE and wallet.balance_usd() > 0) else CFG[“capital”]
self.capital = start
self.day_capital = start
self.open: Dict[str, Trade] = {}
self.closed: List[Trade] = []
self.blacklist: set = set()
self.last_loss_ts: float = 0.0
self.halted: bool = False
self.threshold: float = CFG[“min_score”]
self.recent_pnl: deque = deque(maxlen=20)
# Per-token ring buffers for speed
self.prices: Dict[str, deque] = defaultdict(lambda: deque(maxlen=CFG[“score_history”]))
self.vols: Dict[str, deque] = defaultdict(lambda: deque(maxlen=CFG[“score_history”]))
self.liqs: Dict[str, deque] = defaultdict(lambda: deque(maxlen=CFG[“score_history”]))
self.txs: Dict[str, deque] = defaultdict(lambda: deque(maxlen=CFG[“score_history”]))
# Smart wallet registry
self.smart_wallets: Dict[str, float] = {} # addr -> win_rate
self.token_wallets: Dict[str, set] = defaultdict(set)

```
# risk checks (all O(1))
def can_enter(self, addr: str) -> Tuple[bool, str]:
if self.halted: return False, "HALTED"
if addr in self.blacklist: return False, "BLACKLISTED"
if addr in self.open: return False, "ALREADY_OPEN"
if len(self.open) >= CFG["max_open"]: return False, "MAX_OPEN"
if time.time() - self.last_loss_ts < CFG["cooldown_secs"]:
return False, "COOLDOWN"
loss = (self.capital - self.day_capital) / self.day_capital
if loss <= -CFG["daily_loss_limit"]:
self.halted = True
return False, "DAILY_LIMIT"
return True, "OK"

def record_close(self, t: Trade):
self.closed.append(t)
self.capital += t.size + t.pnl
self.recent_pnl.append(t.pnl_pct)
if t.pnl < 0:
self.last_loss_ts = time.time()
if t.exit_why in ("LIQ_PULL", "SELL_SPIKE"):
self.blacklist.add(t.addr)

def adapt_threshold(self):
if len(self.recent_pnl) < 5:
return
wr = sum(1 for p in self.recent_pnl if p > 0) / len(self.recent_pnl)
if wr > 0.70:
self.threshold = min(80, self.threshold + 1)
elif wr < 0.45:
self.threshold = min(82, self.threshold + 2)
else:
self.threshold = max(58, self.threshold - 0.5)

@property
def stats(self) -> Dict:
c = self.closed
if not c: return {}
wins = [t for t in c if t.pnl > 0]
losses = [t for t in c if t.pnl <= 0]
return {
"n": len(c),
"wr": len(wins) / len(c),
"pnl": sum(t.pnl for t in c),
"avg_w": statistics.mean(t.pnl_pct for t in wins) if wins else 0,
"avg_l": statistics.mean(t.pnl_pct for t in losses) if losses else 0,
"avg_h": statistics.mean(t.exit_time - t.t_open for t in c),
"wins": len(wins),
"losses": len(losses),
}
```

#

# SCORING pure functions, no I/O, runs in <1ms

#

def score_safety(snap: Snap) -> float:
s = 30.0
# Age: too new = rug risk, sweet spot = bonus, too old = no edge
if snap.age_min < CFG[“age_sweet_spot_min”]: s -= 12 # instant rug risk
elif snap.age_min < 5: s += 2 # very early bonus
elif snap.age_min > CFG[“age_sweet_spot_max”]: s -= 4 # old, less edge
if snap.top10_pct > 80: s -= 10
elif snap.top10_pct > 60: s -= 5
elif snap.top10_pct < 40: s += 3 # well distributed = safer
if not snap.lp_locked: s -= 4
if not snap.mint_ok: s -= 3
# MC/liq ratio: healthy = liq is at least 8% of MC
if snap.mc > 0:
ratio = snap.liq / snap.mc
if ratio >= 0.15: s += 3 # very healthy
elif ratio >= 0.08: s += 1
elif ratio < 0.04: s -= 6 # dangerously thin
return max(0, min(30, s))

def score_momentum(snap: Snap, st: State) -> float:
addr = snap.addr
prices = list(st.prices[addr])
vols = list(st.vols[addr])
txs = list(st.txs[addr])
s = 0.0

```
# Price acceleration (weighted toward most recent)
if len(prices) >= 4:
mid = len(prices) // 2
early = statistics.mean(prices[:mid]) or 1e-12
late = statistics.mean(prices[mid:])
accel = (late - early) / early
s += min(10, accel * 120)

# Volume acceleration
if len(vols) >= 4:
mid = len(vols) // 2
ev = statistics.mean(vols[:mid]) or 1
lv = statistics.mean(vols[mid:])
vacc = (lv - ev) / ev
s += min(10, vacc * 60)

# Volume/Price DIVERGENCE detector:
# Vol surging but price flat = coiling, about to move bonus
# Price surging but vol dropping = distribution penalty
if len(prices) >= 4:
p_chg = (prices[-1] - prices[0]) / (prices[0] or 1)
if vacc > 0.5 and abs(p_chg) < 0.05:
s += 4 # coiling setup
elif p_chg > 0.1 and vacc < -0.3:
s -= 5 # distribution pattern

# TX spike rate of change vs 2 periods ago
if len(txs) >= 3 and txs[-2]:
rate = txs[-1] / (txs[-2] or 1)
if rate > 2.0: s += min(8, rate * 2.5)
elif rate > 1.5: s += 3

# Buy pressure non-linear bonus
br = snap.buy_ratio
if br > 0.75: s += 10
elif br > 0.65: s += 6
elif br > 0.55: s += 3
elif br < 0.40: s -= 5

# Raw volume as signal (high absolute vol = real interest)
if snap.vol5m > 50_000: s += 4
elif snap.vol5m > 20_000: s += 2
elif snap.vol5m > 5_000: s += 1

return max(0.0, min(30.0, s))
```

def score_liquidity(snap: Snap, st: State) -> float:
s = 0.0
liqs = list(st.liqs[snap.addr])

```
# Size
if snap.liq >= 50_000: s += 10
elif snap.liq >= 20_000: s += 7
elif snap.liq >= 10_000: s += 5
elif snap.liq >= CFG["min_liquidity"]: s += 2

# Growth
if len(liqs) >= 3:
growth = (liqs[-1] - liqs[0]) / (liqs[0] or 1)
if growth > 0.1: s += min(5, growth * 25)
elif growth < -0.2: s -= 10 # pulling danger

if snap.lp_locked: s += 3
if snap.mint_ok: s += 2
return max(0.0, min(20.0, s))
```

def score_smart_money(snap: Snap, st: State) -> float:
s = 0.0
wallets = st.token_wallets.get(snap.addr, set())
overlap = wallets & st.smart_wallets.keys()
if overlap:
avg_wr = statistics.mean(st.smart_wallets[w] for w in overlap)
s += min(15.0, len(overlap) * avg_wr * 5)
# Boosted tokens have real $ behind them treat as mild smart money signal
if getattr(snap, “_boosted”, False):
s += 5.0
return min(20.0, s)

def compute_score(snap: Snap, st: State) -> Tuple[float, Dict]:
saf = score_safety(snap)
mom = score_momentum(snap, st)
liq = score_liquidity(snap, st)
smar = score_smart_money(snap, st)
total = min(100.0, max(0.0, saf + mom + liq + smar))
return total, {“safety”: saf, “momentum”: mom, “liquidity”: liq, “smart”: smar}

#

# EXIT EVALUATION runs every 0.5s per open trade

#

def check_exit(trade: Trade, snap: Snap, st: State) -> Optional[str]:
p = snap.price
pnl = (p - trade.entry) / trade.entry

```
# Update peak
if p > trade.peak:
trade.peak = p

# Tiered Take Profit (scale out)
# These return special tags handled in exit_task for partial exits
if not trade.t1_hit and pnl >= CFG["take_profit_t1"]:
return "TP_T1" # Scale out 40% at 12%
if trade.t1_hit and not trade.t2_hit and pnl >= CFG["take_profit_t2"]:
return "TP_T2" # Scale out another 40% at 20%
if trade.t2_hit and pnl >= CFG["take_profit_t3"]:
return "TP_T3" # Exit remaining 20% at 35%

# Full Exits
if pnl <= -CFG["stop_loss"]: return "STOP_LOSS"

# Trailing stop (only after 5% gain)
if trade.peak > trade.entry * 1.05:
if p <= trade.peak * (1 - CFG["trailing_stop"]): return "TRAIL_STOP"

# Timeout
elapsed = time.time() - trade.t_open
if elapsed > CFG["timeout_secs"] and abs(pnl) < CFG["min_move_pct"]:
return "TIMEOUT"

# Liquidity pull
liqs = list(st.liqs[snap.addr])
if len(liqs) >= 3 and liqs[0]:
if (liqs[-1] - liqs[0]) / liqs[0] < CFG["liq_pull_pct"]: return "LIQ_PULL"

# Sell spike
if snap.buy_ratio < CFG["sell_pressure_max"] and snap.tx5m > 8:
return "SELL_SPIKE"

# Volume collapse
if snap.vol5m < 100 and elapsed > 30:
return "VOL_COLLAPSE"

return None
```

#

# DATA FETCHER (async, parallel)

#

async def fetch_pairs(session: aiohttp.ClientSession) -> List[Snap]:
“”“Fetch new + trending pairs in parallel, return parsed Snaps.”””
now_ms = time.time() * 1000

```
async def get(url):
try:
async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as r:
if r.status == 200:
return await r.json(content_type=None)
except Exception:
pass
return {}

# Fire THREE requests simultaneously for maximum coverage
new_url = "https://api.dexscreener.com/latest/dex/search?q=solana"
trend_url = "https://api.dexscreener.com/latest/dex/tokens/solana"
boost_url = "https://api.dexscreener.com/token-boosts/latest/v1" # boosted/trending
raw_new, raw_trend, raw_boost = await asyncio.gather(
get(new_url), get(trend_url), get(boost_url)
)

# Extract boosted token addresses for a quick lookup
boosted_addrs = set()
if isinstance(raw_boost, list):
for b in raw_boost:
a = b.get("tokenAddress", "")
if a: boosted_addrs.add(a)

seen = set()
snaps = []

for dataset in (raw_new, raw_trend):
for pair in dataset.get("pairs", [])[:80]:
if pair.get("chainId") != "solana":
continue
base = pair.get("baseToken", {})
addr = base.get("address", "")
if not addr or addr in seen:
continue
seen.add(addr)

try:
liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
vol5m = float(pair.get("volume", {}).get("m5", 0) or 0)
vol1h = float(pair.get("volume", {}).get("h1", 0) or 0)
price = float(pair.get("priceUsd", 0) or 0)
mc = float(pair.get("marketCap", 0) or 0)
tx5m = pair.get("txns", {}).get("m5", {})
buys = int(tx5m.get("buys", 0) or 0)
sells = int(tx5m.get("sells", 0) or 0)
created = pair.get("pairCreatedAt", 0) or 0
age = (time.time() * 1000 - created) / 60_000 if created else 999

if price <= 0 or liq < CFG["min_liquidity"]:
continue

snap = Snap(
addr=addr, symbol=base.get("symbol", "???"),
price=price, liq=liq, vol5m=vol5m, vol1h=vol1h,
buys5m=buys, sells5m=sells, age_min=age, mc=mc,
)
# Boost flag: tokens with active promotions get smart_money bonus
snap._boosted = addr in boosted_addrs
snaps.append(snap)
except Exception:
continue

return snaps
```

#

# CORE TASKS

#

async def scanner_task(session: aiohttp.ClientSession, queue: asyncio.Queue, st: State):
“”“Continuously fetch market data and push snapshots into queue.”””
log.info(” Scanner started”)
while True:
t0 = time.time()
snaps = await fetch_pairs(session)
for snap in snaps:
# Update ring buffers immediately (O(1))
st.prices[snap.addr].append(snap.price)
st.vols [snap.addr].append(snap.vol5m)
st.liqs [snap.addr].append(snap.liq)
st.txs [snap.addr].append(snap.tx5m)
await queue.put(snap)

```
elapsed = time.time() - t0
sleep = max(0, CFG["scan_interval"] - elapsed)
await asyncio.sleep(sleep)
```

async def decision_task(queue: asyncio.Queue, st: State, executor: “JupiterExecutor” = None):
“”“Drain queue, score each token, enter if criteria met.”””
log.info(” Decision engine started”)
slippage = CFG[“slippage”]

```
while True:
snap: Snap = await queue.get()

# Skip if already tracking as open trade (exit_task handles those)
if snap.addr in st.open:
queue.task_done()
continue

# Fast pre-filter before full scoring (saves CPU on obvious rejects)
if snap.vol5m < CFG["min_volume_5m"]: queue.task_done(); continue
if snap.liq < CFG["min_liquidity"]: queue.task_done(); continue
if snap.tx5m < CFG["min_tx_5m"]: queue.task_done(); continue
if snap.buy_ratio < CFG["min_buy_ratio"]: queue.task_done(); continue
if snap.age_min < CFG["age_sweet_spot_min"]: queue.task_done(); continue

# Score (pure computation, <1ms)
score, signals = compute_score(snap, st)

if score < st.threshold:
queue.task_done()
continue

# Risk check (all O(1) dict lookups)
ok, reason = st.can_enter(snap.addr)
if not ok:
queue.task_done()
continue

# Position size = 15% of current capital
size = st.capital * CFG["risk_per_trade"]
e_price= snap.price * (1 + slippage)

# LIVE: execute real buy via Jupiter
tx_sig = None
if LIVE_MODE and executor:
log.info(f" Sending BUY for {snap.symbol} ${size:.4f}...")
tx_sig = await executor.buy(snap, size)
if not tx_sig:
log.warning(f" Buy failed for {snap.symbol} skipping")
queue.task_done()
continue

trade = Trade(
addr=snap.addr, symbol=snap.symbol,
entry=e_price, score=score, size=size,
t_open=time.time(), signals=signals,
)
st.open[snap.addr] = trade
st.capital -= size

mode_tag = f"[LIVE tx={tx_sig[:8]}]" if tx_sig and tx_sig != "SIM_TX" else "[SIM]"
log.info(
f" ENTER {snap.symbol:<8} score={score:.0f} "
f"saf={signals['safety']:.0f} mom={signals['momentum']:.0f} "
f"liq={signals['liquidity']:.0f} smt={signals['smart']:.0f} "
f"${e_price:.8f} pos=${size:.4f} {mode_tag}"
)
queue.task_done()
```

async def exit_task(queue: asyncio.Queue, st: State, executor: “JupiterExecutor” = None):
“”“Poll open trades against latest snapshots for exit signals.”””
log.info(” Exit watcher started”)
slippage = CFG[“slippage”]
# Build a fast addrsnap cache from the queue without consuming it
snap_cache: Dict[str, Snap] = {}

```
async def refresh_cache():
"""Non-blocking drain of queue into local cache."""
while True:
try:
snap = queue.get_nowait()
snap_cache[snap.addr] = snap
queue.task_done()
except asyncio.QueueEmpty:
break

while True:
await asyncio.sleep(CFG["exit_check_interval"])
await refresh_cache()

for addr in list(st.open.keys()):
trade = st.open.get(addr)
if not trade:
continue
snap = snap_cache.get(addr)
if snap is None:
continue

why = check_exit(trade, snap, st)
if why:
# Partial exits (tiered TP)
if why == "TP_T1":
if LIVE_MODE and executor:
await executor.sell(snap, fraction=0.40)
trade.partial_exit(snap.price, 0.40, slippage, "T1@12%")
trade.t1_hit = True
continue
if why == "TP_T2":
if LIVE_MODE and executor:
await executor.sell(snap, fraction=0.40)
trade.partial_exit(snap.price, 0.40, slippage, "T2@20%")
trade.t2_hit = True
continue
# Full close
if LIVE_MODE and executor:
await executor.sell(snap, fraction=1.0)
pnl = trade.close(snap.price, why, slippage)
del st.open[addr]
st.record_close(trade)
st.adapt_threshold()
emoji = "" if pnl >= 0 else ""
held = int(trade.exit_time - trade.t_open)
log.info(
f" EXIT {trade.symbol:<8} {why:<18} "
f"total={trade.pnl_pct*100:+.1f}% ${pnl:+.2f} "
f"held={held}s cap=${st.capital:.2f} {emoji}"
)
```

async def status_task(st: State):
“”“Print periodic performance summary.”””
await asyncio.sleep(CFG[“status_interval”])
while True:
s = st.stats
open_syms = [t.symbol for t in st.open.values()]
if s:
log.info(
f” Trades={s[‘n’]} WR={s[‘wr’]*100:.0f}% “
f”PnL=${s[‘pnl’]:+.2f} AvgWin={s[‘avg_w’]*100:+.1f}% “
f”AvgLoss={s[‘avg_l’]*100:+.1f}% AvgHold={s[‘avg_h’]:.0f}s “
f”Threshold={st.threshold:.0f} Open={open_syms or ‘none’} “
f”Cap=${st.capital:.2f}”
)
else:
log.info(f” Scanning… Open={open_syms or ‘none’} Cap=${st.capital:.2f} Threshold={st.threshold:.0f}”)
await asyncio.sleep(CFG[“status_interval”])

def print_final_report(st: State):
s = st.stats
print(”\n” + “”*64)
print(” FINAL SIMULATION REPORT”)
print(””*64)
print(f” Starting Capital : ${CFG[‘capital’]:.2f}”)
print(f” Final Capital : ${st.capital:.2f}”)
net = st.capital - CFG[“capital”]
print(f” Net P&L : ${net:+.2f} ({net/CFG[‘capital’]*100:+.1f}%)”)
if s:
print(f” Total Trades : {s[‘n’]} ({s[‘wins’]}W / {s[‘losses’]}L)”)
print(f” Win Rate : {s[‘wr’]*100:.1f}%”)
print(f” Avg Win : {s[‘avg_w’]*100:+.1f}%”)
print(f” Avg Loss : {s[‘avg_l’]*100:+.1f}%”)
print(f” Avg Hold Time : {s[‘avg_h’]:.0f}s”)
print(f” Final Threshold : {st.threshold:.1f}”)
print(f” Blacklisted : {len(st.blacklist)} tokens”)
from collections import Counter
reasons = Counter(t.exit_why for t in st.closed)
print(”\n Exit Breakdown:”)
for r, n in reasons.most_common():
print(f” {r:<22} {n:>3}”)
print(””*64 + “\n”)

#

# ENTRYPOINT

#

async def main():
connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
async with aiohttp.ClientSession(connector=connector) as session:

```
# Boot wallet
wallet = WalletManager()
executor = None

if LIVE_MODE:
sol_price = await wallet.fetch_sol_price(session)
sol_bal = await wallet.fetch_sol_balance(session)
print("")
print(" SOLANA MEMECOIN SNIPER LIVE MODE ")
print(" Real transactions on Solana mainnet ")
print("")
print(f" Wallet : {str(wallet.pub)[:8]}...{str(wallet.pub)[-4:]}")
print(f" SOL Balance : {sol_bal:.6f} SOL (${wallet.balance_usd():.4f})")
print(f" SOL Price : ${sol_price:.2f}")
if sol_bal < 0.005:
print("\n WARNING: Very low SOL balance. You need at least 0.005 SOL for gas.")
executor = JupiterExecutor(wallet, session)
else:
print("")
print(" SOLANA MEMECOIN SNIPER SIMULATION MODE ")
print(" Paper trading only. Set LIVE_MODE=true to go live. ")
print("")

st = State(wallet)
print(f" Capital : ${st.capital:.4f}")
print(f" Risk/Trade : {CFG['risk_per_trade']*100:.0f}% (${st.capital*CFG['risk_per_trade']:.4f} per trade)")
print(f" TP Tiers : T1={CFG['take_profit_t1']*100:.0f}%(40%) T2={CFG['take_profit_t2']*100:.0f}%(40%) T3={CFG['take_profit_t3']*100:.0f}%(20%)")
print(f" SL / Trail : {CFG['stop_loss']*100:.0f}% / {CFG['trailing_stop']*100:.0f}% from peak")
print(f" Scan speed : every {CFG['scan_interval']}s (3 sources parallel)")
print(f" Exit check : every {CFG['exit_check_interval']}s (4/sec)")
print(f" Timeout : {CFG['timeout_secs']}s flat")
print(f" Min score : {CFG['min_score']} (adaptive)")
print()

queue = asyncio.Queue(maxsize=2000)

# All engines run concurrently
tasks = [
asyncio.create_task(scanner_task(session, queue, st), name="scanner"),
asyncio.create_task(decision_task(queue, st, executor), name="decision"),
asyncio.create_task(exit_task(queue, st, executor), name="exit"),
asyncio.create_task(status_task(st), name="status"),
]

def _shutdown(sig, frame):
log.info("\n Shutting down...")
for t in tasks: t.cancel()

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

try:
await asyncio.gather(*tasks)
except asyncio.CancelledError:
pass

print_final_report(st)
```

if **name** == “**main**”:
asyncio.run(main())