# OpenAlgo — Paper Trading Instance (PARAM Capital)

Paper trading instance of OpenAlgo connected to Zerodha in ANALYZE mode. All orders are simulated — no real money at risk.

## Architecture

```
  Telegram Bot              Browser
  @paramcapitalbot          https://:5000
        │                        │
        │                        ▼
        │               ┌─────────────────┐
        │               │   Nginx (SSL)   │
        │               │   Port 5000     │
        │               └────────┬────────┘
        │                        │ proxy
        ▼                        ▼
  ┌─────────────────────────────────────────┐
  │         OpenAlgo Flask App              │
  │         127.0.0.1:5001                  │
  │                                         │
  │  ┌──────────────┐  ┌─────────────────┐  │
  │  │  Strategies  │  │  Telegram Bot   │  │
  │  │  Scheduler   │  │  + OpenRouter   │  │
  │  └──────┬───────┘  │    AI Layer     │  │
  │         │          └─────────────────┘  │
  │         ▼                               │
  │  ┌──────────────┐  ┌─────────────────┐  │
  │  │ Strategy     │  │   Flow Editor   │  │
  │  │ Scripts      │  │   (Visual       │  │
  │  │ /strategies/ │  │    Workflows)   │  │
  │  │  /scripts/   │  └─────────────────┘  │
  │  └──────┬───────┘                       │
  │         │ analyze_mode=True             │
  │         ▼ (virtual orders only)         │
  │  ┌──────────────┐                       │
  │  │  PostgreSQL  │                       │
  │  │   openalgo   │                       │
  │  └──────────────┘                       │
  └─────────────────────────────────────────┘
        │ WebSocket :8765
        │ ZMQ :5555
        ▼
  External Clients (optional)
```

## Key Details

| Property | Value |
|---|---|
| External URL | https://meanrev.duckdns.org:5000 |
| Flask (internal) | http://127.0.0.1:5001 |
| Database | PostgreSQL `openalgo` |
| Mode | `analyze_mode=True` (paper/sandbox) |
| Broker | Zerodha (`y2w2anuotknt3zc9`) |
| Service | `openalgo.service` |
| WebSocket | ws://127.0.0.1:8765 |
| ZMQ | tcp://127.0.0.1:5555 |

## Setup

### 1. Clone & install

```bash
git clone https://github.com/shangita/openalgo.git /root/trading/openalgo
cd /root/trading/openalgo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure .env

```bash
cp .env.example .env
nano .env
```

Critical variables:
```env
FLASK_PORT=5001
DATABASE_URL=postgresql://trader:trader@127.0.0.1:5432/openalgo
BROKER_API_KEY=<zerodha_api_key>
BROKER_API_SECRET=<zerodha_api_secret>
OPENALGO_APIKEY=<your_paper_api_key>   # Required for strategy scripts
APP_KEY=<random_64_char_hex>
API_KEY_PEPPER=<random_64_char_hex>
```

> **Critical**: `OPENALGO_APIKEY` must be set — strategy scripts read this via `os.getenv("OPENALGO_APIKEY")`. Without it, all strategy API calls return 500.

### 3. Setup PostgreSQL

```bash
sudo -u postgres psql
CREATE USER trader WITH PASSWORD 'trader';
CREATE DATABASE openalgo OWNER trader;
\q
cd /root/trading/openalgo
flask db upgrade
```

### 4. Configure Nginx SSL

```nginx
server {
    listen 5000 ssl;
    ssl_certificate /etc/letsencrypt/live/meanrev.duckdns.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/meanrev.duckdns.org/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 5. Create systemd service

```bash
cat > /etc/systemd/system/openalgo.service << EOF
[Unit]
Description=OpenAlgo Paper Trading
After=network.target postgresql.service

[Service]
WorkingDirectory=/root/trading/openalgo
ExecStart=/root/trading/openalgo/venv/bin/python app.py
Restart=always
EnvironmentFile=/root/trading/openalgo/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable openalgo
systemctl start openalgo
```

## Daily Startup

Every morning before 09:15 IST:

```bash
# 1. Ensure service is running
systemctl is-active openalgo

# 2. Login via browser to refresh Zerodha OAuth token
# URL: https://meanrev.duckdns.org:5000
# This is REQUIRED daily — Zerodha tokens expire each day

# 3. Verify strategies are running
cat /root/trading/openalgo/strategies/strategy_configs.json

# 4. Tail strategy logs
ls -lt /root/trading/openalgo/log/strategies/ | head -5
```

## Paper Strategies

Located in `/root/trading/openalgo/strategies/scripts/`:

| Strategy | File | Symbol | Lot Size | Session |
|---|---|---|---|---|
| Edge V2 EMA Pullback Short | `edge_v2_hdfcbank_short_20260402.py` | HDFCBANK28APR26FUT | 550 | 09:20–14:30 |
| Nifty Two-Sided v3.1 | `nifty_twosided_v31_20260402.py` | NIFTY28APR26FUT | 65 | 09:20–14:30 |
| Silver Micro Regime-Adaptive | `silver_regime_adaptive_20260402.py` | SILVERM30APR26FUT | 1 | 09:15–23:25 |
| Silver RSI7 Bear | `silver_rsi7_bear_20260402.py` | SILVERM30APR26FUT | 1 | STOPPED |

### Strategy State Management

```bash
# View strategy state
cat /root/trading/openalgo/strategies/strategy_configs.json

# To permanently stop a strategy (survives restarts):
# Set: is_running=false, is_scheduled=false, manually_stopped=true
# Then restart: systemctl restart openalgo

# To restart all strategies:
systemctl restart openalgo
```

### Common Fixes

**"0 bars" / No trades:**
```bash
# Check strategy logs for HTTP 400
tail -50 /root/trading/openalgo/log/strategies/<latest_log>
# Ensure scripts use host="http://127.0.0.1:5001" (NOT 5000)
```

**"Symbol not found":**
```bash
# Check symtoken table is populated
sudo -u postgres psql -d openalgo -c "SELECT COUNT(*) FROM symtoken;"
# Should be ~147,000+. If 0, trigger master contract download from UI.
```

**"BEFORE_SESSION" during market hours:**
```bash
# Scripts must use IST timezone
# from zoneinfo import ZoneInfo
# now = datetime.now(ZoneInfo('Asia/Kolkata'))
```

## Telegram Bot + AI

Bot: **@paramcapitalbot**

Commands:
- `/status` — connection status
- `/positions` — open positions
- `/funds` — available funds
- `/pnl` — today's P&L (realized + unrealized)
- `/orderbook` — today's orders
- `/tradebook` — today's trades

**AI (OpenRouter — Gemini 2.0 Flash):**
Send any plain text message to the bot:
```
show positions
what is my pnl today
buy 65 nifty futures market
show funds
```

AI service: `services/openrouter_service.py`

## Flow Editor

Visual workflow builder at: https://meanrev.duckdns.org:5000/flow

Pre-configured flows:
- Edge V2 EMA Pullback Short — HDFCBANK
- Nifty Two-Sided v3.1
- Silver Micro Regime-Adaptive

> **Note**: Flows are inactive by default. Do NOT activate them — trading is handled by the Python strategy scripts. Activating would cause double-trades.

## Important Warnings

- `analyze_mode=True` must stay ON — switching to live mode would route orders as real Zerodha trades
- Strategy scripts call `http://127.0.0.1:5001` directly (not nginx port 5000)
- Both paper and live OpenAlgo instances share the same Zerodha API key

## Monitoring

```bash
systemctl status openalgo
tail -f /var/log/openalgo.log
ls -lt /root/trading/openalgo/log/strategies/
```
