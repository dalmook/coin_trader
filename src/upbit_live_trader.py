"""Upbit 실거래/모의거래 실행 스크립트 (운영형 가드 포함)."""

import base64, csv, hashlib, hmac, json, os, time, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.upbit.com"


def b64url(data: bytes) -> str: return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def jwt_hs256(payload: dict, secret: str) -> str:
    h = b64url(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(",",":")).encode())
    b = b64url(json.dumps(payload, separators=(",",":")).encode())
    s = hmac.new(secret.encode(), f"{h}.{b}".encode(), hashlib.sha256).digest()
    return f"{h}.{b}.{b64url(s)}"

def auth_headers(access: str, secret: str, params: dict | None = None) -> dict:
    payload = {"access_key": access, "nonce": str(uuid.uuid4())}
    if params:
        q = urlencode(params).encode(); payload["query_hash"] = hashlib.sha512(q).hexdigest(); payload["query_hash_alg"] = "SHA512"
    return {"Authorization": f"Bearer {jwt_hs256(payload, secret)}", "Content-Type": "application/json"}

def http_get(path: str, params: dict | None = None, headers: dict | None = None):
    req = Request(API + path + (("?"+urlencode(params)) if params else ""), method="GET", headers=headers or {})
    with urlopen(req, timeout=15) as r: return json.loads(r.read().decode())

def http_post(path: str, params: dict, headers: dict):
    req = Request(API + path, data=json.dumps(params).encode(), method="POST", headers=headers)
    with urlopen(req, timeout=15) as r: return json.loads(r.read().decode())

def get_candles(market: str, count: int = 120, unit: int = 60):
    return list(reversed(http_get(f"/v1/candles/minutes/{unit}", {"market": market, "count": count})))

def get_ticker(market: str): return http_get("/v1/ticker", {"markets": market})[0]

def sma(v, n): return None if len(v) < n else sum(v[-n:]) / n

def signal_breakout_trend(c):
    closes=[x["trade_price"] for x in c]; opens=[x["opening_price"] for x in c]; highs=[x["high_price"] for x in c]; lows=[x["low_price"] for x in c]
    if len(closes)<50: return 0
    target = opens[-1] + 0.5*(highs[-2]-lows[-2]); trend=sma(closes[:-1],48)
    return 1 if trend is not None and highs[-1] > target and closes[-1] > trend else 0

def signal_sma_cross(c):
    closes=[x["trade_price"] for x in c]; f,s=sma(closes,20),sma(closes,80)
    return 1 if f is not None and s is not None and f>s else 0

def signal_rsi(c):
    closes=[x["trade_price"] for x in c]
    if len(closes)<15:return -1
    d=[closes[i]-closes[i-1] for i in range(1,len(closes))]; g=[max(x,0) for x in d[-14:]]; l=[max(-x,0) for x in d[-14:]]
    rsi = 100 - (100/(1 + ((sum(g)/14)/((sum(l)/14)+1e-12))))
    return 1 if rsi<30 else (0 if rsi>55 else -1)

def get_signal(strategy,candles,prev_pos):
    if strategy=="sma_cross_20_80": return signal_sma_cross(candles)
    if strategy=="rsi_mean_reversion":
        r=signal_rsi(candles); return prev_pos if r==-1 else r
    return signal_breakout_trend(candles)

def pick_best_strategy(market, summary="results/all_summary.csv"):
    best=("breakout_k05_trend", -1e18)
    try:
        with open(summary,newline="") as f:
            for r in csv.DictReader(f):
                if r["market"]==market and float(r["total_return_pct"])>best[1]: best=(r["strategy"], float(r["total_return_pct"]))
    except FileNotFoundError: pass
    return best[0]

def pick_best_market(summary="results/all_summary.csv", quote="KRW"):
    best=(f"{quote}-BTC", -1e18)
    try:
        with open(summary,newline="") as f:
            for r in csv.DictReader(f):
                m=r["market"]
                if m.startswith(f"{quote}-") and float(r["total_return_pct"])>best[1]: best=(m, float(r["total_return_pct"]))
    except FileNotFoundError: pass
    return best[0]

def place_order(access, secret, market, side, ord_type, price="", volume=""):
    p={"market":market,"side":side,"ord_type":ord_type}
    if price: p["price"]=price
    if volume: p["volume"]=volume
    return http_post("/v1/orders", p, auth_headers(access,secret,p))

