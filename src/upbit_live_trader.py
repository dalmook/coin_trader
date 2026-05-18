"""Upbit live/paper trading script with Telegram reports."""

import base64, csv, hashlib, hmac, json, os, time, uuid, subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.upbit.com"
KST = timezone(timedelta(hours=9))


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

def signal_bb_reversion(c):
    closes=[x["trade_price"] for x in c]
    if len(closes)<20:return -1
    window=closes[-20:]
    ma=sum(window)/20
    sd=(sum((x-ma)**2 for x in window)/20)**0.5
    lower=ma-2*sd
    return 1 if closes[-1]<lower else (0 if closes[-1]>ma else -1)

def get_signal(strategy,candles,prev_pos):
    if strategy=="sma_cross_20_80": return signal_sma_cross(candles)
    if strategy=="rsi_mean_reversion":
        r=signal_rsi(candles); return prev_pos if r==-1 else r
    if strategy=="bb_reversion_20_2":
        r=signal_bb_reversion(candles); return prev_pos if r==-1 else r
    return signal_breakout_trend(candles)


def run_backtest(markets: list[str], count: int, unit: int, out_dir: str = "results", top_markets: int = 30, min_history: int = 0):
    cmd = [
        "python", "src/upbit_backtest.py",
        "--markets", *markets,
        "--count", str(count),
        "--unit", str(unit),
        "--out", out_dir,
        "--top-markets", str(top_markets),
        "--min-history", str(min_history),
    ]
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def parse_summary(summary="results/all_summary.csv"):
    rows=[]
    with open(summary, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "strategy": r["strategy"],
                "market": r["market"],
                "trades": int(float(r["trades"])),
                "total_return_pct": float(r["total_return_pct"]),
                "max_drawdown_pct": float(r["max_drawdown_pct"]),
                "sharpe": float(r["sharpe"]),
            })
    return rows


def pick_best_combo(summary="results/all_summary.csv", quote="KRW", min_trades=3, max_mdd_pct=20.0, min_sharpe=0.2, fallback=None):
    try:
        rows = parse_summary(summary)
    except FileNotFoundError:
        if fallback:
            return fallback[0], fallback[1], "keep current(no summary)"
        return f"{quote}-BTC", "sma_cross_20_80", "fallback(no summary)"

    filtered=[]
    for r in rows:
        if not r["market"].startswith(f"{quote}-"):
            continue
        if r["trades"] < min_trades:
            continue
        if abs(r["max_drawdown_pct"]) > max_mdd_pct:
            continue
        if r["sharpe"] < min_sharpe:
            continue
        filtered.append(r)

    if not filtered:
        if fallback:
            return fallback[0], fallback[1], "keep current(no filtered candidate)"
        return f"{quote}-BTC", "sma_cross_20_80", "fallback(no filtered candidate)"
    pool = filtered
    if not pool:
        if fallback:
            return fallback[0], fallback[1], "keep current(no rows)"
        return f"{quote}-BTC", "sma_cross_20_80", "fallback(no rows)"
    top = sorted(pool, key=lambda x: x["total_return_pct"], reverse=True)[0]
    reason = f"ret={top['total_return_pct']:.2f} trades={top['trades']} mdd={top['max_drawdown_pct']:.2f} sharpe={top['sharpe']:.2f}"
    return top["market"], top["strategy"], reason


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


