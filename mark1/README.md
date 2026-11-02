# Mark 1: personal trading dashboard (DEMO ONLY)

Mark 1 is a paper-trading research dashboard for an OANDA **practice** account. It reads candles,
runs a multi-timeframe signal engine, backtests it honestly, logs paper trades to SQLite, and can
send one WhatsApp alert per confirmed setup.

**It contains no code that places orders.** It only reads market data (`GET` requests), so every
trade in the log is a paper trade. Scores are heuristics out of 100, **not probabilities**.

## Quick start

```bash
cd mark1
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # skip if your .env already exists
python run.py                                          # open http://127.0.0.1:8000
```

The `.env` file can live in `mark1/` or one folder up. It is listed in `.gitignore`. The app never
prints or logs its values; the UI only shows ✓/✗ for whether each key is set.

| Variable | Needed for |
|---|---|
| `OANDA_TOKEN`, `OANDA_ENV=practice` | Live candles for XAUUSD, EURUSD, GBPUSD. Without a token, the app runs on **synthetic** data and shows a yellow SYNTHETIC DATA badge. |
| `FINNHUB_KEY` (optional) | Forex headlines under the market grid. |
| `TWILIO_SID`, `TWILIO_TOKEN`, `WHATSAPP_FROM`, `WHATSAPP_TO` | WhatsApp alerts (`whatsapp:` prefix optional). |
| `MARK1_DATA=synthetic` (optional) | Forces offline synthetic mode. |
| `MARK1_HOST`, `MARK1_PORT` (optional) | Default `127.0.0.1:8000`. Leave the host local unless you add authentication: there is no login. |

Run the tests with `python -m pytest` from inside `mark1/`. They never touch the network or your keys.

## Project layout

```
mark1/
  run.py                  one-command start (uvicorn)
  app/
    config.py             pairs, timeframes, strategy params, .env loading
    db.py                 SQLite: settings, candle cache, paper trades, alerts, backtests, suggestions
    data/                 oanda.py, binance.py, synthetic.py, news.py, market.py (source + cache), resample.py
    engine/               indicators.py, setups.py (FVG/OB/swings/BOS/CHoCH/6-to-6), sessions.py, signals.py
    backtest/             engine.py (simulator + metrics), walkforward.py
    runner.py             30-second background loop (keeps running with the browser closed)
    alerts.py             Twilio WhatsApp + de-duplication
    stats.py              proof panel + per-setup past stats
    assistant.py          suggest-only command assistant
    server.py             FastAPI JSON API + static UI
  static/                 index.html, app.css, app.js, vendored Lightweight Charts 4.2.3 (Apache-2.0)
  tests/                  engine, backtest, runner, services, API
```

## Data

- **OANDA v20 practice**: M1/M5/H1/H4 candles with `price=BA`. Mid = (bid+ask)/2, and the spread is the real ask−bid.
- **BTCUSD from Binance** (`BTCUSDT` public klines, no key). At startup the app asks your OANDA
  account which instruments it lists and shows the answer in the footer
  ("BTC on OANDA: …"). Most practice accounts don't list BTC. Binance has no spread field, so the
  backtest uses a fixed $5 spread for BTC.
- **Forex Factory** weekly calendar (`nfs.faireconomy.media/ff_calendar_thisweek.json`) is cached for
  1 hour in memory and on disk. Signals pause from 15 minutes before to 15 minutes after any **high-impact USD** event.
  If the feed fails, the header shows `clear*` and the error appears in the status line.
- Candles are cached in SQLite, so a second 6-month backtest doesn't download everything again.

## Signal engine (per pair)

1. Trend on 4h, 1h and 5m: up when EMA20 > EMA50 **and** the 20-bar regression slope > 0 (down is the mirror). All three must agree.
2. 1m entry: within the last 3 bars, the regression z-score went ≤ −`z_entry` (a pullback in an uptrend),
   RSI14 is turning up from below 50, and the candle closes up. Shorts mirror this.
