# coin_trader

업비트(Upbit) 기준으로 **다양한 전략 백테스트 + 결과 분석 + 자동투자 템플릿**을 바로 돌려볼 수 있게 구성했습니다.

## 포함된 코드

- `src/upbit_backtest.py`
  - 전략 3종 백테스트
    1. `sma_cross_20_80` (추세추종)
    2. `rsi_mean_reversion` (역추세)
    3. `breakout_k05_trend` (돌파 + 추세필터)
  - 출력:
    - `results/all_summary.csv`
    - `results/KRW_BTC_summary.csv` 등 마켓별 CSV

- `src/auto_invest_template.py`
  - 실거래 전 단계용 자동투자 템플릿 (paper mode 기본)
  - SMA 시그널 기반으로 BUY/SELL 로그 출력

## 실행 방법

```bash
python src/upbit_backtest.py --markets KRW-BTC KRW-ETH KRW-XRP --count 600 --unit 60 --out results
```

## 최근 실행 결과 (2026-05-13 UTC)

> 이 실행 환경은 외부망 제한(프록시 403)으로 업비트 API 직접 호출이 불가해,
> `synthetic_candles()` 대체 데이터로 백테스트가 수행되었습니다.

상위 전략(마켓별)은 공통으로 `breakout_k05_trend`가 선택되었습니다.

- KRW-BTC
  - Total Return: `58.96%`
  - MDD: `-0.14%`
  - Win Rate: `96.30%`
- KRW-ETH
  - Total Return: `58.96%`
- KRW-XRP
  - Total Return: `58.96%`

## 자동투자 적용 가이드

1. 백테스트 기간 확대: `--count` 값을 늘려 최소 수개월~수년 검증
2. 수수료/슬리피지 보수적 반영
3. 워크포워드 검증 (학습 구간 / 검증 구간 분리)
4. paper trading 최소 2~4주
5. 실거래 전 리스크 룰 필수
   - 1회 주문 리스크 제한
   - 일중 손실 한도
   - 최대 포지션 크기 제한

## 주의

- 이 코드는 투자 자문이 아닙니다.
- 실거래 주문 API(인증/서명/에러처리/재시도)는 템플릿에 의도적으로 미포함이며,
  반드시 별도 안전장치를 추가한 뒤 사용하세요.
