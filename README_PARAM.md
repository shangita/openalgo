# OpenAlgo — PARAM Capital (Paper Trading Instance)

Paper trading instance of OpenAlgo connected to Zerodha in ANALYZE mode.
All orders are simulated — no real money at risk.

---

## Quick-Start: Fresh Server Deploy (Ubuntu 24.04)

### 0. Prerequisites

```bash
# Minimum specs: 4 vCPU, 4 GB RAM, 40 GB SSD, Ubuntu 24.04
apt update && apt upgrade -y
apt install -y git curl wget unzip build-essential \
  python3 python3-pip python3-venv python3-dev \
  nginx certbot python3-certbot-nginx \
  postgresql postgresql-contrib libpq-dev
```

Node.js 20 (required for frontend build):
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
```

### 1. Clone repo

```bash
mkdir -p /root/trading
cd /root/trading
git clone https://github.com/shangita/openalgo.git openalgo
```

### 2. Python virtualenv + dependencies

```bash
cd /root/trading/openalgo
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. PostgreSQL setup

```bash
sudo -u postgres psql -c "CREATE USER trader WITH PASSWORD 'trader';"
sudo -u postgres psql -c "CREATE DATABASE openalgo OWNER trader;"

cd /root/trading/openalgo
source venv/bin/activate
flask db upgrade
```

### 4. Configure .env

```bash
cp .env.example .env
nano .env
```

Minimum required values — replace `<...>` with your actual values:

```env
# Flask
FLASK_PORT=5001
FLASK_HOST_IP=127.0.0.1
FLASK_DEBUG=False
FLASK_ENV=production

# Security — generate with: python3 -c "import secrets; print(secrets.token_hex(32))"
APP_KEY=<64-char-hex>
API_KEY_PEPPER=<64-char-hex>

# Database
DATABASE_URL=postgresql://trader:trader@127.0.0.1:5432/openalgo

# Broker — Zerodha
BROKER_API_KEY=<zerodha_api_key>
BROKER_API_SECRET=<zerodha_api_secret>
VALID_BROKERS=zerodha

# CRITICAL: strategy scripts use this to call the API
# Generate from /apikey page after first login, then restart the service
OPENALGO_APIKEY=<your_api_key>

# Logging
LOG_TO_FILE=True
LOG_LEVEL=INFO
LOG_DIR=log
```

### 5. Build the frontend

The frontend build requires ~1.5 GB RAM peak. Kill any stray processes first.

```bash
cd /root/trading/openalgo/frontend
npm install
NODE_OPTIONS='--max-old-space-size=1500' npm run build
```

Built assets go to `frontend/dist/` which Flask serves automatically.

### 6. SSL certificate (Let's Encrypt)

```bash
# Point your domain DNS A-record to this server IP first, then:
certbot certonly --nginx -d yourdomain.duckdns.org
```

### 7. Nginx config

```bash
cat > /etc/nginx/sites-available/openalgo << 'NGINXEOF'
server {
    listen 5000 ssl;
    server_name yourdomain.duckdns.org;

    ssl_certificate /etc/letsencrypt/live/yourdomain.duckdns.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.duckdns.org/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # SSE endpoints (live logs) — disable buffering
    location ~* ^/(scanner/logs|deltaneutral/logs) {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600;
        proxy_set_header Connection '';
        chunked_transfer_encoding on;
    }

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120;
        client_max_body_size 10M;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/openalgo /etc/nginx/sites-enabled/openalgo
nginx -t && systemctl reload nginx
```

### 8. Systemd service

```bash
cat > /etc/systemd/system/openalgo.service << 'SVCEOF'
[Unit]
Description=OpenAlgo Trading Platform
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/trading/openalgo
ExecStart=/root/trading/openalgo/venv/bin/python app.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/openalgo.log
StandardError=append:/var/log/openalgo.log

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable openalgo
systemctl start openalgo
systemctl is-active openalgo
```

### 9. First login and API key

1. Open `https://yourdomain:5000` in browser
2. Register admin user
3. Click **Connect Broker** → complete Zerodha OAuth
4. Go to `/apikey` → generate API key
5. Add to `.env` as `OPENALGO_APIKEY=<key>`
6. `systemctl restart openalgo`

> Zerodha OAuth tokens expire daily. Re-login via browser **before 09:15 IST** each trading day.

---

## Architecture