def get_asset_balance(access, secret, currency):
    for row in http_get("/v1/accounts", headers=auth_headers(access,secret)):
        if row.get("currency")==currency: return float(row.get("balance","0") or 0)
    return 0.0

@dataclass
class Config:
    market: str = os.getenv("MARKET", "AUTO")
    krw_per_order: float = float(os.getenv("KRW_PER_ORDER", "50000"))
    unit: int = int(os.getenv("CANDLE_UNIT", "60"))
    interval_sec: int = int(os.getenv("LOOP_SECONDS", "60"))
    real: bool = os.getenv("REAL_TRADING", "false").lower() == "true"
    strategy: str = os.getenv("STRATEGY", "AUTO")
    min_order_krw: float = float(os.getenv("MIN_ORDER_KRW", "5000"))
    max_slippage_bps: float = float(os.getenv("MAX_SLIPPAGE_BPS", "30"))
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "3.0"))
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "5.0"))


def run():
    cfg=Config(); access,secret=os.getenv("UPBIT_ACCESS_KEY",""),os.getenv("UPBIT_SECRET_KEY","")
    market = pick_best_market() if cfg.market.upper()=="AUTO" else cfg.market
    strategy = pick_best_strategy(market) if cfg.strategy.upper()=="AUTO" else cfg.strategy
    base, coin = market.split("-")
    print(f"[BOOT] market={market} strategy={strategy} mode={'REAL' if cfg.real else 'PAPER'}")

    pos=0; entry_price=0.0; realized_today=0.0; day=datetime.now(timezone.utc).date()
    while True:
        now_dt=datetime.now(timezone.utc)
        if now_dt.date()!=day: day=now_dt.date(); realized_today=0.0
        if realized_today <= -cfg.max_daily_loss_pct:
            print(f"[{now_dt.isoformat()}] DAILY LOSS LIMIT HIT {realized_today:.2f}% <= -{cfg.max_daily_loss_pct}%"); time.sleep(cfg.interval_sec); continue

        candles=get_candles(market,120,cfg.unit); last_close=candles[-1]["trade_price"]; sig=get_signal(strategy,candles,pos)
        ticker=get_ticker(market); trade_price=float(ticker["trade_price"])
        slippage_bps=abs(trade_price-last_close)/last_close*10000
        if slippage_bps>cfg.max_slippage_bps:
            print(f"[{now_dt.isoformat()}] SKIP high slippage {slippage_bps:.1f}bps>{cfg.max_slippage_bps}bps"); time.sleep(cfg.interval_sec); continue

        if pos==1 and entry_price>0:
            unreal=(trade_price/entry_price-1)*100
            if unreal <= -cfg.stop_loss_pct: sig=0

        if sig==1 and pos==0:
            if cfg.krw_per_order < cfg.min_order_krw:
                print(f"[{now_dt.isoformat()}] BUY BLOCKED: KRW_PER_ORDER<{cfg.min_order_krw}")
            else:
                if cfg.real:
                    if not access or not secret: raise RuntimeError("REAL_TRADING=true but API keys missing")
                    o=place_order(access,secret,market,"bid","price",price=str(int(cfg.krw_per_order))); print(f"[{now_dt.isoformat()}] REAL BUY {o.get('uuid',o)}")
                else: print(f"[{now_dt.isoformat()}] PAPER BUY {market} KRW={cfg.krw_per_order}")
                pos=1; entry_price=trade_price

        elif sig==0 and pos==1:
            pnl_pct=(trade_price/entry_price-1)*100 if entry_price>0 else 0
            realized_today += pnl_pct
            if cfg.real:
                if not access or not secret: raise RuntimeError("REAL_TRADING=true but API keys missing")
                vol=get_asset_balance(access,secret,coin)
                if vol>0: o=place_order(access,secret,market,"ask","market",volume=f"{vol:.8f}"); print(f"[{now_dt.isoformat()}] REAL SELL {o.get('uuid',o)} pnl={pnl_pct:.2f}% day={realized_today:.2f}%")
                else: print(f"[{now_dt.isoformat()}] REAL SELL skipped balance=0 pnl={pnl_pct:.2f}% day={realized_today:.2f}%")
            else: print(f"[{now_dt.isoformat()}] PAPER SELL {market} pnl={pnl_pct:.2f}% day={realized_today:.2f}%")
            pos=0; entry_price=0.0

        time.sleep(cfg.interval_sec)

if __name__ == "__main__": run()
