# AI Day Trader

Autonomous AI day trading system powered by Grok (xAI). Learns from every trade,
enriches candidates with real-time news catalysts, and sends push notifications to Discord.

![Python](https://img.shields.io/badge/python-3.11+-green)
![Status](https://img.shields.io/badge/status-paper%20trading-blue)

---

## What It Does

- Scans the market at open and power hour for gap/volume setups
- Enriches top candidates with real-time news via Perplexity Sonar (structured catalyst analysis)
- Passes enriched candidates to Grok for trade decisions with bracket orders (stop + target)
- Monitors positions throughout the day, closes all at 1:00 PM PST
- Learns from outcomes - validated patterns feed back into future Grok prompts
- Sends real-time push notifications to Discord (trade opens, closes, scan summaries, EOD)

---

## API Overview

| API | Required | Cost | What It Buys You |
|-----|----------|------|-----------------|
| **Grok (xAI)** | Yes | ~$1-3/day | Trade decisions - the brain of the system |
| **Schwab** | Yes (data) | Free | Real-time market data (quotes, OHLCV, positions) |
| **Alpaca** | Yes (paper) | Free | Paper trading execution + news headlines |
| **Perplexity Sonar** | Recommended | ~$4/month | Live web search for catalysts per symbol |
| **Brave Search** | Recommended | Free | Sector/theme queries (biotech events, earnings calendar) - only useful if Ollama is also on |
| **Ollama (local)** | Recommended | Free (local GPU) | Pattern backtesting, EOD reflection, data formatting |
| **Discord Bot** | Optional | Free | Real-time push notifications on your phone |
| **Schwab (trading)** | Optional | Free | Live trading (paper mode by default, enable when ready) |

---

## Required APIs

### 1. Grok (xAI) - Trade Decision Engine

**What it does**: Receives enriched candidate data and makes all buy/sell/hold decisions.
Uses `grok-4-0709` for the morning regime check and `grok-4-1-fast-reasoning` for intraday updates.

**Cost**: Roughly $1-3/day depending on number of scans and position updates.

**Setup**:
1. Go to [console.x.ai](https://console.x.ai)
2. Create an account and add a payment method
3. Generate an API key
4. Set the environment variable:
   ```bash
   export XAI_API_KEY="your-key-here"
   ```
   Or on Windows (add to your PowerShell profile):
   ```powershell
   $env:XAI_API_KEY = "your-key-here"
   ```

---

### 2. Schwab - Market Data

**What it does**: Provides real-time quotes, OHLCV bars, and account/position data.
Free with a Schwab brokerage account. Much better than Alpaca's 15-minute delayed free tier.

**Cost**: Free (requires a Schwab brokerage account).

**Setup**:
1. Open a Schwab brokerage account at [schwab.com](https://www.schwab.com) if you don't have one
2. Go to [developer.schwab.com](https://developer.schwab.com) and create an app
3. Set the callback URL to: `https://developer.schwab.com/oauth2-redirect.html`
4. Note your **App Key** and **App Secret**
5. Save them to config files:
   ```
   ai_trader/trading_agent/config/schwab_app_key.txt
   ai_trader/trading_agent/config/schwab_app_secret.txt
   ```
6. Run the OAuth setup script to get your refresh token:
   ```
   ai_trader/renew_schwab_oauth.bat
   ```
   This opens a browser, you authorize with Schwab, copy the full redirect URL
   (`https://developer.schwab.com/oauth2-redirect.html?code=...`) and paste it back
   into the terminal. The refresh token is saved automatically to
   `config/schwab_refresh_token.txt`.

   **Important**: The auth code expires in ~30 seconds. Have the terminal visible
   before you click Authorize in the browser - copy and paste the redirect URL
   immediately or it will fail and you'll need to run the script again.

**Note**: Schwab OAuth tokens expire periodically. Re-run `renew_schwab_oauth.bat`
if data stops working.

---

### 3. Alpaca - Paper Trading + News

**What it does**: Executes paper trades (simulated money) and provides news headlines
used for position monitoring. Paper trading is the default - no real money at risk.

**Cost**: Free tier is sufficient (paper trading + news API included).

**Setup**:
1. Create an account at [alpaca.markets](https://alpaca.markets)
2. Go to your dashboard > API Keys
3. Generate a **Paper Trading** API key and secret
4. Set environment variables:
   ```bash
   export ALPACA_API_KEY="your-key-here"
   export ALPACA_SECRET_KEY="your-secret-here"
   ```
5. In `broker_config.json`, ensure `active_broker` is set to `alpaca_paper`

**Switching to live trading**: Change `active_broker` to `schwab_live` and set
`schwab_live.enabled` to `true` in `broker_config.json`. The system will ask for
confirmation before placing any real orders.

---

## Recommended APIs

### 4. Perplexity Sonar - Catalyst Enrichment

**What it does**: For each top scan candidate, makes a targeted web search for news
from the last 48-72 hours and returns structured JSON with catalyst type, direction,
date, relevance, and whether the move is already priced in. This context is injected
into the Grok prompt so it knows *why* a stock is moving before deciding to trade it.

**Without it**: Falls back to Brave Search + Ollama for plain-text summaries (requires Ollama).
If Ollama is also off, enrichment is skipped entirely. See Enrichment Modes below.

**Cost**: ~$0.005/request + ~$0.001 in tokens. At 7 candidates/scan and 2 scans/day
this is roughly **$4/month**. Perplexity auto-refills at a threshold you set.

**Setup**:
1. Go to [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api)
2. Add a payment method (set auto-refill to $10 recommended)
3. Generate an API key
4. Save it to:
   ```
   ai_trader/trading_agent/config/perplexity_api_key.txt
   ```
5. In `broker_config.json` set `perplexity.enabled` to `true`

---

### 5. Brave Search - Sector Queries

**What it does**: Makes 3 broad topic queries at market open (e.g. "biotech FDA approvals today",
"earnings surprises pre-market") to surface sector-level catalysts that single-symbol
searches might miss. Also used for news checks on open positions.

**Without it**: Sector context queries are skipped. Individual stock enrichment still
works via Perplexity.

**Note**: Brave Search is only useful if Ollama is also enabled. Brave fetches the
raw headlines but Ollama is what summarizes them. If Ollama is off, Brave results
go unused - see Enrichment Modes below.

**Cost**: Free tier - 2,000 requests/month at 1 req/sec. Current usage is ~400-600/month.

**Setup**:
1. Go to [brave.com/search/api](https://brave.com/search/api/)
2. Sign up for the free tier
3. Generate an API key
4. In `broker_config.json`, set `brave_search.api_key` to your key

---

### 6. Ollama - Local AI Workers

**What it does**: Runs local LLM workers for tasks that don't need frontier AI:
pattern backtesting (re-validates learned lessons on historical data), EOD trade
reflection, data formatting, and news summarization. Runs on your GPU - no API cost.

**Without it**: Backtesting is skipped. If Perplexity is also off, catalyst enrichment
is skipped entirely and Grok receives raw scan data only. See Enrichment Modes below.

**Cost**: Free - runs locally. Requires an Nvidia GPU with enough VRAM.

**Setup**:
1. Install Ollama from [ollama.com](https://ollama.com)
2. Pull the models for your GPU tier (see table below)
3. In `broker_config.json` set `ollama.enabled` to `true` and update `analysis_model` / `worker_model`
4. Ollama is auto-started by the scheduler at 6:20 AM and shut down at 4:00 PM

**Models by VRAM** (24GB config is confirmed working; smaller tiers are starting points - monitor
`nvidia-smi` and reduce `num_workers` if VRAM is tight):

| VRAM | GPU example | Analysis model | Worker model | Workers |
|------|-------------|---------------|--------------|---------|
| 24GB | RTX 3090/4090 | `qwen3:14b` (~9.3GB) | `qwen2.5:7b` (~5GB shared) | 6 |
| 20GB | RTX 3080 Ti | `qwen3:14b` (~9.3GB) | `qwen2.5:7b` (~5GB shared) | 3 |
| 16GB | RTX 3080/4080 | `qwen3:8b` (~6GB) | `qwen2.5:7b` (~5GB shared) | 2 |
| 12GB | RTX 3060 Ti | `qwen3:8b` (~6GB) | `qwen2.5:7b` (~5GB shared) | 1 |

Note: Worker weights are shared in memory - multiple workers cost little extra VRAM beyond
the first. The main risk is the analysis model + first worker not fitting together.
Start with `num_workers: 1`, confirm it fits with `nvidia-smi`, then increase.

```bash
# 24GB / 20GB
ollama pull qwen3:14b && ollama pull qwen2.5:7b

# 16GB / 12GB
ollama pull qwen3:8b && ollama pull qwen2.5:7b
```

Update `broker_config.json` to match your setup:
```json
"ollama_worker_pool": {
    "analysis_model": "qwen3:14b",
    "worker_model": "qwen2.5:7b",
    "num_workers": 6
}
```

---

## Enrichment Modes

Perplexity and Ollama are independent. Here is exactly what Grok receives under each combination:

| Perplexity | Ollama | What Grok sees for each candidate |
|------------|--------|-----------------------------------|
| on | either | Structured catalyst JSON: type, direction, date, relevance, priced_in flag. Best quality. Backtesting also runs in parallel (if Ollama on). |
| off | on | Brave Search fetches raw headlines, Ollama summarizes them into a plain-text catalyst summary. Decent quality. Brave is only useful in this combination. |
| off | off | No catalyst data at all. Grok still receives scan data (symbol, score, gap%, relative volume, ATR, float, sector) and makes decisions with that alone. Graceful degradation - nothing breaks. |

**Recommendation**: Keep Perplexity on ($4/month). Ollama is a free bonus for backtesting -
the enrichment path does not depend on it when Perplexity is enabled.

---

## Optional APIs

### 7. Discord Bot - Push Notifications

**What it does**: Sends real-time push notifications to a Discord channel so you
can monitor trades from your phone without watching logs. Automatically creates a
monthly thread ("February 2026", "March 2026") so messages stay organized.

**Events notified**: Trade opened (entry, conviction, catalyst, stop/target),
trade closed (P&L, exit reason), scan summaries, circuit breaker alerts, daily EOD summary.

**Cost**: Free.

**Setup**:

**Step 1 - Create a Discord application and bot:**
1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (e.g. "Trading Agent")
3. Left sidebar: **Bot** > click **Add Bot**
4. Under the token section click **Reset Token** and copy it
5. Save the token to:
   ```
   ai_trader/trading_agent/config/discord_bot_token.txt
   ```

**Step 2 - Invite the bot to your server:**
1. Left sidebar: **OAuth2** > **URL Generator**
2. Under Scopes check: `bot`
3. Under Bot Permissions check:
   - View Channels
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
4. Copy the generated URL, open it in your browser
5. Select your Discord server and click Authorize

**Step 3 - Get your channel ID:**
1. In Discord: User Settings > Advanced > enable **Developer Mode**
2. Right-click the channel you want notifications in
3. Click **Copy Channel ID**
4. Save it to:
   ```
   ai_trader/trading_agent/config/discord_channel_id.txt
   ```

**Step 4 - Give the bot channel access:**
1. Right-click your trading channel > **Edit Channel** > **Permissions**
2. Click **+** and find your bot
3. Enable: View Channel, Send Messages, Create Public Threads, Send Messages in Threads

**Test it:**
```bash
cd ai_trader/trading_agent
python test_discord_notifier.py
```

You should see a "February 2026" thread appear in your channel with 6 test messages.
Delete the thread when done - the bot will auto-create a fresh one on the next real event.

**Note**: `config/discord_bot_token.txt` and `config/discord_channel_id.txt` are gitignored.
Never commit these files.

---

## Configuration Summary

Keys are split between environment variables and config files depending on how each
module reads them. All config files are gitignored.

**Environment variables** (set in your shell profile so they load on every session):
```bash
XAI_API_KEY          # Grok (xAI) - trade decisions
ALPACA_API_KEY       # Alpaca paper trading + news
ALPACA_SECRET_KEY    # Alpaca secret
SCHWAB_APP_KEY       # Schwab real-time data
SCHWAB_APP_SECRET    # Schwab secret
```

On Windows, add these to your PowerShell profile (`notepad $PROFILE`):
```powershell
$env:XAI_API_KEY = "your-key"
$env:ALPACA_API_KEY = "your-key"
$env:ALPACA_SECRET_KEY = "your-key"
$env:SCHWAB_APP_KEY = "your-key"
$env:SCHWAB_APP_SECRET = "your-key"
```

**Config files** (read directly from disk, no env var needed):
```
ai_trader/ai_trader_data/broker_config.json       # Brave Search key lives inside here
ai_trader/trading_agent/config/
  perplexity_api_key.txt     # Perplexity Sonar key
  discord_bot_token.txt      # Discord bot token
  discord_channel_id.txt     # Discord channel ID
  discord_thread_id.txt      # Auto-managed, do not edit
```

---

## Quick Start

```bash
git clone https://github.com/jchap2k/grok_day_trader.git
cd grok_day_trader/ai_trader/trading_agent

pip install -r requirements.txt

# Set required environment variables (see above)
export XAI_API_KEY="..."
export SCHWAB_APP_KEY="..."
export SCHWAB_APP_SECRET="..."
export ALPACA_API_KEY="..."
export ALPACA_SECRET_KEY="..."

# Run (paper trading by default - no real money)
python automated_scheduler.py
```

The scheduler will start at the next scheduled time. During market hours it runs
every 30 minutes. Outside market hours it waits and logs a heartbeat.

---

## System Requirements

**Minimum** (no local AI):
- Python 3.11+
- 4GB RAM
- Any modern CPU

**Recommended** (with Ollama workers):
- Python 3.11+
- 8GB RAM
- Nvidia GPU with 16GB+ VRAM (RTX 3090 or better for full worker pool)

---

## Safety

- Paper trading is the default - real money requires explicit config change
- Circuit breaker halts new entries if daily loss exceeds -2% (weekly -5%)
- All positions auto-close at 1:00 PM PST
- Forbidden symbols protection prevents trading pre-existing long-term holdings
- Stale price guard blocks bracket orders if entry price is >5% from current price

---

## Disclaimer

Trading involves risk. This system is provided for educational and research purposes.
Past performance does not guarantee future results. Always test with paper trading
before using real money. The authors are not responsible for financial losses.
