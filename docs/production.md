# Production deployment

This document is the operator's runbook for moving from `localhost`
development to the NY-region VPS that runs the bot in paper or live
mode. See `CLAUDE.md` §3 for the rationale behind these choices.

## Prerequisites

- A domain name (e.g. `tradingbot.example.com`). Cloudflare's free
  tier handles DNS, WAF and DDoS — recommended.
- A VPS in the NY region with at least 2 vCPU, 4 GB RAM, 40 GB SSD.
  QuantVPS or AWS `us-east-1` work; pick one ≤ 1 ms from the IBKR
  gateway.
- Latest Debian 12 or Ubuntu 22.04 LTS. SSH key auth only.
- Already-completed Phase 0 prerequisites (`CLAUDE.md` §5):
  IBKR Pro account funded, market data subscription active, IB
  Gateway + IBC installed.

## Topology

```
                    ┌──────────────────────────────────────────────┐
                    │                                              │
   Internet ──TLS──▶│  Cloudflare (WAF + DDoS, free tier)          │
                    │                                              │
                    │                                              │
                    └────────┬─────────────────────────────────────┘
                             │
                             │ TLS to origin
                             ▼
                    ┌──────────────────────────────────────────────┐
                    │  VPS (NY region)                             │
                    │                                              │
                    │  Caddy :443 ◀── auto-cert from Let's Encrypt │
                    │   ├─ /api/* + /healthz ─▶ FastAPI :8000      │
                    │   └─ everything else   ─▶ /var/www/tradingbot│
                    │                                              │
                    │  systemd:                                    │
                    │    tradingbot.service       (the bot)        │
                    │    tradingbot-api.service   (FastAPI)        │
                    │    docker compose (Postgres+Timescale+Redis  │
                    │                    + Prometheus + Grafana)   │
                    │                                              │
                    │  IB Gateway + IBC (the broker connection)    │
                    └──────────────────────────────────────────────┘
```

The frontend is statically served by Caddy; the bot and the API are
two independent `systemd` units that talk through the docker-compose
Postgres + Redis.

## One-time host setup

```bash
# Install required tooling
sudo apt-get update
sudo apt-get install -y caddy git rsync python3-pip
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo install -m 0755 -d /var/www/tradingbot
sudo install -m 0750 -o tradingbot -g tradingbot -d /etc/tradingbot

# Clone the repo into the user's home
sudo useradd --create-home --shell /bin/bash tradingbot
sudo -iu tradingbot
git clone git@github.com:rmoral/Trading_algoritmico.git ~/app
cd ~/app
uv sync --frozen
```

Stateful infra (Postgres + Redis + Prometheus + Grafana) still runs
through `docker compose up -d` — same compose file as in
development. The bot connects to it over `127.0.0.1`.

## Secrets

`/etc/tradingbot/env` is owned by `tradingbot:tradingbot`, mode 0600,
and is the only place credentials live:

```env
IBKR_HOST=127.0.0.1
IBKR_PORT=4002              # Switch to 4001 when going live.
IBKR_CLIENT_ID=1
IBKR_ACCOUNT=DUxxxxxxx

LIVE_TRADING=false          # Must be true AND port=4001 for live.
KILL_SWITCH=false

DATABASE_URL=postgresql+asyncpg://tradingbot:<strong-password>@localhost:5432/tradingbot
REDIS_URL=redis://localhost:6379/0

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

WEB_API_HOST=127.0.0.1
WEB_API_PORT=8000
WEB_API_SECRET_KEY=...      # secrets.token_urlsafe(32)
WEB_ADMIN_USERNAME=admin
WEB_ADMIN_PASSWORD=...      # strong, generated once

LOG_LEVEL=INFO
LOG_FORMAT=json
```

## systemd

Create `/etc/systemd/system/tradingbot.service`:

```ini
[Unit]
Description=Trading bot
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=tradingbot
WorkingDirectory=/home/tradingbot/app
EnvironmentFile=/etc/tradingbot/env
ExecStart=/home/tradingbot/.local/bin/uv run tradingbot
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

And `/etc/systemd/system/tradingbot-api.service`:

```ini
[Unit]
Description=Trading bot Web API
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=tradingbot
WorkingDirectory=/home/tradingbot/app
EnvironmentFile=/etc/tradingbot/env
ExecStart=/home/tradingbot/.local/bin/uv run tradingbot-api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tradingbot.service tradingbot-api.service
```

## Caddy (TLS + reverse proxy)

```bash
sudo cp infra/caddy/Caddyfile.example /etc/caddy/Caddyfile
sudo sed -i 's/tradingbot.example.com/<your-domain>/g; s/ops@example.com/<your-email>/g' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The first request will provision a certificate from Let's Encrypt
automatically. Verify:

```bash
curl -sI https://<your-domain>/healthz
```

## Cloudflare

1. Add the domain in Cloudflare; switch the orange-cloud proxy on.
2. SSL/TLS → mode "Full (strict)".
3. Optional: a WAF rule that blocks all paths except `/`, `/api/*`,
   `/healthz`, and the assets the SPA loads.

## Deploying a new build

```bash
sudo -iu tradingbot
cd ~/app
git pull --ff-only
uv sync --frozen
uv run alembic upgrade head             # if migrations changed
(cd frontend && npm ci && npm run build)
sudo rsync -a --delete frontend/dist/ /var/www/tradingbot/
sudo systemctl restart tradingbot-api.service tradingbot.service
```

Rolling back is the inverse: `git checkout <prev-sha>`, sync, rebuild,
rsync, restart.

## Going live

Switching from paper to live is intentionally explicit. In
`/etc/tradingbot/env`:

```env
IBKR_PORT=4001
LIVE_TRADING=true
```

Then `sudo systemctl restart tradingbot.service`. The bot validates
the live combination at startup (`tradingbot.settings`) and refuses
any other combination. Smoke-test paper for the four weeks required
by Phase 4 before flipping this switch.
