# Trading bot frontend

React 18 + Vite + TypeScript + TanStack Query + Tailwind. Operator
dashboard for the trading bot. Talks only to the FastAPI backend
(`tradingbot_api`); never to the bot or the DB directly.

## Quickstart

Requires Node 20+ and npm. From this directory:

```bash
npm install
npm run dev          # http://localhost:5173 — proxies /api to :8000
```

The Vite dev server proxies `/api/*` and `/healthz` to the FastAPI
process on `localhost:8000`, so cookies stay same-origin.

## Build

```bash
npm run build        # tsc -b && vite build  -> dist/
npm run preview      # serve dist locally
npm run lint
```

## Layout

```
src/
├── main.tsx, App.tsx, router.tsx
├── index.css                  # Tailwind directives
├── api/
│   ├── client.ts              # fetch wrapper, ApiError
│   ├── types.ts               # mirrors src/tradingbot_api/schemas.py
│   └── queries.ts             # TanStack Query hooks for every endpoint
├── auth/
├── pages/
│   ├── LoginPage              # password + (optional) TOTP
│   ├── DashboardPage          # polls /api/status every 3s
│   ├── AssetPage              # set daily ticker
│   ├── ConfigPage             # read-only view of config_policies
│   └── ProtectedRoute         # session guard (GET /api/me)
└── components/                # Header, Badge, KillButton
```

## Known limitations (this commit)

- TOTP enrollment UI is not implemented; the API endpoints exist and
  the login form does prompt for a code when the server reports
  `totp_required`.
- Config editor is read-only; PUT works in the API.
- No client codegen yet (openapi-typescript). Types are hand-written
  in `src/api/types.ts` and must stay in sync with
  `src/tradingbot_api/schemas.py`.
