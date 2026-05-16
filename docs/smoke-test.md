# Manual end-to-end smoke test

Run this before any merge to `main` that touches the live execution
path, and once a week against the running paper environment. The
checklist is sequential: each step assumes the previous one passed.

## 0. Prerequisites

- Docker Desktop or Docker Engine running (`docker ps` works).
- IB Gateway running on paper port 4002 with a paper account logged
  in via IBC. The Market Data API subscription is active (without it,
  bars arrive 15 min delayed).
- A Telegram bot token + chat id, OR set `TELEGRAM_BOT_TOKEN=""` to
  skip the Telegram path.

## 1. Infra

```bash
docker compose up -d
docker compose ps                  # all services healthy
```

Expected: 4 containers (`tradingbot-postgres`, `tradingbot-redis`,
`tradingbot-prometheus`, `tradingbot-grafana`), all `running` /
`healthy`.

## 2. Schema and seed

```bash
uv sync
uv run alembic upgrade head        # creates 14 tables + 2 hypertables
uv run python scripts/seed_config.py
```

Expected: `config_policies_seeded version=1` log line. Second run
emits `config_policies_already_seeded` and exits 0.

## 3. Bring up the API and the bot

In separate terminals (or `tmux` panes):

```bash
# Terminal A — API
WEB_API_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
WEB_ADMIN_PASSWORD=ChangeMe-1234 \
uv run tradingbot-api
```

```bash
# Terminal B — bot
uv run tradingbot
```

```bash
# Terminal C — frontend
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## 4. Auth + dashboard

- [ ] Login screen renders.
- [ ] Sign in with `admin / ChangeMe-1234` → redirects to `/dashboard`.
- [ ] Wrong password → red "invalid credentials" error.
- [ ] Dashboard polls `/api/status` every 3 s.
- [ ] Connection badge transitions:
  - immediately shows `unknown` (bot may not have published yet),
  - within ~5 s shows `connected` (IB Gateway up) or `disconnected`
    (no gateway).
- [ ] No open position is displayed.

## 5. TOTP enrollment

- [ ] `/me` → "Enable two-factor" → QR + secret displayed.
- [ ] Scan into authenticator app.
- [ ] Enter the 6-digit code → "Two-factor is enabled" banner.
- [ ] Logout. Login without TOTP → 401 with field "Two-factor code"
  appearing.
- [ ] Login with correct TOTP → dashboard.
- [ ] `/me` → "Disable two-factor" with current code → 204; login
  again works without a code.

## 6. Daily asset

- [ ] `/asset` shows "No asset selected yet".
- [ ] Type `aapl`, click Set → page shows `AAPL` (uppercased).
- [ ] Change to `MSFT` → succeeds.
- [ ] Manually insert a fake open position in Postgres
  (`INSERT INTO positions (id, opened_at, symbol, side, qty,
  avg_entry_price, state) VALUES (gen_random_uuid(), NOW(), 'AAPL',
  'LONG', 100, 150, 'ABIERTA');`) and try to change the asset to
  `TSLA` → 409 with "cannot change active asset while position … is
  open". Roll back the row.

## 7. Config editor

- [ ] `/config` renders v1 in read-only mode.
- [ ] Click Edit → all fields become editable.
- [ ] Try setting `max_daily_loss_usd = 99999` → Save → 422 with red
  field-level error pinning the cap to 5000.
- [ ] Fix the value and save → version increments to v2; `created_by`
  shows the admin username.
- [ ] Audit table check:
  `psql … -c "SELECT action, actor FROM audit_log ORDER BY ts DESC LIMIT 5;"`
  → top row is `config_policy_updated` by `admin`.

## 8. Kill switch — local trip

- [ ] In a Python shell with the bot stopped, run
  `KILL_SWITCH=true uv run tradingbot` (or via the .env).
- [ ] Dashboard shows `kill_switch_tripped=true` with reason
  `startup`.
- [ ] Logs include `kill_switch_tripped_at_startup`.
- [ ] Stop the bot.

## 9. Kill switch — remote trip from the web app

- [ ] Reset env (`KILL_SWITCH=false`). Start the bot.
- [ ] On the dashboard, click the red "Kill switch" button →
  optionally type a reason → "Confirm kill" → button returns to
  idle state.
- [ ] Within ~2 s the dashboard updates: badge flips to `TRIPPED`
  with reason `web:admin[:<reason>]`.
- [ ] Bot logs include `remote_kill_request_received` followed by
  `kill_switch_tripped reason=web:admin…`.

## 10. Telegram

(Skip if no Telegram bot configured.)

- [ ] `/status` from the operator's Telegram chat → reply with
  `IB Gateway: connected` and `Kill switch: TRIPPED` if step 9 left
  it set.
- [ ] `/positions` → either the manual row from step 6 or "No open
  position."
- [ ] `/pnl` → "No P&L recorded yet" (Phase 1 writes nothing here).
- [ ] `/kill` alone → prompt for `/kill confirm`.
- [ ] `/kill confirm` → "Kill switch TRIPPED."

## 11. Survives gateway restart

- [ ] With the bot connected, stop IB Gateway from IBC.
- [ ] Bot logs: `ib_disconnected_reconnecting`, then `ib_connect_failed`
  with exponential backoff.
- [ ] Dashboard `connection_state` transitions `connected` →
  `disconnected` within a couple of seconds (push-based).
- [ ] Restart IB Gateway. Within the backoff window the bot reconnects
  (`ib_connected`) and the dashboard returns to `connected`.

## 12. Survives Postgres restart

- [ ] `docker compose restart postgres`.
- [ ] Bot continues running. API requests during the outage return
  500 briefly, then recover.
- [ ] No data loss: `pnl_daily`, `config_policies`, `active_asset_selections`
  still hold their rows after `docker compose start`.

## 13. Metrics

- [ ] `curl http://localhost:9100/metrics | grep tradingbot_` shows
  `tradingbot_ib_connection_state`, `tradingbot_kill_switch_state`,
  `tradingbot_account_net_liquidation_usd`, `tradingbot_account_buying_power_usd`,
  `tradingbot_account_total_cash_usd`.
- [ ] Grafana at `http://localhost:3000` (admin / admin) sees the
  `Prometheus` datasource.

## 14. Shutdown

- [ ] `Ctrl+C` on the bot terminal. Logs show `shutdown_signaled`,
  then `shutdown_complete` within a few seconds (clean drain of all
  asyncio tasks).
- [ ] `Ctrl+C` on the API. Logs show `api_shutdown`.
- [ ] `docker compose down` (volumes preserved unless `-v` is added).

A green run = the Phase 2 surface is operational against paper. Move
to Phase 4 (≥ 4-week paper forward-test) once strategy code (Phase 3)
is wired in.