```
  Browser / Telegram Bot
        |
        v
  +------------------+
  |  Nginx SSL :5000 |
  +--------+---------+
           | proxy
           v
  +----------------------------------------------+
  |   OpenAlgo Flask App  127.0.0.1:5001          |
  |                                               |
  |  +------------+   +-----------------------+  |
  |  | Strategies |   | Telegram Bot          |  |
  |  | Scheduler  |   | + AI (OpenRouter      |  |
  |  +-----+------+   |   Gemini 2.0 Flash)   |  |
  |        |          +-----------------------+  |
  |        v                                     |
  |  +------------+   +-----------------------+  |
  |  | Strategy   |   | Scanner (Nifty50)     |  |
  |  | Scripts    |   | Paper Engine          |  |
  |  | /scripts/  |   +-----------------------+  |
  |  +------------+                              |
  |                                              |
  |  +------------+   +-----------------------+  |
  |  | Delta      |   | Flow Editor           |  |
  |  | Neutral    |   | (Visual Workflows)    |  |
  |  | Monitor    |   +-----------------------+  |
  |  +------------+                              |
  |                                              |
  |       analyze_mode=True (paper only)         |
  |                                              |
  |  +------------------------------------------+|
  |  | PostgreSQL  +  SQLite (db/scanner.db)    ||
  |  +------------------------------------------+|
  +----------------------------------------------+
        | WebSocket :8765 / ZMQ :5555
        v
  External Clients (MCP, MT5, etc.)
```

### Active systemd services

| Service | Description |
|---|---|
| `openalgo.service` | Main Flask app (paper trading, port 5001) |
| `openalgo-live.service` | Live trading instance |
| `openalgo-mcp.service` | MCP server for AI tool integrations (port 8002) |
| `nginx.service` | SSL reverse proxy (port 5000 external) |
| `postgresql@16-main` | PostgreSQL database (port 5432) |
| `paper-engine.service` | PARAM paper P&L engine |
| `straddle-bot.service` | NIFTY Short Straddle Bot |
| `param-api.service` | FastAPI backend |
| `param-capital.service` | PARAM dashboard |

---

## Key URLs

| Page | URL |
|---|---|
| Dashboard | https://meanrev.duckdns.org:5000 |
| Scanner | https://meanrev.duckdns.org:5000/scanner |
| Delta Neutral | https://meanrev.duckdns.org:5000/deltaneutral |
| Flow Editor | https://meanrev.duckdns.org:5000/flow |
| Python Strategies | https://meanrev.duckdns.org:5000/python |
| API Key | https://meanrev.duckdns.org:5000/apikey |

---

## Strategy Scripts

Located in `/root/trading/openalgo/strategies/scripts/`:

| Strategy | File | Symbol |
|---|---|---|
| Edge V2 EMA Pullback Short | `edge_v2_hdfcbank_short_20260402.py` | HDFCBANK FUT |
| Nifty Two-Sided v3.1 | `nifty_twosided_v31_20260402.py` | NIFTY FUT |
| Silver Micro Regime-Adaptive | `silver_regime_adaptive_20260402.py` | SILVERM FUT |
| Dual-Setup Scanner A | `scanner_setup_a_paper.py` | Nifty 50 (49 symbols) |
| Dual-Setup Scanner B | `scanner_setup_b_paper.py` | Nifty 50 (49 symbols) |
| Delta Neutral v1 | `delta_neutral_v1_20260428.py` | Options |

> Strategy scripts call `http://127.0.0.1:5001` directly — never port 5000.
> All read `OPENALGO_APIKEY` from the env file.

---

## Scanner (Nifty 50 Dual-Setup)

URL: `/scanner`

- **Setup A** — EMA5 Pullback on daily bars
- **Setup B** — EMA5 Breakout above previous-day high on 5-min bars
- Universe: 49 Nifty 50 symbols (`services/scanner/universe.py`)
  *(TATAMOTORS removed — demerged into TMCV/TMPV, no longer on NSE)*
- Paper positions with ATR-based trailing stop loss
- Live activity log panel + P&L updates every 60 s

```
services/scanner/
  scheduler.py       periodic scan runner
  scanner_a.py       Setup A (daily EMA pullback)
  scanner_b.py       Setup B (5-min breakout)
  paper_engine.py    paper trade + live unrealised P&L
  store.py           SQLite persistence  (db/scanner.db)
  log_buffer.py      in-memory log ring buffer (200 entries)
  universe.py        Nifty 50 symbol list
```

---

## Delta Neutral Monitor

URL: `/deltaneutral`

Reads live open option positions, computes portfolio Greeks (Delta Gamma Theta Vega),
payoff-at-expiry chart, and streams an activity log.

