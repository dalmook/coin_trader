# coin_trader

## 핵심
- `src/upbit_backtest.py`: 전략 백테스트 + 결과 CSV 생성
- `src/upbit_live_trader.py`: 실거래/모의거래 봇 (운영형 가드 포함)

## 운영형 가드(적용됨)
- 최소 주문금액 체크: `MIN_ORDER_KRW` (기본 5000)
- 슬리피지 한도: `MAX_SLIPPAGE_BPS` (기본 30bp)
- 손절: `STOP_LOSS_PCT` (기본 3%)
- 일손실 제한: `MAX_DAILY_LOSS_PCT` (기본 5%)

## 자동 선택
- 코인 자동 선택: `MARKET=AUTO` (results/all_summary.csv 기준 최고 수익률 마켓)
- 전략 자동 선택: `STRATEGY=AUTO` (선택된 마켓 기준 최고 수익률 전략)

> 즉, 원하면 코인도 자동/전략도 자동으로 가능합니다.

## 백테스트
```bash
python src/upbit_backtest.py --markets KRW-BTC KRW-ETH KRW-XRP --count 600 --unit 60 --out results
```

## 실행 (PAPER)
```bash
MARKET=AUTO \
STRATEGY=AUTO \
REAL_TRADING=false \
KRW_PER_ORDER=50000 \
MIN_ORDER_KRW=5000 \
MAX_SLIPPAGE_BPS=30 \
STOP_LOSS_PCT=3.0 \
MAX_DAILY_LOSS_PCT=5.0 \
python src/upbit_live_trader.py
```

## 실행 (REAL)
```bash
UPBIT_ACCESS_KEY=... \
UPBIT_SECRET_KEY=... \
MARKET=AUTO \
STRATEGY=AUTO \
REAL_TRADING=true \
KRW_PER_ORDER=50000 \
MIN_ORDER_KRW=5000 \
MAX_SLIPPAGE_BPS=30 \
STOP_LOSS_PCT=3.0 \
MAX_DAILY_LOSS_PCT=5.0 \
python src/upbit_live_trader.py
```

## Synology NAS Container Manager 기준
1. 프로젝트를 NAS에 올리고(예: `/volume1/docker/coin_trader`) 이미지 생성 또는 Python 베이스 이미지 사용.
2. Container Manager > 프로젝트/컨테이너 생성 시 작업 디렉토리를 `/workspace/coin_trader`로 마운트.
3. 환경변수에 위 실행 예시 값 입력 (`UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY`, `REAL_TRADING` 등).
4. 시작 명령:
   - 백테스트: `python src/upbit_backtest.py --markets KRW-BTC KRW-ETH KRW-XRP --count 600 --unit 60 --out results`
   - 자동매매: `python src/upbit_live_trader.py`
5. 재시작 정책을 `always`로 설정하고, 로그에서 `DAILY LOSS LIMIT HIT`, `SKIP high slippage` 메시지 확인.

## 주의
- 투자 자문 아님. 실거래 전 PAPER 모드로 충분히 검증하세요.


## Synology Container Manager 빠른 배포 (docker-compose)
1. 이 저장소를 NAS 폴더(예: `/volume1/docker/coin_trader`)에 업로드.
2. Container Manager → **프로젝트** → **생성** → 폴더 선택.
3. `docker-compose.yml` 자동 인식 후 환경변수 수정:
   - 실거래면 `REAL_TRADING=true`
   - `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY` 입력
4. **배포** 클릭.
5. 로그에서 `BOOT`, `SKIP high slippage`, `DAILY LOSS LIMIT HIT` 메시지 확인.

### 백테스트 컨테이너 실행
- compose의 `backtest` 서비스는 `tools` profile로 분리되어 있습니다.
- 필요 시 터미널에서:
```bash
docker compose --profile tools up backtest
```


## 텔레그램 일일 리포트 (KST 오전 9시 기본)
환경변수 추가:
- `TELEGRAM_BOT_TOKEN` : 텔레그램 봇 토큰
- `TELEGRAM_CHAT_ID` : 받을 채팅 ID
- `REPORT_HOUR_KST` : 리포트 발송 시각(기본 9)

리포트 내용:
- 당일 매매 횟수
- 당일 손익(%)
- 누적 손익(%)
- 현재 포지션
- 현재가
- 일손실 제한값

예시:
```bash
TELEGRAM_BOT_TOKEN=xxxx TELEGRAM_CHAT_ID=123456789 REPORT_HOUR_KST=9 python src/upbit_live_trader.py
```
