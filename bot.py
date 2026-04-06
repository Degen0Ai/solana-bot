# Solana Memecoin Sniper Bot - LIVE MODE

import asyncio
import aiohttp
import time
import base64
import os
import logging
import sys
import signal
import statistics
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import deque, defaultdict

try:
from dotenv import load_dotenv
load_dotenv()
except ImportError:
pass

try:
from solders.keypair import Keypair
from solders.pubkey import Pubkey
SOLDERS_OK = True
except ImportError:
SOLDERS_OK = False
Keypair = None
Pubkey = None

LIVE_MODE = os.getenv(‘LIVE_MODE’, ‘false’).lower() == ‘true’
RPC_URL = os.getenv(‘HELIUS_RPC_URL’, ‘https://api.mainnet-beta.solana.com’)
WALLET_KEY = os.getenv(‘WALLET_PRIVATE_KEY’, ‘’)
GROQ_API_KEY = os.getenv(‘GROQ_API_KEY’, ‘’)
GROQ_URL = ‘https://api.groq.com/openai/v1/chat/completions’
GROQ_MODEL = ‘llama-3.3-70b-versatile’
WSOL_MINT = ‘So11111111111111111111111111111111111111112’
JUPITER_QUOTE= ‘https://quote-api.jup.ag/v6/quote’
JUPITER_SWAP = ‘https://quote-api.jup.ag/v6/swap’
JITO_URL = ‘https://mainnet.block-engine.jito.wtf/api/v1/transactions’
PUMP_PROGRAM = ‘6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P’
HELIUS_WS = RPC_URL.replace(‘https://’, ‘wss://’).replace(‘http://’, ‘ws://’)

CFG = {
‘capital’: 1.0,
‘risk_per_trade’: 0.20,
‘max_open’: 5,
‘min_score’: 15,
‘min_liquidity’: 500,
‘min_volume_5m’: 0,
‘min_tx_5m’: 1,
‘min_buy_ratio’: 0.20,
‘age_sweet_spot_min’:0.05,
‘age_sweet_spot_max’:120.0,
‘take_profit_t1’: 0.20,
‘take_profit_t2’: 0.35,
‘take_profit_t3’: 0.50,
‘stop_loss’: 0.22,
‘trailing_stop’: 0.12,
‘timeout_secs’: 45,
‘min_move_pct’: 0.03,
‘daily_loss_limit’: 0.05,
‘cooldown_secs’: 3,
‘slippage’: 0.015,
‘scan_interval’: 2.0,
‘exit_check_interval’:0.25,
‘score_history’: 15,
‘status_interval’: 2,
‘liq_pull_pct’: -0.05,
‘sell_pressure_max’: 0.40,
‘smart_wallet_min_wr’:0.65,
}

log = logging.getLogger(‘Sniper’)
log.setLevel(logging.INFO)
fmt = logging.Formatter(’%(asctime)s.%(msecs)03d %(message)s’, ‘%H:%M:%S’)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
fh = logging.FileHandler(‘sim_trades.log’)
fh.setFormatter(fmt)
log.addHandler(sh)
log.addHandler(fh)

NARRATIVE_KEYWORDS = [‘ai’,‘agent’,‘gpt’,‘trump’,‘elon’,‘doge’,‘pepe’,‘wojak’,
‘moon’,‘pump’,‘sol’,‘cat’,‘dog’,‘inu’,‘baby’,‘meta’,‘btc’,‘meme’,‘chad’,‘sigma’]

def has_narrative(symbol, name=’’):
text = (symbol + ’ ’ + name).lower()
for kw in NARRATIVE_KEYWORDS:
if kw in text:
return True, kw
return False, ‘’

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
_boosted: bool = False
_narrative: bool = False
_narrative_kw: str = ‘’

```
@property
def buy_ratio(self):
t = self.buys5m + self.sells5m
return self.buys5m / t if t else 0.5

@property
def tx5m(self):
return self.buys5m + self.sells5m
```

@dataclass
class Trade:
addr: str
symbol: str
entry: float
score: float
size: float
t_open: float
signals: Dict
peak: float = 0.0
exit_price: float = 0.0
exit_time: float = 0.0
exit_why: str = ‘’
pnl: float = 0.0
pnl_pct: float = 0.0
closed: bool = False
t1_hit: bool = False
t2_hit: bool = False
remaining: float = 1.0
ai_conf: float = 0.6

```
def __post_init__(self):
self.peak = self.entry

def partial_exit(self, price, fraction, slippage, label):
eff = price * (1 - slippage)
sold = self.size * fraction
pnl_slice = sold * ((eff - self.entry) / self.entry)
self.pnl += pnl_slice
self.remaining -= fraction
self.size -= sold
log.info(f' 💰 SCALE {self.symbol:<8} {label:<10} +{(eff/self.entry-1)*100:.1f}% realized=${pnl_slice:+.4f}')
return pnl_slice

def close(self, price, reason, slippage):
eff = price * (1 - slippage)
self.exit_price = eff
self.exit_time = time.time()
self.exit_why = reason
final_pnl = self.size * ((eff - self.entry) / self.entry)
self.pnl += final_pnl
self.pnl_pct = self.pnl / max(0.0001, self.size / max(0.01, self.remaining))
self.closed = True
return self.pnl
```

TRADE_TP: Dict[str, tuple] = {}

def set_trade_tp(addr, confidence):
if confidence >= 0.80:
t1,t2,t3,sl = 0.25,0.40,0.60,0.22
elif confidence >= 0.65:
t1,t2,t3,sl = 0.20,0.35,0.50,0.22
elif confidence >= 0.50:
t1,t2,t3,sl = 0.15,0.25,0.40,0.20
else:
t1,t2,t3,sl = 0.10,0.20,0.30,0.18
TRADE_TP[addr] = (t1,t2,t3,sl)

class WalletManager:
def **init**(self):
self.keypair = None
self.pub = None
self.balance = 0.0
self.sol_price= 130.0

```
if not LIVE_MODE:
return
if not SOLDERS_OK:
print('\n SOLDERS not installed. Run: pip install solders')
sys.exit(1)
if not WALLET_KEY:
print('\n WALLET_PRIVATE_KEY not set')
sys.exit(1)
try:
import base58
raw = base58.b58decode(WALLET_KEY)
self.keypair = Keypair.from_bytes(raw)
self.pub = self.keypair.pubkey()
log.info(f' Wallet: {str(self.pub)[:8]}...{str(self.pub)[-4:]}')
except Exception as e:
print(f'\n Failed to load wallet: {e}')
sys.exit(1)

async def fetch_sol_balance(self, session):
if not self.pub:
return 0.0
try:
payload = {'jsonrpc':'2.0','id':1,'method':'getBalance',
'params':[str(self.pub),{'commitment':'confirmed'}]}
async with session.post(RPC_URL, json=payload,
timeout=aiohttp.ClientTimeout(total=5)) as r:
data = await r.json()
self.balance = data.get('result',{}).get('value',0) / 1e9
return self.balance
except Exception:
return self.balance

async def fetch_sol_price(self, session):
for url in [
'https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT',
'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd',
]:
try:
async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as r:
if r.status == 200:
data = await r.json()
if 'price' in data:
self.sol_price = float(data['price'])
elif 'solana' in data:
self.sol_price = float(data['solana']['usd'])
return self.sol_price
except Exception:
continue
return self.sol_price

def balance_usd(self):
return self.balance * self.sol_price

def usd_to_lamports(self, usd):
if self.sol_price <= 0:
return 0
return int((usd / self.sol_price) * 1e9)
```

class State:
def **init**(self, wallet=None):
start = wallet.balance_usd() if (wallet and LIVE_MODE and wallet.balance_usd() > 0) else CFG[‘capital’]
self.capital = start
self.day_capital = start
self.open: Dict[str, Trade] = {}
self.closed: List[Trade] = []
self.blacklist: set = set()
self.last_loss_ts: float = 0.0
self.halted: bool = False
self.threshold: float = CFG[‘min_score’]
self.recent_pnl: deque = deque(maxlen=20)
self.prices: Dict[str,deque] = defaultdict(lambda: deque(maxlen=CFG[‘score_history’]))
self.vols: Dict[str,deque] = defaultdict(lambda: deque(maxlen=CFG[‘score_history’]))
self.liqs: Dict[str,deque] = defaultdict(lambda: deque(maxlen=CFG[‘score_history’]))
self.txs: Dict[str,deque] = defaultdict(lambda: deque(maxlen=CFG[‘score_history’]))
self.smart_wallets: Dict[str,float] = {}
self.token_wallets: Dict[str,set] = defaultdict(set)
self.kol_wallets: Dict[str,str] = {
‘5tzFkiKscfRcs9HpTTBTWH4xhKmMnmRMYbEbpJnmkLET’: ‘ansem’,
‘7WQDnydFJUqFjGmRfBqbPXiJQ9N4JmTX7EGRj9LqkbFU’: ‘cobie’,
‘3NTnQbdCpxkTzGBMvGybhyGnqwGvmKqDTRUGJQFmhcCB’: ‘hsaka’,
‘GUfCR9mK6azb9vcpsxgXyj7XRPAKJd4KMHTTVvtncGgp’: ‘blknoiz’,
‘DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh’: ‘beanie’,
‘CUPSEYqBDDJHqKhXFsZhgaJECQZajjXEUcwEKKHSHdHu’: ‘cupsey’,
‘4q2wPZMys1zCoAVpNTCqFQKBGoLjSRuHsCRkfmJhLCEH’: ‘degenspartan’,
‘BRpsJtFxRxNSBST5V3RQjp5xcTSBuCzMRe5TxCCQbVTG’: ‘ledgerstatus’,
‘Ez6zFMR7bwm4bBFLTmWXeWCRLQaGRsqVxGbFVXjjjmj4’: ‘crypto_bitlord’,
‘9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM’: ‘inversebrah’,
}
self.wallet_buy_times: Dict[str,List] = defaultdict(list)
self.coordinated_buys: Dict[str,int] = defaultdict(int)
# Activity counters
self.tokens_scanned = 0
self.tokens_considered = 0
self.tokens_scored = 0
self.tokens_ai_called = 0
self.tokens_entered = 0

```
def can_enter(self, addr):
if self.halted: return False, 'HALTED'
if addr in self.blacklist: return False, 'BLACKLISTED'
if addr in self.open: return False, 'ALREADY_OPEN'
if len(self.open) >= CFG['max_open']:return False, 'MAX_OPEN'
if time.time() - self.last_loss_ts < CFG['cooldown_secs']:
return False, 'COOLDOWN'
loss = (self.capital - self.day_capital) / max(0.001, self.day_capital)
if loss <= -CFG['daily_loss_limit']:
self.halted = True
return False, 'DAILY_LIMIT'
return True, 'OK'

def record_close(self, t):
self.closed.append(t)
self.capital += t.size + t.pnl
self.recent_pnl.append(t.pnl_pct)
if t.pnl < 0:
self.last_loss_ts = time.time()
if t.exit_why in ('LIQ_PULL','SELL_SPIKE'):
self.blacklist.add(t.addr)

def adapt_threshold(self):
if len(self.recent_pnl) < 5:
return
wr = sum(1 for p in self.recent_pnl if p > 0) / len(self.recent_pnl)
if wr > 0.70: self.threshold = min(30, self.threshold + 0.5)
elif wr < 0.45: self.threshold = min(30, self.threshold + 1)
else: self.threshold = max(10, self.threshold - 0.5)

@property
def stats(self):
c = self.closed
if not c: return {}
wins = [t for t in c if t.pnl > 0]
losses = [t for t in c if t.pnl <= 0]
return {
'n': len(c), 'wr': len(wins)/len(c),
'pnl': sum(t.pnl for t in c),
'avg_w': statistics.mean(t.pnl_pct for t in wins) if wins else 0,
'avg_l': statistics.mean(t.pnl_pct for t in losses) if losses else 0,
'avg_h': statistics.mean(t.exit_time - t.t_open for t in c),
'wins': len(wins), 'losses': len(losses),
}
```

def score_token(snap: Snap, st: State) -> Tuple[float, Dict]:
s = 20.0
# Age bonus
if snap.age_min < 0.1: s += 15
elif snap.age_min < 1: s += 10
elif snap.age_min < 5: s += 6
elif snap.age_min < 30: s += 2
elif snap.age_min > 120: s -= 3
# Concentration (memecoins are always bundled, mild penalty only)
if snap.top10_pct > 95: s -= 8
elif snap.top10_pct > 85:s -= 3
elif snap.top10_pct < 40:s += 3
# LP/mint
if snap.lp_locked: s += 2
if snap.mint_ok: s += 1
# MC/liq
if snap.mc > 0:
r = snap.liq / snap.mc
if r >= 0.15: s += 3
elif r < 0.02:s -= 4
safety = max(0, min(30, s))

```
# Momentum - pure velocity
prices = list(st.prices[snap.addr])
vols = list(st.vols[snap.addr])
txs = list(st.txs[snap.addr])
mom = 0.0
if len(prices) >= 2:
vel = (prices[-1] - prices[0]) / (prices[0] or 1e-12)
if vel > 0.20: mom += 30
elif vel > 0.10: mom += 20
elif vel > 0.05: mom += 12
elif vel > 0.02: mom += 6
elif vel > 0: mom += 2
elif vel < -0.05:mom -= 8
if len(txs) >= 2 and txs[-2]:
rate = txs[-1] / txs[-2]
if rate > 5: mom += 15
elif rate > 3: mom += 10
elif rate > 2: mom += 6
br = snap.buy_ratio
if br > 0.85: mom += 15
elif br > 0.75: mom += 10
elif br > 0.65: mom += 6
elif br > 0.55: mom += 3
elif br < 0.35: mom -= 8
if len(vols) >= 2 and vols[0]:
vs = vols[-1] / vols[0]
if vs > 10: mom += 12
elif vs > 5: mom += 8
elif vs > 2: mom += 4
mom = max(0, min(30, mom))

# Liquidity score
liq = 0.0
liqs = list(st.liqs[snap.addr])
if snap.liq >= 50000: liq += 10
elif snap.liq >= 20000: liq += 7
elif snap.liq >= 10000: liq += 5
elif snap.liq >= 2000: liq += 2
if len(liqs) >= 3:
g = (liqs[-1] - liqs[0]) / (liqs[0] or 1)
if g > 0.1: liq += min(5, g*20)
elif g < -0.2:liq -= 10
if snap.lp_locked: liq += 3
if snap.mint_ok: liq += 2
liq = max(0, min(20, liq))

# Smart money
smar = 0.0
wallets = st.token_wallets.get(snap.addr, set())
kol_overlap = wallets & st.kol_wallets.keys()
if kol_overlap:
names = [st.kol_wallets[w] for w in kol_overlap]
log.info(f' KOL: {names} in {snap.symbol}')
smar += min(20, len(kol_overlap) * 8)
if getattr(snap,'_boosted',False): smar += 5
coord = st.coordinated_buys.get(snap.addr, 0)
if coord >= 3: smar += 35
elif coord >= 2: smar += 20
elif coord >= 1: smar += 10
smar = min(20, smar)

# Narrative bonus
narr_boost = 0.0
if getattr(snap,'_narrative',False):
kw = getattr(snap,'_narrative_kw','')
narr_boost = 14 if kw in ['ai','trump','elon','pepe'] else 8

total = (safety + mom + liq + smar + narr_boost) * 1.15
total = min(100, max(0, total))

signals = {'safety':safety,'momentum':mom,'liquidity':liq,'smart':smar,
'narrative':getattr(snap,'_narrative_kw',''),'kol_boost':smar}
return total, signals
```

def check_exit(trade: Trade, snap: Snap, st: State):
p = snap.price
pnl = (p - trade.entry) / trade.entry
if p > trade.peak: trade.peak = p
tp = TRADE_TP.get(trade.addr, (CFG[‘take_profit_t1’],CFG[‘take_profit_t2’],CFG[‘take_profit_t3’],CFG[‘stop_loss’]))
tp1,tp2,tp3,sl = tp
if not trade.t1_hit and pnl >= tp1: return ‘TP_T1’
if trade.t1_hit and not trade.t2_hit and pnl >= tp2: return ‘TP_T2’
if trade.t2_hit and pnl >= tp3: return ‘TP_T3’
if pnl <= -sl: return ‘STOP_LOSS’
if trade.t1_hit and trade.peak > trade.entry*(1+tp1):
if p <= trade.peak*(1-CFG[‘trailing_stop’]): return ‘TRAIL_STOP’
elapsed = time.time() - trade.t_open
if elapsed > CFG[‘timeout_secs’] and abs(pnl) < CFG[‘min_move_pct’]: return ‘TIMEOUT’
liqs = list(st.liqs[snap.addr])
if len(liqs) >= 3 and liqs[0] and (liqs[-1]-liqs[0])/liqs[0] < CFG[‘liq_pull_pct’]: return ‘LIQ_PULL’
if snap.buy_ratio < CFG[‘sell_pressure_max’] and snap.tx5m > 8: return ‘SELL_SPIKE’
if snap.vol5m < 50 and elapsed > 20: return ‘VOL_COLLAPSE’
return None

JUDGMENT_CACHE: Dict[str,tuple] = {}

async def get_judgment(snap: Snap, st: State, signals: Dict, score: float, session) -> Dict:
cached = JUDGMENT_CACHE.get(snap.addr)
if cached and time.time() - cached[1] < 20:
return cached[0]

```
# Pattern fallback (always works)
def pattern():
trade = score >= st.threshold
boost = 0.0
reasons = []
prices = list(st.prices[snap.addr])
vols = list(st.vols[snap.addr])
liqs = list(st.liqs[snap.addr])
txs = list(st.txs[snap.addr])
kols = st.token_wallets.get(snap.addr,set()) & st.kol_wallets.keys()

if snap.age_min < 2 and snap.vol5m > 1000 and snap.buy_ratio > 0.55:
boost += 15; trade = True; reasons.append('new+vol')
if kols and snap.buy_ratio > 0.40:
boost += 20; trade = True; reasons.append(f'KOL:{[st.kol_wallets[w] for w in kols]}')
if len(vols) >= 2 and vols[0] and vols[-1]/vols[0] > 3:
boost += 12; trade = True; reasons.append('vol3x')
if len(txs) >= 2 and txs[-2] and txs[-1]/txs[-2] > 3:
boost += 10; trade = True; reasons.append('tx_spike')
if snap.buy_ratio > 0.75:
boost += 8; reasons.append('buy_dom')
if len(liqs) >= 3 and liqs[0] and (liqs[-1]-liqs[0])/liqs[0] < -0.15:
boost -= 20; trade = False; reasons.append('liq_drop')
if snap.buy_ratio < 0.30 and snap.tx5m > 10:
boost -= 15; trade = False; reasons.append('sell_spike')
conf = min(1.0, max(0.1, 0.5 + boost/100))
return {'trade':trade,'confidence':conf,'boost':boost,
'reason':'|'.join(reasons) or 'score','action':'BUY' if trade else 'SKIP','source':'pattern'}

if not GROQ_API_KEY:
return pattern()

kols_in = [st.kol_wallets[w] for w in st.token_wallets.get(snap.addr,set()) & st.kol_wallets.keys()]
prices = list(st.prices[snap.addr])
price_trend = 'up' if len(prices)>=2 and prices[-1]>prices[0]*1.02 else 'flat'

prompt = f'''You are an ultra-fast Solana memecoin scalp trader. 2-30 second holds only.
```

One question: will {snap.symbol} go UP in the next 30 seconds?

Age: {snap.age_min:.1f}min | Liq: ${snap.liq:,.0f} | Vol5m: ${snap.vol5m:,.0f}
Buy%: {snap.buy_ratio*100:.0f}% | TX5m: {snap.tx5m} | Price: {price_trend}
KOLs: {kols_in or ‘none’} | Score: {score:.0f} | Narrative: {signals.get(‘narrative’) or ‘none’}

Reply ONLY with JSON: {{“trade”:true/false,“confidence”:0.0-1.0,“boost”:-20 to 30,“reason”:“brief”,“action”:“BUY/SKIP”}}
Rules: new+vol=BUY, KOL=always BUY, liq dropping=SKIP, be aggressive’’’

```
try:
hdrs = {'Authorization':f'Bearer {GROQ_API_KEY}','Content-Type':'application/json'}
payload = {'model':GROQ_MODEL,'messages':[{'role':'user','content':prompt}],
'max_tokens':100,'temperature':0.2}
async with session.post(GROQ_URL, json=payload, headers=hdrs,
timeout=aiohttp.ClientTimeout(total=3)) as r:
if r.status != 200:
return pattern()
data = await r.json()
text = data['choices'][0]['message']['content'].strip()
text = text[text.find('{'):text.rfind('}')+1]
result = json.loads(text)
result['source'] = 'groq'
JUDGMENT_CACHE[snap.addr] = (result, time.time())
log.info(f" 🤖 {result.get('action','?'):<4} {snap.symbol:<8} conf={result.get('confidence',0):.0%} -- {result.get('reason','')}")
return result
except Exception as e:
log.debug(f'Groq error: {e}')
return pattern()
```

class JupiterExecutor:
def **init**(self, wallet: WalletManager, session: aiohttp.ClientSession):
self.wallet = wallet
self.session = session
self.holdings: Dict[str,int] = {}

```
async def buy(self, snap: Snap, size_usd: float):
if not LIVE_MODE: return 'SIM_TX'
lamports = self.wallet.usd_to_lamports(size_usd)
if lamports < 1000: return None
max_lam = int(self.wallet.balance * 1e9 * CFG['risk_per_trade'])
lamports = min(lamports, max_lam)
quote = await self._quote(WSOL_MINT, snap.addr, lamports, 300)
if not quote: return None
self.holdings[snap.addr] = int(quote.get('outAmount',0))
tx = await self._swap_tx(quote)
if not tx: return None
return await self._send(tx)

async def sell(self, snap: Snap, fraction=1.0):
if not LIVE_MODE: return 'SIM_TX'
held = self.holdings.get(snap.addr, 0)
if held <= 0: return None
amt = int(held * fraction)
if amt < 100: return None
quote = await self._quote(snap.addr, WSOL_MINT, amt, 350)
if not quote: return None
self.holdings[snap.addr] = held - amt
if fraction >= 0.99: self.holdings.pop(snap.addr, None)
tx = await self._swap_tx(quote)
if not tx: return None
return await self._send(tx)

async def _quote(self, inp, out, amount, slippage_bps=300):
try:
params = {'inputMint':inp,'outputMint':out,'amount':str(amount),
'slippageBps':str(slippage_bps),'onlyDirectRoutes':'false'}
async with self.session.get(JUPITER_QUOTE, params=params,
timeout=aiohttp.ClientTimeout(total=3)) as r:
return await r.json() if r.status == 200 else None
except: return None

async def _swap_tx(self, quote):
try:
payload = {'quoteResponse':quote,'userPublicKey':str(self.wallet.pub),
'wrapAndUnwrapSol':True,'computeUnitPriceMicroLamports':2_000_000}
async with self.session.post(JUPITER_SWAP, json=payload,
timeout=aiohttp.ClientTimeout(total=5)) as r:
data = await r.json()
return data.get('swapTransaction') if r.status == 200 else None
except: return None

async def _send(self, tx_b64):
try:
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
tx_bytes = base64.b64decode(tx_b64)
tx = VersionedTransaction.from_bytes(tx_bytes)
msg_bytes = to_bytes_versioned(tx.message)
sig = self.wallet.keypair.sign_message(msg_bytes)
signed_tx = VersionedTransaction.populate(tx.message, [sig])
signed_b64= base64.b64encode(bytes(signed_tx)).decode()
# Try Jito first
try:
jp = {'jsonrpc':'2.0','id':1,'method':'sendTransaction',
'params':[signed_b64,{'encoding':'base64','skipPreflight':True}]}
async with self.session.post(JITO_URL, json=jp,
timeout=aiohttp.ClientTimeout(total=2)) as r:
data = await r.json()
if 'result' in data:
log.info(f' Jito: {data["result"][:8]}...')
return data['result']
except: pass
# RPC fallback
rp = {'jsonrpc':'2.0','id':1,'method':'sendTransaction',
'params':[signed_b64,{'encoding':'base64','skipPreflight':True,
'preflightCommitment':'confirmed','maxRetries':2}]}
async with self.session.post(RPC_URL, json=rp,
timeout=aiohttp.ClientTimeout(total=3)) as r:
data = await r.json()
if 'result' in data:
log.info(f' TX: https://solscan.io/tx/{data["result"]}')
return data['result']
except Exception as e:
log.warning(f' Send error: {e}')
return None
```

def parse_pair(pair, boosted, seen):
if pair.get(‘chainId’) != ‘solana’: return None
base = pair.get(‘baseToken’,{})
addr = base.get(‘address’,’’)
if not addr or addr in seen: return None
seen.add(addr)
try:
liq = float(pair.get(‘liquidity’,{}).get(‘usd’,0) or 0)
vol5m = float(pair.get(‘volume’,{}).get(‘m5’,0) or 0)
vol1h = float(pair.get(‘volume’,{}).get(‘h1’,0) or 0)
price = float(pair.get(‘priceUsd’,0) or 0)
mc = float(pair.get(‘marketCap’,0) or 0)
tx5m = pair.get(‘txns’,{}).get(‘m5’,{})
buys = int(tx5m.get(‘buys’,0) or 0)
sells = int(tx5m.get(‘sells’,0) or 0)
created = pair.get(‘pairCreatedAt’,0) or 0
age = (time.time()*1000 - created)/60000 if created else 999
if price <= 0 or liq < CFG[‘min_liquidity’]: return None
narr, narr_kw = has_narrative(base.get(‘symbol’,’’), base.get(‘name’,’’))
snap = Snap(addr=addr, symbol=base.get(‘symbol’,’???’),
price=price, liq=liq, vol5m=vol5m, vol1h=vol1h,
buys5m=buys, sells5m=sells, age_min=age, mc=mc,
_boosted=addr in boosted, _narrative=narr, _narrative_kw=narr_kw)
return snap
except: return None

async def fetch_pairs(session):
async def get(url):
try:
async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
if r.status == 200:
return await r.json(content_type=None)
except: pass
return {}

```
# Fetch sequentially to avoid rate limits
raw1 = await get('https://api.dexscreener.com/latest/dex/search?q=solana')
await asyncio.sleep(0.3)
raw2 = await get('https://api.dexscreener.com/latest/dex/tokens/solana')
await asyncio.sleep(0.1)
raw3 = await get('https://api.dexscreener.com/token-boosts/latest/v1')

boosted = set()
if isinstance(raw3, list):
for b in raw3:
a = b.get('tokenAddress','')
if a: boosted.add(a)

seen = set()
new_snaps = []
other_snaps = []

for dataset in (raw1, raw2):
if not dataset or not isinstance(dataset, dict): continue
for pair in (dataset.get('pairs') or [])[:100]:
snap = parse_pair(pair, boosted, seen)
if snap is None: continue
if snap.age_min <= 2.0:
new_snaps.append(snap)
else:
other_snaps.append(snap)

new_snaps.sort(key=lambda s: s.age_min)
other_snaps.sort(key=lambda s: s.vol5m, reverse=True)
return new_snaps[:200] + other_snaps[:50]
```

async def scanner_task(session, queue, st):
log.info(’ 🔍 Scanner started’)
tick = 0
while True:
tick += 1
try:
snaps = await fetch_pairs(session)
if snaps:
for snap in snaps:
st.prices[snap.addr].append(snap.price)
st.vols [snap.addr].append(snap.vol5m)
st.liqs [snap.addr].append(snap.liq)
st.txs [snap.addr].append(snap.tx5m)
await queue.put(snap)
log.info(f’ 🔍 Fetched {len(snaps)} pairs | queue={queue.qsize()}’)
else:
log.warning(’ 🔍 No pairs returned – API may be slow’)
except Exception as e:
log.warning(f’ 🔍 Scanner error: {e}’)

```
if tick % 20 == 0:
asyncio.create_task(_kol_scan(session, st))

await asyncio.sleep(CFG['scan_interval'])
```

async def _kol_scan(session, st):
api_key = RPC_URL.split(‘api-key=’)[-1] if ‘api-key=’ in RPC_URL else ‘’
if not api_key: return
async def check(w):
try:
url = f’https://api.helius.xyz/v0/addresses/{w}/transactions’
async with session.get(url, params={‘api-key’:api_key,‘limit’:‘5’,‘type’:‘SWAP’},
timeout=aiohttp.ClientTimeout(total=3)) as r:
if r.status != 200: return
txs = await r.json()
if not txs or not isinstance(txs, list): return
for tx in txs:
for t in tx.get(‘events’,{}).get(‘swap’,{}).get(‘tokenOutputs’,[]):
mint = t.get(‘mint’,’’)
if mint and mint != WSOL_MINT:
st.token_wallets[mint].add(w)
st.coordinated_buys[mint] = len(st.token_wallets[mint] & st.kol_wallets.keys())
except: pass
await asyncio.gather(*[check(w) for w in list(st.kol_wallets.keys())])

async def decision_task(queue, st, executor=None, groq_session=None, wallet=None):
log.info(’ ⚡ Decision engine started’)
slippage = CFG[‘slippage’]
while True:
snap: Snap = await queue.get()
try:
if snap.addr in st.open:
continue
st.tokens_scanned += 1

```
# Pre-filter
if snap.liq < CFG['min_liquidity']: continue
if snap.tx5m < CFG['min_tx_5m']: continue
if snap.buy_ratio < CFG['min_buy_ratio']:continue
if snap.age_min < CFG['age_sweet_spot_min']: continue
st.tokens_considered += 1

# Score
score, signals = score_token(snap, st)
st.tokens_scored += 1

# AI judgment
st.tokens_ai_called += 1
sess = groq_session or asyncio.get_event_loop()
judgment = await get_judgment(snap, st, signals, score, groq_session)
adjusted = score + judgment.get('boost', 0)

# Only hard skip if very low score AND AI confident it's bad
if adjusted < 10 and not judgment['trade'] and judgment.get('confidence',0) > 0.80:
continue

if not judgment.get('trade', True) and judgment.get('confidence',0) > 0.75:
continue

ok, reason = st.can_enter(snap.addr)
if not ok: continue

# Size = 20% of real wallet
real_usd = wallet.balance_usd() if (LIVE_MODE and wallet and wallet.balance_usd()>0) else st.capital
size = real_usd * CFG['risk_per_trade']
e_price = snap.price * (1 + slippage)

tx_sig = None
if LIVE_MODE and executor:
log.info(f' ⚡ BUY {snap.symbol} ${size:.4f}...')
tx_sig = await executor.buy(snap, size)
if not tx_sig:
log.warning(f' Buy failed: {snap.symbol}')
continue

trade = Trade(addr=snap.addr, symbol=snap.symbol, entry=e_price,
score=adjusted, size=size, t_open=time.time(),
signals=signals, ai_conf=judgment.get('confidence',0.6))
st.open[snap.addr] = trade
st.capital -= size
st.tokens_entered += 1
set_trade_tp(snap.addr, judgment.get('confidence',0.6))

narr_tag = f" [{signals['narrative'].upper()}]" if signals.get('narrative') else ''
age_tag = f' NEW{snap.age_min:.1f}m' if snap.age_min < 2 else ''
mode_tag = f'[LIVE {tx_sig[:8]}]' if tx_sig and tx_sig != 'SIM_TX' else '[SIM]'
log.info(f" 📥 ENTER {snap.symbol:<8} score={adjusted:.0f} "
f"conf={judgment.get('confidence',0):.0%}{narr_tag}{age_tag} "
f"${e_price:.8f} pos=${size:.4f} {mode_tag}")
except Exception as e:
log.warning(f' Decision error: {e}')
finally:
queue.task_done()
```

async def exit_task(queue, st, executor=None):
log.info(’ 🛡 Exit watcher started’)
slippage = CFG[‘slippage’]
snap_cache: Dict[str,Snap] = {}

```
while True:
await asyncio.sleep(CFG['exit_check_interval'])
# Drain queue into cache
while True:
try:
snap = queue.get_nowait()
snap_cache[snap.addr] = snap
queue.task_done()
except asyncio.QueueEmpty:
break

for addr in list(st.open.keys()):
trade = st.open.get(addr)
snap = snap_cache.get(addr)
if not trade or not snap: continue
why = check_exit(trade, snap, st)
if not why: continue

if why == 'TP_T1':
if LIVE_MODE and executor: await executor.sell(snap, 0.40)
trade.partial_exit(snap.price, 0.40, slippage, 'T1@20%')
trade.t1_hit = True
continue
if why == 'TP_T2':
if LIVE_MODE and executor: await executor.sell(snap, 0.40)
trade.partial_exit(snap.price, 0.40, slippage, 'T2@35%')
trade.t2_hit = True
continue

if LIVE_MODE and executor: await executor.sell(snap, 1.0)
pnl = trade.close(snap.price, why, slippage)
del st.open[addr]
st.record_close(trade)
st.adapt_threshold()
emoji = '✅' if pnl >= 0 else '❌'
held = int(trade.exit_time - trade.t_open)
log.info(f" 📤 EXIT {trade.symbol:<8} {why:<18} "
f"pnl={trade.pnl_pct*100:+.1f}% ${pnl:+.4f} "
f"held={held}s {emoji}")
```

async def status_task(st, wallet=None, session=None):
log.info(’ 📊 Status task started’)
await asyncio.sleep(2)
tick = 0
last_report = time.time()

```
while True:
tick += 1
now = time.time()

if wallet and session and tick % 15 == 0:
try:
await wallet.fetch_sol_balance(session)
await wallet.fetch_sol_price(session)
except: pass

real_usd = wallet.balance_usd() if (wallet and LIVE_MODE and wallet.balance_usd()>0) else st.capital
real_bal = wallet.balance if (wallet and LIVE_MODE) else 0
open_trs = list(st.open.values())
open_detail = [f'{t.symbol}({int(now-t.t_open)}s)' for t in open_trs]

if open_trs:
log.info(f' 📊 Open={open_detail} Wallet=${real_usd:.3f} Threshold={st.threshold:.0f}')

if now - last_report >= 60:
last_report = now
s = st.stats
log.info(' ' + '='*52)
log.info(f' 📈 MINUTE REPORT')
log.info(f' 💰 Wallet : {real_bal:.6f} SOL = ${real_usd:.4f}')
log.info(f' 🔍 Scanned : {st.tokens_scanned:,} tokens')
log.info(f' 🎯 Considered : {st.tokens_considered:,} passed filter')
log.info(f' 📐 Scored : {st.tokens_scored:,} fully scored')
log.info(f' 🤖 AI Called : {st.tokens_ai_called:,} sent to Groq')
log.info(f' 📥 Traded : {st.tokens_entered:,} entries this minute')
if s:
log.info(f" 📊 All trades : {s['n']} | WR={s['wr']*100:.0f}% | ({s['wins']}W/{s['losses']}L) | PnL=${s['pnl']:+.4f}")
log.info(f" ⚡ Avg hold : {s['avg_h']:.0f}s | Win={s['avg_w']*100:+.1f}% | Loss={s['avg_l']*100:+.1f}%")
else:
log.info(f' 📊 No closed trades yet -- still hunting')
log.info(f' 🚫 Blacklist : {len(st.blacklist)} tokens')
log.info(f' 🎚 Threshold : {st.threshold:.1f}')
log.info(f' 🛑 Halted : {"YES" if st.halted else "No"}')
log.info(' ' + '='*52)
st.tokens_scanned = st.tokens_considered = st.tokens_scored = 0
st.tokens_ai_called = st.tokens_entered = 0

await asyncio.sleep(2)
```

async def main():
connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
async with aiohttp.ClientSession(connector=connector) as session:
wallet = WalletManager()
executor = None

```
if LIVE_MODE:
await wallet.fetch_sol_price(session)
await wallet.fetch_sol_balance(session)
print('='*56)
print(' SOLANA MEMECOIN SNIPER -- LIVE MODE')
print(' Real transactions on Solana mainnet')
print('='*56)
print(f' Wallet : {str(wallet.pub)[:8]}...{str(wallet.pub)[-4:]}')
print(f' Balance : {wallet.balance:.6f} SOL = ${wallet.balance_usd():.4f}')
print(f' Per trade : 20% = ${wallet.balance_usd()*0.20:.4f}')
executor = JupiterExecutor(wallet, session)
else:
print('='*56)
print(' SOLANA MEMECOIN SNIPER -- SIMULATION MODE')
print('='*56)

st = State(wallet)
print(f' Capital : ${st.capital:.4f}')
print(f' Threshold : {st.threshold} (adaptive)')
print(f' Scan every: {CFG["scan_interval"]}s')
print()

groq_connector = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
groq_session = aiohttp.ClientSession(connector=groq_connector)

if GROQ_API_KEY:
log.info(f' 🤖 Groq key: {GROQ_API_KEY[:12]}...')
try:
hdrs = {'Authorization':f'Bearer {GROQ_API_KEY}','Content-Type':'application/json'}
p = {'model':GROQ_MODEL,'messages':[{'role':'user','content':'say ok'}],'max_tokens':5}
async with groq_session.post(GROQ_URL, json=p, headers=hdrs,
timeout=aiohttp.ClientTimeout(total=6)) as r:
body = await r.text()
if r.status == 200:
log.info(' 🤖 Groq CONNECTED -- Llama 3.3 70B ACTIVE')
else:
log.warning(f' 🤖 Groq FAILED {r.status}: {body[:100]}')
except Exception as e:
log.warning(f' 🤖 Groq error: {e}')
else:
log.warning(' 🧠 No GROQ_API_KEY -- pattern engine only')

queue = asyncio.Queue(maxsize=5000)

tasks = [
asyncio.create_task(scanner_task(session, queue, st), name='scanner'),
asyncio.create_task(decision_task(queue, st, executor, groq_session, wallet), name='decision'),
asyncio.create_task(exit_task(queue, st, executor), name='exit'),
asyncio.create_task(status_task(st, wallet, session), name='status'),
]

def _shutdown(sig, frame):
log.info('\n Shutting down...')
for t in tasks: t.cancel()

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

try:
await asyncio.gather(*tasks)
except asyncio.CancelledError:
pass

s = st.stats
print('\n' + '='*56)
print(' FINAL REPORT')
print('='*56)
if s:
print(f" Trades : {s['n']} | WR={s['wr']*100:.0f}% | PnL=${s['pnl']:+.4f}")
print('='*56)
```

if **name** == ‘**main**’:
asyncio.run(main())