- Payoff chart includes futures hedge legs and equity holdings
- Hedge/Holdings table shows FUT / EQ / HOLD positions
- Activity log polls incremental entries from `delta_neutral_log_buffer`
- Auto-refresh every 15 s

```
services/delta_neutral_service.py        Greeks + payoff computation
services/delta_neutral_log_buffer.py     in-memory log ring buffer (200 entries)
blueprints/delta_neutral.py              Flask routes
frontend/src/pages/DeltaNeutral.tsx      React page
frontend/src/api/delta-neutral.ts        TypeScript API client
```

---

## Backtest

Located in `backtest/`:

```
backtest/
  engine.py          vectorised backtest runner
  scorecard.py       metrics (Sharpe, max-drawdown, win-rate)
  tearsheet.py       HTML report generator
  run_all.py         batch run all strategies
  config.py          shared config
  requirements.txt   backtest-specific deps
  strategies/
    gold_ema_pullback.py
    silver_rsi_bear.py
    eurusd_dual_osc.py
  data/              place OHLCV CSVs here  (<symbol>.csv)
  reports/           HTML tearsheets written here
```

Run:
```bash
cd /root/trading/openalgo/backtest
pip install -r requirements.txt
python run_all.py
```

---

## Daily Checklist (before 09:15 IST)

```bash
# 1. Verify all services are active
systemctl is-active openalgo openalgo-live straddle-bot paper-engine nginx

# 2. Re-login via browser to refresh Zerodha OAuth token
#    https://meanrev.duckdns.org:5000  ->  Connect Broker

# 3. Confirm master contract is loaded
sudo -u postgres psql -d openalgo -c "SELECT COUNT(*) FROM symtoken;"
# Expected: 147,000+. If 0 -> trigger master contract download from the UI.

# 4. Tail app log
tail -f /var/log/openalgo.log
```

---

## Deploying Frontend Changes

When any file in `frontend/src/` is modified:

```bash
# On the server

# Kill stray node processes first (prevents OOM)
pkill -f vite; pkill -f tsc; sleep 5

# Check free memory  (need >= 1.5 GB)
free -h

# Build
cd /root/trading/openalgo/frontend
NODE_OPTIONS='--max-old-space-size=1500' npm run build

# Restart Flask
systemctl restart openalgo
```

---

## Common Fixes

**"Symbol not found" for a stock:**
```bash
# Check if ticker exists in master contract
sudo -u postgres psql -d openalgo -c \
  "SELECT symbol, exchange FROM symtoken WHERE symbol ILIKE 'TATAMOTORS%';"
# If missing: NSE ticker changed (e.g. TATAMOTORS demerged into TMCV/TMPV)
# Update services/scanner/universe.py accordingly
```

**Strategies not trading / "0 bars":**
```bash
tail -50 /root/trading/openalgo/log/strategies/<latest>.log
# Look for:
#   HTTP 400       wrong host (should be 127.0.0.1:5001, not :5000)
#   BEFORE_SESSION timezone bug (scripts must use Asia/Kolkata)
#   Symbol not found  stale ticker
```

**OOM during frontend build:**
```bash
pkill -f vite; pkill -f tsc
sleep 15
free -h      # wait until >= 2 GB available
NODE_OPTIONS='--max-old-space-size=1500' npm run build
```

**Zerodha OAuth expired (HTTP 401 in strategy logs):**
```bash
# Re-login via browser at https://meanrev.duckdns.org:5000
# Then: systemctl restart openalgo
```

**PostgreSQL not responding:**
```bash
systemctl status postgresql@16-main
systemctl restart postgresql@16-main
```

---

## Git Workflow

```bash
cd /root/trading/openalgo
git status
git add <files>
git commit -m "describe change"
git push origin main
```

Repo: https://github.com/shangita/openalgo

---

## Telegram Bot

Bot: **@paramcapitalbot** — AI-powered via OpenRouter (Gemini 2.0 Flash)

Commands: `/status`  `/positions`  `/funds`  `/pnl`  `/orderbook`  `/tradebook`

Plain text examples:
```
show positions
what is my pnl today
show funds
```

---

## Important Warnings

- `analyze_mode=True` must stay ON — switching it off routes orders as real Zerodha trades
- Strategy scripts must use port **5001** (Flask direct), not 5000 (nginx)
- Both paper and live OpenAlgo instances share the same Zerodha API key
- Zerodha tokens expire daily — browser re-login required every morning before 09:15 IST
- Do NOT activate Flow Editor workflows — strategies run via Python scripts; activating flows causes double-trades
