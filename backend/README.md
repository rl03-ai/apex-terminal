# Apex backend v3

FastAPI backend for an investment-focused equity discovery and portfolio intelligence platform.

## What is implemented now

- Real scoring engine based on stored fundamentals, events and medium-term price structure.
- Scanner profiles for repricing, early growth, quality compounders, narrative and speculative ideas.
- Portfolio snapshot logic with thesis status and bear/base/bull scenarios.
- Configurable data provider layer:
  - `demo` for deterministic local data
  - `yfinance` for real market data
- REST endpoints to ingest tickers, recompute scores, run scanners and refresh portfolio snapshots.
- SQLite by default, ready to run locally.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python seed_demo.py
uvicorn app.main:app --reload
```

Open the docs at `http://127.0.0.1:8000/docs`.

## Real data mode

Edit `.env`:

```bash
DATA_PROVIDER=yfinance
YFINANCE_HISTORY_DAYS=370
```

Then ingest one or more tickers:

```bash
curl -X POST http://127.0.0.1:8000/assets/NVDA/refresh-all
curl -X POST http://127.0.0.1:8000/assets/ingest/batch   -H 'Content-Type: application/json'   -d '["MSFT", "NVDA", "RKLB"]'
```

## Useful endpoints

- `POST /assets/{ticker}/ingest`
- `POST /assets/{ticker}/refresh-all`
- `POST /scanner/run`
- `GET /scanner/top-opportunities`
- `POST /portfolios/{portfolio_id}/refresh`
- `POST /positions/{position_id}/refresh`
- `GET /positions/{position_id}/scenarios`

## Notes

- Auth remains intentionally simplified for local development.
- In `yfinance` mode, event coverage is limited compared with premium providers.
- The scoring model is transparent and rule-based rather than predictive magic.
