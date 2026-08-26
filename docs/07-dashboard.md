# Dashboard

Next.js 16 (App Router, Turbopack), TypeScript, Tailwind CSS v4, TanStack Query, Recharts. Every
page is a Client Component — there is no server-side data fetching; all pages poll the FastAPI
backend directly over `NEXT_PUBLIC_API_BASE_URL`.

## Shell

`components/shell/app-shell.tsx` wraps every page with a left sidebar (`sidebar.tsx`) and a
persistent status strip (`status-strip.tsx`) showing Signal Source / Broker / Mode / Auto Trading /
Balance — visible on every screen, not just the Overview page, since knowing the bot's state while
looking at Trades or Settings is exactly when it matters most.

## Screens

| Route | Purpose |
|---|---|
| `/` | Overview — performance stat cards, recent trades, recent events (PROJECT_SPEC.md section 15.1) |
| `/trades` | Full trade history with status/result/date filters and every column from section 15.3 |
| `/signals` | Every parsed signal; expand a row to see the exact raw message text the parser saw — the primary parser-debugging tool (section 15.4) |
| `/analytics` | Win rate, average win/loss, best/worst trade, a cumulative profit-curve chart, and a wins-vs-losses-by-day chart (section 15.5) |
| `/settings` | All dashboard-controlled trading rules; switching to LIVE requires typing `ENABLE LIVE TRADING` (section 15.2 / 28); includes a live Pip Preview (Target/Symbol Point/Pip Size/Price Distance/Example Entry/Calculated TP) that updates as Target Pips is edited (section 10 transparency requirement) |
| `/logs` | The full audit trail, filterable by severity (section 15.6) |
| `/simulation` | Drives the entire pipeline from the browser — mock messages, a controllable price, TP/SL triggers, resets (section 15.7, mandatory before real integrations) |

## Data layer

- `types/api.ts` — TypeScript interfaces mirroring every backend Pydantic response schema.
  Money/price fields are typed as `string` because the backend serializes `Decimal` as a normalized
  decimal string (`app/core/decimals.py::CleanDecimal`), not a float — this avoids floating-point
  drift for anything that touches money.
- `lib/api.ts` — a thin `fetch` wrapper; throws `ApiError` with the backend's `detail` message on
  a non-2xx response, and attaches `X-API-Key` from `localStorage` when present.
- `hooks/*.ts` — one TanStack Query hook per resource (`use-trades`, `use-signals`,
  `use-settings`, `use-simulation`, ...). Mutations invalidate the relevant query keys on success
  so, e.g., sending a mock message on the Simulation page immediately refreshes Trades, Signals,
  Logs, and the dashboard summary.
- Polling interval is 5s by default (`app/providers.tsx`), 3s for the Simulation page's live
  market state — this is a dashboard for a single operator, not a high-frequency trading UI, so
  polling was chosen over websockets for this phase.
- `hooks/use-settings.ts::usePipPreview` calls `GET /api/settings/pip-preview` — the Settings page
  never computes pip math itself. `hooks/use-debounced-value.ts` debounces the Target Pips input
  (400ms) before that hook's query key changes, so editing the field doesn't fire a request per
  keystroke.

## Design system

Dark, trading-terminal-inspired UI (`app/globals.css` theme tokens): a muted antique-gold accent
(`--gold`, tied to the literal subject — gold bullion — rather than an arbitrary brand color),
IBM Plex Mono for all numeric/data display (`.font-data`, tabular figures), Space Grotesk for UI
text. Hand-built primitives live in `components/ui/` (Card, Badge, Button, Input/Select/Textarea,
Switch, StatCard, Empty/Loading/Error states) rather than a component library — deliberately
simple, since the dashboard's job is legibility of financial data, not visual flourish.

## Running it

```bash
cd dashboard
npm install
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_BASE_URL at the backend
npm run dev
```

`npm run build` produces a fully static-shell production build (all 7 routes prerender — there is
no server-rendered data, everything fetches client-side after hydration).
