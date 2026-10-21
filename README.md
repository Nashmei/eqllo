# XAUUSD NVIDIA AI Signal Filter (M1/M5)

A local Windows dashboard and FastAPI webhook receiver that filters candidate XAUUSD signals with NVIDIA NIM. It does **not** place trades.

## Architecture
1. TradingView Pine Script detects a candidate setup locally.
2. TradingView sends an alert to `https://YOUR-TUNNEL/webhook/YOUR_WEBHOOK_TOKEN`.
3. FastAPI acknowledges immediately and schedules NVIDIA analysis in the background.
4. Results are stored in SQLite and shown at `http://127.0.0.1:8000`.

> TradingView cannot receive an HTTP response back into Pine Script. AI decisions appear in this external dashboard, not as retroactive chart labels.

## Windows setup
1. Install Python 3.11+.
2. Download/clone this repository.
3. Copy `.env.example` to `.env` and fill in `NVIDIA_API_KEY` and a long random `WEBHOOK_TOKEN`.
4. Run `install.bat`, then `start.bat`.
5. Open `http://127.0.0.1:8000`.
6. For TradingView webhooks, expose port 8000 using a trusted HTTPS tunnel (for example, a tunnel service you configure). Do not expose the server directly to the public internet without HTTPS and a strong token.
7. In TradingView, add `gold_ai.pine` to the chart, create an alert using **Any alert() function call**, and set the webhook URL to `https://YOUR-HTTPS-HOST/webhook/YOUR_WEBHOOK_TOKEN`.

## NVIDIA configuration
The app calls the NVIDIA OpenAI-compatible endpoint:
`https://integrate.api.nvidia.com/v1/chat/completions`
Set `NVIDIA_MODEL` to a model available to your API key. The default is a configurable example; confirm availability in your NVIDIA account.

## Files
- `app.py`: API, background AI task, SQLite, dashboard endpoints.
- `dashboard.html`: dashboard UI.
- `gold_ai.pine`: illustrative candidate detector for M1/M5.
- `test_connection.py`: API-key/model connectivity test.
- `.env.example`: configuration template; never commit your real `.env`.

## Security
- Never put the NVIDIA API key in Pine Script, TradingView alert payloads, or GitHub.
- `.env` is ignored by Git.
- The webhook token is a secret URL path segment. Rotate it if it is exposed.
- This is a starter implementation. Add a production ASGI server, persistent task queue, HTTPS reverse proxy, rate limiting, and monitoring before relying on it continuously.

## Signal logic caveats
The Pine Script is a starter example, not a validated trading strategy. It uses a simple liquidity-sweep/structure approximation and M5 EMA context. It does not guarantee profitable signals. Backtest with realistic spread, slippage, symbol feed differences, and out-of-sample data before live use.