3. Sessions: London/NY, 07:00–21:00 UTC (not DST-adjusted) for FX and gold. BTC trades 24/7.
4. No new signals inside a news window, after the kill switch, or once the daily loss limit is hit.
5. SL = 1.5 × ATR14 on **5m** (1m ATR is too noisy). TP = 2R.
6. **Score (0–100, heuristic):** alignment 30, pullback depth 15, RSI turn 10, 5m FVG touch 10,
   5m order-block touch 10, 5m structure bias 10, session (overlap 10, otherwise 6), 6-to-6 breakout or fake-out 5.
   A signal needs score ≥ `min_score` (default 55).
7. Setups are detected on 5m: FVGs (3-candle gaps ≥ 0.1 ATR), order blocks (the last opposite candle
   before a ≥ 1.2 ATR displacement), swing highs/lows (2 bars each side, confirmed only 2 bars later),
   BOS/CHoCH, and the **6-to-6 range** (18:00→06:00 New York time). A close outside the range is a
   breakout; closing back inside within 6 bars makes it a fake breakout.
8. "Why this setup?" shows the 13-item checklist (8 hard rules plus confluence) and the past stats
   for that setup type from the latest backtest and paper trades. It warns when there are fewer than 100 trades.

## Backtest

- Up to 6 months of 1m data. Higher timeframes are resampled from 1m, and only **closed** higher-timeframe bars are used.
- A signal at the close of bar *i* fills at the **open of bar *i+1***. Costs are half the spread on entry and on
  exit, slippage (pips) on entry and on stops, and a stop that gaps fills at the open.
- **SL is checked before TP** when both fall inside one bar. One position per pair. The daily loss limit applies.
- **Walk-forward**: tune `z_entry` × `min_score` on N days, test on the next M unseen days, roll forward.
  Only the unseen test trades are reported, and the app warns if the tuned windows made money while the unseen ones lost.
- Output: trades, win rate, profit factor, avg R, total R, max drawdown (R), equity curve.
- The verdict box says plainly when the sample is under 100 trades, when PF < 1 (it lost money), when PF is marginal,
  and when drawdown exceeds the gain. **News is not filtered in backtests** because there is no free historical calendar.

## Alerts and the background loop

The loop runs every 30 s inside the server, whether or not a browser is open. When a 1m candle closes
with a confirmed BUY/SELL and the engine is running, it opens a paper trade and, if WhatsApp is on,
sends **one** message (pair, side, entry, SL, TP, score). The key `pair|side|candle time` is stored
in SQLite, so restarts don't send duplicates.

## Assistant (suggest-only)

- View commands apply straight away and change only the display, never the strategy:
  `show FVG on 5m`, `hide order blocks`, `show 6-to-6 range on 1h`.
- `why this setup?` shows the checklist and past stats.
- Strategy commands such as `set min score to 65`, `use z 1.5`, `set SL to 2 ATR`, `set RR to 2.5`, `trailing on`,
  `daily limit 2` and `optimize` **only create a suggestion**. Each suggestion is backtested against the current settings
  on the same 60 days of data and shown with Approve / Reject. Nothing changes until you click Approve.
- `optimize` tunes on the first 70% of the data and shows results on the unseen last 30%.
- It is a rule-based parser, not an LLM, so it understands only these command shapes (type `help`).

## Safety

- The red DEMO ACCOUNT badge, the kill switch (header and risk panel) and the daily loss limit are always visible.
- The kill switch stops the engine, closes all open paper trades, and blocks Start until you reset it.
- There are no person-lookup, OSINT or tracking features.

## What to check on your machine

1. `python run.py` with your `.env`: the SYNTHETIC badge should be **gone** and prices should match OANDA.
2. Footer "BTC on OANDA": confirm what your account lists.
3. Header "News": should read `clear` or `PAUSED`, not `clear*` (the asterisk means the feed failed).
4. Run a 6-month backtest on XAUUSD. The first run downloads about 36 pages of 1m candles from OANDA and takes a few minutes.
5. Turn WhatsApp on and test it with `curl -X POST localhost:8000/api/alerts/test`. You need to have
   joined the Twilio WhatsApp sandbox from your phone first.
