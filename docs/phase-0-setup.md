# Phase 0 — Manual setup (operator-driven)

Phase 0 is everything that has to happen between Interactive Brokers
and your laptop **before any code runs**. It is the longest single
phase by calendar time (account approval + paperwork + market data
subscription) and the one I cannot do for you. Work through it
sequentially: each step assumes the previous one is confirmed.

## 0.1 IBKR Pro account (1–3 business days)

- [ ] Open an account at <https://www.interactivebrokers.com>.
  Choose **IBKR Pro** explicitly (NOT Lite). Lite has a different
  commission structure and routes orders through IBKR for payment
  for order flow — we model and depend on Pro tiered commissions
  in the sizing math.
- [ ] Complete the Pattern Day Trader (PDT) acknowledgements. PDT
  applies to every scalping account by default.
- [ ] Fund the account with the target capital. CLAUDE.md §1 and
  §7 are sized for **$150k USD**; the bot still works at lower
  capital but the risk caps in `config/defaults.yaml` will need
  to be downscaled.
- [ ] Wait for the account to be marked **Approved**. You will
  receive an email; you can also check the status from the IBKR
  portal under "Account Management".

**Verification:** log into IBKR Client Portal, top right shows
"IBKR Pro" and the account is in green/active status.

## 0.2 Market data subscription (~$15/month)

Without this the bot receives **delayed (15 min) bars**, which is
unusable for scalping. Inside the Client Portal:

- [ ] Subscribe to: **US Securities Snapshot and Futures Value
  Bundle** ($10/month) AND **NYSE (Network A/CTA)** + **NASDAQ
  (Network C/UTP)** Top of Book. Combined billing is around
  $14–16/month total.
- [ ] Sign the **Market Data API Acknowledgement** (Settings →
  User Settings → Trading Permissions → Market Data
  Subscriptions). Without this signature the API returns a
  permissions error even with the subscription active.

**Verification:** in the Client Portal, "Market Data Subscriptions"
shows both bundles as ACTIVE with a recent billing date.

## 0.3 Paper trading account

- [ ] In Client Portal → Settings → Account Settings →
  "Paper Trading Account", request a paper account. It is created
  automatically; the username is `${live_username}` and a
  separate password is emailed.
- [ ] Note the paper account number — it starts with `DU…`. This
  is the value of `IBKR_ACCOUNT` in `.env`.
- [ ] **Important:** paper market data is provided free using your
  live subscriptions. You must have completed 0.2 above first.

**Verification:** log in to Client Portal with your paper
credentials. The dashboard shows ~$1M virtual cash.

## 0.4 IB Gateway

We use **Gateway**, not TWS, because it has a much smaller memory
footprint and exposes the same API.

- [ ] Download the **Stable** (not Latest) build of IB Gateway
  from <https://www.interactivebrokers.com/en/trading/ibgateway-stable.php>.
  Choose your OS.
- [ ] Install it.
- [ ] First-time launch: log in with your **paper** credentials.
  The Gateway window shows the paper account number.
- [ ] In Gateway → Configure → API → Settings:
  - "Enable ActiveX and Socket Clients" = **ON**
  - "Read-Only API" = **OFF** (we need to place orders)
  - "Allow connections from localhost only" = **ON**
  - Socket port = **4002** (paper). The live port is 4001; we
    will not touch that until Phase 5.
  - Master API client ID = blank
  - Trusted IPs = `127.0.0.1`

**Verification:** in a terminal, run `nc -zv 127.0.0.1 4002`. It
should report a successful connection.

## 0.5 IBC (auto-login + 2FA recovery)

IB Gateway forces a daily restart and requires 2FA on every login.
IBC automates the password entry and walks you through the 2FA
prompt on your phone.

- [ ] Download IBC from <https://github.com/IbcAlpha/IBC/releases>.
  Pick the version that matches your Gateway build.
- [ ] Unpack into `~/IBC/` (Linux/macOS) or
  `C:\IBC\` (Windows).
- [ ] Copy the example config:
  `cp ~/IBC/config.ini.example ~/IBC/config.ini`. Edit
  `IbLoginId`, `IbPassword`, `TradingMode=paper`, and the path to
  the Gateway install.
- [ ] First run: `~/IBC/scripts/ibcstart.sh 1019` (replace 1019
  with your Gateway build number). On the first launch, IBKR
  Mobile pushes a 2FA prompt — accept it.
- [ ] Verify IBC re-logs you in after the daily reset:
  Gateway closes itself around 23:45 ET nightly; IBC should
  bring it back within a couple of minutes.

**Verification:** restart Gateway manually. IBC should auto-relog
without you typing anything.

## 0.6 First connection smoke test

Once 0.1–0.5 are green, run this script (`scripts/smoke_ibkr.py`,
shipped with the repo). It connects, prints the account summary,
and exits. It performs no orders.

```bash
cp .env.example .env
# Fill in IBKR_ACCOUNT, IBKR_HOST=127.0.0.1, IBKR_PORT=4002
uv sync
uv run python scripts/smoke_ibkr.py
```

Expected output (your DU number and balance):

```
connected to 127.0.0.1:4002 as DU0000000
NetLiquidation     999934.55 USD
BuyingPower       3999738.20 USD
TotalCashValue     999934.55 USD
```

A successful run means: account active, market data subscription
recognized, Gateway listening, IBC keeping it alive. You are ready
for Phase 1 onwards.

## 0.7 Telegram bot (optional but recommended)

- [ ] Talk to <https://t.me/BotFather> and create a new bot.
  Save the token; this is `TELEGRAM_BOT_TOKEN`.
- [ ] Start a chat with your bot. Send any message.
- [ ] Visit
  `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser
  and find your `chat.id`. This is `TELEGRAM_CHAT_ID`.
- [ ] Add both to `.env`.

**Verification:** `uv run tradingbot` (Phase 1 binary). From the
chat, send `/status`; the bot should reply.

## 0.8 Common pitfalls

| Symptom | Likely cause |
|---|---|
| `ConnectionRefusedError` from the bot | Gateway is not running, OR the port is wrong (4002 paper / 4001 live), OR Trusted IPs does not include 127.0.0.1 |
| `Market data is not subscribed` errors | 0.2 not completed, OR the Market Data API ack not signed |
| Bars arrive but 15 minutes late | Same — subscription / ack missing |
| Gateway closes daily and stays down | IBC not configured or wrong build number |
| 2FA times out on every launch | Open IBKR Mobile and enable "Allow notifications" |
| `Read-Only API` error on order submit | Toggle is ON in Gateway → API → Settings; turn it OFF |

When all checkboxes in 0.1–0.6 are ticked and the smoke script
prints the account summary, ping me and we run the full Phase 1
smoke described in `docs/smoke-test.md`.
