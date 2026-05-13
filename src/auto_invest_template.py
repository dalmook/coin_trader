"""Upbit 자동매매 템플릿 (실거래 전 충분한 검증 필수)."""

import os
import time
from datetime import datetime

import requests

UPBIT_MINUTE_URL = "https://api.upbit.com/v1/candles/minutes/60"


def get_last_close(market: str = "KRW-BTC", count: int = 120):
    r = requests.get(UPBIT_MINUTE_URL, params={"market": market, "count": count}, timeout=10)
    r.raise_for_status()
    data = r.json()
    closes = [x["trade_price"] for x in reversed(data)]
    return closes


def signal_sma(closes: list[float], fast: int = 20, slow: int = 80) -> int:
    if len(closes) < slow:
        return 0
    f = sum(closes[-fast:]) / fast
    s = sum(closes[-slow:]) / slow
    return 1 if f > s else 0


def main():
    # 반드시 본인 키 설정 (실거래 주문 코드 추가 전 paper mode로 검증)
    paper_mode = os.getenv("PAPER_MODE", "true").lower() == "true"
    market = os.getenv("MARKET", "KRW-BTC")

    position = 0
    while True:
        closes = get_last_close(market)
        sig = signal_sma(closes)

        now = datetime.utcnow().isoformat()
        if sig == 1 and position == 0:
            if paper_mode:
                print(f"[{now}] PAPER BUY {market}")
            else:
                print(f"[{now}] REAL BUY {market} (주문 API 연동 필요)")
            position = 1

        elif sig == 0 and position == 1:
            if paper_mode:
                print(f"[{now}] PAPER SELL {market}")
            else:
                print(f"[{now}] REAL SELL {market} (주문 API 연동 필요)")
            position = 0

        time.sleep(60)


if __name__ == "__main__":
    main()