def send_telegram(token: str, chat_id: str, text: str):
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text}, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, method="POST", headers={"Content-Type": "application/json; charset=utf-8"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def now_kst():
    return datetime.now(timezone.utc).timestamp() + 9*3600


def kst_hour_min(dt_utc):
    ts = dt_utc.timestamp() + 9*3600
    h = int((ts % 86400)//3600)
    m = int((ts % 3600)//60)
    d = int(ts // 86400)
    return d, h, m


def kst_date_key(dt_utc=None):
    dt_utc = dt_utc or datetime.now(timezone.utc)
    return dt_utc.astimezone(KST).date().isoformat()


def load_state(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[STATE] load failed: {e}")
        return {}


def save_state(path: str, state: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def build_state(market, strategy, pos, entry_price, realized_today, cumulative_pnl, trades_today, day_key, last_report_day):
    return {
        "market": market,
        "strategy": strategy,
        "pos": pos,
        "entry_price": entry_price,
        "realized_today": realized_today,
        "cumulative_pnl": cumulative_pnl,
        "trades_today": trades_today,
        "day_kst": day_key,
        "last_report_day": last_report_day,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


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
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    report_hour_kst: int = int(os.getenv("REPORT_HOUR_KST", "9"))
    reselect_interval_min: int = int(os.getenv("RESELECT_INTERVAL_MIN", "1440"))
    reselect_backtest_count: int = int(os.getenv("RESELECT_BACKTEST_COUNT", "3000"))
    reselect_markets: str = os.getenv("RESELECT_MARKETS", "KRW-BTC,KRW-ETH,KRW-XRP")
    reselect_top_markets: int = int(os.getenv("RESELECT_TOP_MARKETS", "30"))
    reselect_min_history: int = int(os.getenv("RESELECT_MIN_HISTORY", os.getenv("RESELECT_BACKTEST_COUNT", "3000")))
    select_min_trades: int = int(os.getenv("SELECT_MIN_TRADES", "3"))
    select_max_mdd_pct: float = float(os.getenv("SELECT_MAX_MDD_PCT", "20"))
    select_min_sharpe: float = float(os.getenv("SELECT_MIN_SHARPE", "0.2"))
    state_path: str = os.getenv("LIVE_STATE_PATH", "results/live_state.json")


def run():
    cfg=Config(); access,secret=os.getenv("UPBIT_ACCESS_KEY",""),os.getenv("UPBIT_SECRET_KEY","")
    markets_for_reselect = [m.strip() for m in cfg.reselect_markets.split(",") if m.strip()]
    if cfg.market.upper()=="AUTO" or cfg.strategy.upper()=="AUTO":
        market, strategy, reason = pick_best_combo(min_trades=cfg.select_min_trades, max_mdd_pct=cfg.select_max_mdd_pct, min_sharpe=cfg.select_min_sharpe)
        print(f"[SELECT] market={market} strategy={strategy} reason={reason}")
    else:
        market = cfg.market
        strategy = cfg.strategy
    base, coin = market.split("-")
    print(f"[BOOT] market={market} strategy={strategy} mode={'REAL' if cfg.real else 'PAPER'}")

    state = load_state(cfg.state_path)
    day = kst_date_key()
    state_day = state.get("day_kst")
    realized_today = float(state.get("realized_today", 0.0)) if state_day == day else 0.0
    cumulative_pnl = float(state.get("cumulative_pnl", 0.0))
    trades_today = state.get("trades_today", []) if state_day == day else []
    last_report_day = state.get("last_report_day") if state_day == day else None
    pos = int(state.get("pos", 0) or 0)
    entry_price = float(state.get("entry_price", 0.0) or 0.0)
    if pos == 1 and state.get("market"):
        market = state.get("market", market)
        strategy = state.get("strategy", strategy)
        base, coin = market.split("-")
        print(f"[STATE] restored open position market={market} strategy={strategy} entry={entry_price}")
    last_reselect_ts = time.time()

    while True:
        now_dt=datetime.now(timezone.utc)
        current_day = kst_date_key(now_dt)
        if current_day != day:
            day=current_day; realized_today=0.0; trades_today=[]; last_report_day=None
            save_state(cfg.state_path, build_state(market, strategy, pos, entry_price, realized_today, cumulative_pnl, trades_today, day, last_report_day))
        if realized_today <= -cfg.max_daily_loss_pct:
            print(f"[{now_dt.isoformat()}] DAILY LOSS LIMIT HIT {realized_today:.2f}% <= -{cfg.max_daily_loss_pct}%"); time.sleep(cfg.interval_sec); continue

        kday, kh, km = kst_hour_min(now_dt)
        if kh == cfg.report_hour_kst and km < 2 and last_report_day != kday:
            ticker = get_ticker(market)
            live_price = float(ticker["trade_price"])
            msg = (
                f"[일일 리포트] {market}\n"
                f"- 모드: {'실거래' if cfg.real else '모의거래'}\n"
                f"- 전략: {strategy}\n"
                f"- 리포트 시각: KST {cfg.report_hour_kst}:00\n"
                f"- 오늘 매도 횟수: {len(trades_today)}회\n"
                f"- 오늘 실현손익률: {realized_today:.2f}%\n"
                f"- 누적 실현수익률: {cumulative_pnl:.2f}%\n"
                f"- 현재 포지션: {'보유중' if pos else '미보유'}\n"
                f"- 현재가: {live_price:,.0f}원\n"
                f"- 일 손실 제한: -{cfg.max_daily_loss_pct}%"
            )
            try:
                send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
                print(f"[{now_dt.isoformat()}] TELEGRAM daily report sent")
            except Exception as e:
                print(f"[{now_dt.isoformat()}] TELEGRAM send failed: {e}")
            last_report_day = kday
            save_state(cfg.state_path, build_state(market, strategy, pos, entry_price, realized_today, cumulative_pnl, trades_today, day, last_report_day))

        if pos == 0 and (cfg.market.upper()=="AUTO" or cfg.strategy.upper()=="AUTO") and (time.time() - last_reselect_ts >= cfg.reselect_interval_min * 60):
            old_market, old_strategy = market, strategy
            try:
                run_backtest(
                    markets_for_reselect,
                    cfg.reselect_backtest_count,
                    cfg.unit,
                    "results",
                    top_markets=cfg.reselect_top_markets,
                    min_history=cfg.reselect_min_history,
                )
                market, strategy, reason = pick_best_combo(
                    min_trades=cfg.select_min_trades,
                    max_mdd_pct=cfg.select_max_mdd_pct,
                    min_sharpe=cfg.select_min_sharpe,
                    fallback=(old_market, old_strategy),
                )
                base, coin = market.split("-")
                print(f"[{now_dt.isoformat()}] RESELECT market={market} strategy={strategy} reason={reason}")
                if market != old_market or strategy != old_strategy:
                    msg = (f"[자동 재선정 변경]\n"
                           f"- 이전: {old_market} / {old_strategy}\n"
                           f"- 변경: {market} / {strategy}\n"
                           f"- 사유: {reason}")
                    try:
                        send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, msg)
                        print(f"[{now_dt.isoformat()}] TELEGRAM reselect alert sent")
                    except Exception as e:
                        print(f"[{now_dt.isoformat()}] TELEGRAM reselect alert failed: {e}")
            except Exception as e:
                print(f"[{now_dt.isoformat()}] RESELECT failed: {e}")
            last_reselect_ts = time.time()
            save_state(cfg.state_path, build_state(market, strategy, pos, entry_price, realized_today, cumulative_pnl, trades_today, day, last_report_day))

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
                save_state(cfg.state_path, build_state(market, strategy, pos, entry_price, realized_today, cumulative_pnl, trades_today, day, last_report_day))

        elif sig==0 and pos==1:
            pnl_pct=(trade_price/entry_price-1)*100 if entry_price>0 else 0
            realized_today += pnl_pct
            cumulative_pnl += pnl_pct
            trades_today.append({"time": now_dt.isoformat(), "market": market, "strategy": strategy, "pnl_pct": pnl_pct, "price": trade_price})
            if cfg.real:
                if not access or not secret: raise RuntimeError("REAL_TRADING=true but API keys missing")
                vol=get_asset_balance(access,secret,coin)
                if vol>0: o=place_order(access,secret,market,"ask","market",volume=f"{vol:.8f}"); print(f"[{now_dt.isoformat()}] REAL SELL {o.get('uuid',o)} pnl={pnl_pct:.2f}% day={realized_today:.2f}%")
                else: print(f"[{now_dt.isoformat()}] REAL SELL skipped balance=0 pnl={pnl_pct:.2f}% day={realized_today:.2f}%")
            else: print(f"[{now_dt.isoformat()}] PAPER SELL {market} pnl={pnl_pct:.2f}% day={realized_today:.2f}%")
            pos=0; entry_price=0.0
            save_state(cfg.state_path, build_state(market, strategy, pos, entry_price, realized_today, cumulative_pnl, trades_today, day, last_report_day))

        time.sleep(cfg.interval_sec)

if __name__ == "__main__": run()
