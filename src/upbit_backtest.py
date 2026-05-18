import argparse
import csv
import json
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
API_URL = "https://api.upbit.com/v1"


def sma(values, period, i):
    if i + 1 < period:
        return None
    w = values[i - period + 1 : i + 1]
    return sum(w) / period


def stdev(values):
    if not values:
        return 0.0
    m = sum(values) / len(values)
    return (sum((x - m) ** 2 for x in values) / len(values)) ** 0.5


def fetch_candles(market: str, unit: int, count: int):
    rows = []
    to = None
    remain = count
    while remain > 0:
        batch = min(200, remain)
        params = {"market": market, "count": batch}
        if to:
            params["to"] = to
        url = BASE_URL.format(unit=unit) + "?" + urlencode(params)
        data = None
        for attempt in range(5):
            try:
                with urlopen(url, timeout=15) as res:
                    data = json.loads(res.read().decode("utf-8"))
                break
            except (HTTPError, URLError) as e:
                if attempt == 4:
                    raise RuntimeError(f"failed to fetch candles for {market}: {e}") from e
                time.sleep(0.5 * (attempt + 1))
        if not data:
            break
        rows.extend(data)
        last = data[-1]["candle_date_time_utc"]
        dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%S") - timedelta(seconds=1)
        to = dt.strftime("%Y-%m-%dT%H:%M:%S")
        remain -= len(data)
        time.sleep(0.15)

    rows.sort(key=lambda x: x["candle_date_time_utc"])
    dedup = {}
    for r in rows:
        dedup[r["candle_date_time_utc"]] = r
    ordered = [dedup[k] for k in sorted(dedup.keys())]
    return ordered


def top_krw_markets(limit: int, exclude=None):
    exclude = set(exclude or [])
    with urlopen(f"{API_URL}/market/all?isDetails=false", timeout=15) as res:
        markets = [
            r["market"]
            for r in json.loads(res.read().decode("utf-8"))
            if r["market"].startswith("KRW-") and r["market"] not in exclude
        ]

    tickers = []
    for i in range(0, len(markets), 100):
        chunk = markets[i : i + 100]
        url = f"{API_URL}/ticker?" + urlencode({"markets": ",".join(chunk)})
        with urlopen(url, timeout=15) as res:
            tickers.extend(json.loads(res.read().decode("utf-8")))
        time.sleep(0.05)

    tickers.sort(key=lambda r: r.get("acc_trade_price_24h", 0), reverse=True)
    return [r["market"] for r in tickers[:limit]]


def backtest(records, mode):
    closes = [r["trade_price"] for r in records]
    opens = [r["opening_price"] for r in records]
    highs = [r["high_price"] for r in records]
    lows = [r["low_price"] for r in records]

    pos = [0.0] * len(records)
    fee = 0.0005

    if mode == "sma_cross_20_80":
        for i in range(len(records)):
            f = sma(closes, 20, i)
            s = sma(closes, 80, i)
            sig = 1.0 if (f is not None and s is not None and f > s) else 0.0
            pos[i] = pos[i - 1] if i > 0 else 0.0
            if i > 0:
                pos[i] = sig
    elif mode == "rsi_mean_reversion":
        gains, losses = [], []
        current = 0.0
        for i in range(len(records)):
            if i == 0:
                pos[i] = 0.0
                continue
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
            if len(gains) > 14:
                gains.pop(0); losses.pop(0)
            if len(gains) < 14:
                pos[i] = current
                continue
            avg_up = sum(gains) / 14
            avg_dn = sum(losses) / 14
            rs = avg_up / (avg_dn + 1e-12)
            rsi = 100 - 100 / (1 + rs)
            if rsi < 30: current = 1.0
            elif rsi > 55: current = 0.0
            pos[i] = current
    elif mode == "bb_reversion_20_2":
        current = 0.0
        for i in range(len(records)):
            ma = sma(closes, 20, i)
            if ma is None:
                pos[i] = current
                continue
            sd = stdev(closes[i - 19 : i + 1])
            lower = ma - 2 * sd
            if closes[i] < lower:
                current = 1.0
            elif closes[i] > ma:
                current = 0.0
            pos[i] = current
    else:
        for i in range(len(records)):
            trend = sma(closes, 48, i)
            sig = 0.0
            if i > 0:
                rng = highs[i - 1] - lows[i - 1]
                target = opens[i] + 0.5 * rng
                sig = 1.0 if highs[i] > target and trend is not None and closes[i] > trend else 0.0
            pos[i] = sig

    returns = [0.0] * len(records)
    equity = [1.0] * len(records)
    for i in range(1, len(records)):
        r = closes[i] / closes[i - 1] - 1
        turnover = abs(pos[i] - pos[i - 1])
        sr = pos[i - 1] * r - turnover * fee
        returns[i] = sr
        equity[i] = equity[i - 1] * (1 + sr)

    return pos, returns, equity


