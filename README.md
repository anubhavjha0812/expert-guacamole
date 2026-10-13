# Options Trading Engine

A Python trading engine for NIFTY options using Supertrend signals, configurable
position sizing, and scheduled monitoring through Angel One SmartAPI.

## Architecture

The engine separates market data, signal generation, and order management.
Two worker threads monitor entry signals and active positions, with request
limiting and retries for data retrieval.

| Module | Responsibility |
| --- | --- |
| `strategy/config.py` | Environment configuration, validation, and shared state |
| `strategy/market_data.py` | Instrument discovery, contract lookup, and historical candles |
| `strategy/signals.py` | Supertrend calculation and indicator column normalization |
| `strategy/orders.py` | Order submission, local positions, stop loss, and target calculations |
| `strategy/engine.py` | Entry decisions, sizing, exit conditions, and monitoring loops |
| `strategy/rate_limiter.py` | Thread-safe API request limiting |
| `strategy/clock.py` | Candle interval scheduling |
| `strategy/runner.py` | Broker authentication, session management, and worker startup |
| `strategy/tests/` | Offline unit tests with mocked broker responses |

## Setup

Requires Python 3.12 or later. Run from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r strategy/requirements.txt
```

Create a local configuration file if one does not already exist:

```bash
cp strategy/.env.example .env
```

Replace the placeholders in `.env` with your broker credentials and strategy
settings. The file is excluded from version control. Existing process environment
variables take precedence over values loaded from `.env`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `ANGEL_API_KEY`, `ANGEL_USERNAME`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET` | Broker authentication |
| `LOT_SIZE` | Units per lot |
| `MAX_TRADES` | Maximum entry submissions per day |
| `MARTINGALE_LOTS` | Lot multipliers as a JSON list |
| `OPTION_EXPIRY` | Contract expiry in `DDMMMYYYY` format |
| `TICK_TIMEFRAME` | Candle interval, such as `FIVE_MINUTE` |
| `CHECK_INTERVAL` | Monitoring interval in seconds; must match the candle timeframe |
| `TRADE_START_TIME`, `TRADE_END_TIME` | Trading window in `HH:MM`, using the host's local time |
| `SUPERTREND_PERIOD`, `SUPERTREND_MULTIPLIER` | Indicator parameters |
| `INSTRUMENT_LIST_URL` | Instrument catalogue endpoint |
| `OFFLINE_FILE_PATH` | Local instrument catalogue fallback |

Configuration is validated before broker login. The sizing list must cover the
daily trade limit. Set a valid contract expiry and confirm the trading window
before starting.

## Run

```bash
python -m strategy
```

The application authenticates with SmartAPI, loads the instrument catalogue,
and starts signal and position monitoring. Running the engine with valid
credentials enables live order submission. Local position tracking follows
submission; fill confirmation and partial-fill reconciliation are not implemented.

## Tests

```bash
python -m unittest discover -s strategy/tests -t . -v
```

The suite covers configuration parsing, scheduling, rate limiting, instrument
lookup, candle processing, retries, order payloads, signal calculations, caching,
sizing, entry conditions, and exit decisions.

Tests use synthetic market data and mocked broker responses, with network access
blocked. They require no broker credentials and place no real orders. Live broker
authentication and exchange execution are outside the offline test suite.