def calc_metrics(records, pos, returns, equity):
    total_ret = (equity[-1] - 1) * 100
    t0 = datetime.strptime(records[0]["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(records[-1]["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    years = max((t1 - t0).total_seconds() / (365 * 24 * 3600), 1 / 365)
    cagr = ((equity[-1] / equity[0]) ** (1 / years) - 1) * 100

    peak, mdd = equity[0], 0.0
    for e in equity:
        if e > peak: peak = e
        dd = e / peak - 1
        if dd < mdd: mdd = dd

    trades = sum(1 for i in range(1, len(pos)) if pos[i] > pos[i - 1])
    in_rets = [returns[i] for i in range(1, len(returns)) if pos[i - 1] > 0]
    wins = sum(1 for x in in_rets if x > 0)
    win_rate = (wins / len(in_rets) * 100) if in_rets else 0.0
    sharpe = ((sum(returns) / len(returns)) / (stdev(returns) + 1e-12)) * math.sqrt(24 * 365)

    return dict(trades=trades, total_return_pct=total_ret, cagr_pct=cagr, max_drawdown_pct=mdd * 100, win_rate_pct=win_rate, sharpe=sharpe)


@dataclass
class Row:
    strategy: str
    market: str
    trades: int
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    sharpe: float


def synthetic_candles(count, unit, market):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    market_seed = sum(ord(c) for c in market)
    price = 80_000_000.0 + (market_seed % 30_000_000)
    out = []
    for i in range(count):
        t = now - timedelta(minutes=unit*(count-i))
        drift = 0.0001 + ((market_seed % 17) - 8) * 0.00001
        phase1 = (market_seed % 19) / 10
        phase2 = (market_seed % 23) / 10
        vol = 0.002 + (market_seed % 7) * 0.00025
        shock = math.sin(i / (13 + (market_seed % 5)) + phase1) * vol + math.cos(i / (7 + (market_seed % 3)) + phase2) * (vol * 0.65)
        ret = drift + shock
        open_p = price
        close_p = price * (1 + ret)
        high_p = max(open_p, close_p) * (1 + 0.0015)
        low_p = min(open_p, close_p) * (1 - 0.0015)
        out.append({
            "candle_date_time_utc": t.strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": open_p,
            "high_price": high_p,
            "low_price": low_p,
            "trade_price": close_p,
        })
        price = close_p
    return out

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--markets", nargs="+", default=["KRW-BTC", "KRW-ETH", "KRW-XRP"])
    p.add_argument("--count", type=int, default=1000)
    p.add_argument("--unit", type=int, default=60)
    p.add_argument("--out", default="results")
    p.add_argument("--top-markets", type=int, default=30)
    p.add_argument("--min-history", type=int, default=0)
    args = p.parse_args()

    out = Path(args.out); out.mkdir(exist_ok=True)
    markets = args.markets
    if len(markets) == 1 and markets[0].upper() == "AUTO":
        markets = top_krw_markets(args.top_markets, exclude={"KRW-USDT"})
        print(f"[AUTO_MARKETS] {' '.join(markets)}")

    all_rows = []
    for m in markets:
        records = fetch_candles(m, args.unit, args.count)
        if args.min_history and len(records) < args.min_history:
            print(f"[SKIP] {m} history={len(records)} < {args.min_history}")
            continue
        market_rows = []
        for strat in ["sma_cross_20_80", "rsi_mean_reversion", "bb_reversion_20_2", "breakout_k05_trend"]:
            pos, rets, eq = backtest(records, strat)
            met = calc_metrics(records, pos, rets, eq)
            row = Row(strat, m, **met)
            all_rows.append(row); market_rows.append(row)

        with open(out / f"{m.replace('-', '_')}_summary.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=asdict(market_rows[0]).keys()); w.writeheader()
            for r in market_rows: w.writerow(asdict(r))

    if not all_rows:
        raise RuntimeError("no backtest rows generated")

    with open(out / "all_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=asdict(all_rows[0]).keys()); w.writeheader()
        for r in all_rows: w.writerow(asdict(r))

    best = {}
    for r in all_rows:
        if r.market not in best or r.total_return_pct > best[r.market].total_return_pct:
            best[r.market] = r
    print(json.dumps({k: asdict(v) for k, v in best.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
