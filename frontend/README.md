# Relay frontend

The customer interface has three screens, which are the policy review, the approval inbox and the decision log. It is presented as a Viseca feature powered by Relay and is structured as an independently deployable module for later integration into the Viseca one app. It starts in offline mock mode, so it runs without the backend.

## Run locally

```text
npm install
npm run dev
```

Use `VITE_USE_MOCK_API=false` to connect to the backend. `VITE_API_BASE_URL` may be left empty when Vite proxies a local backend on port 8000, or set to the deployed backend origin.

## Backend contract

The frontend calls these routes through `src/api.ts`.

- `POST /api/policy/compile`
- `POST /api/policy/draft`, then `POST /api/policy/confirm`
- `PATCH` and `DELETE /api/policy`
- `POST /api/run/start`
- `GET /api/status`, `/api/decisions` and `/api/pending`
- `POST /api/resolve/{authorization_id}`
- `GET /api/stream` as a server-sent event stream

The TypeScript wire types are in `src/types.ts`. The decisions and pending routes return backend decision records. The adapter exposes their nested `DecisionTrace` values to the screens and uses `human_deadline_at` for the approval countdown. Each record also carries the backend's `trust_score`, which the adapter puts on the trace as `trust`. `src/TrustGauge.tsx` draws it as a tachometer on an inbox card, as a bar in a log row and as the line-by-line calculation in an expanded row. The frontend never computes the score. The method is in `docs/trust-score.md`.

The event stream uses named `status`, `decision`, `resolution`, `run`, `mandate` and `reset` events. The frontend handles the first three directly and reloads the inbox and the log from the backend whenever the stream reconnects. Repeated records are upserted by live authorization ID.

The browser talks only to this REST and SSE boundary. It contains no platform key, no direct calls to the Viseca API, no policy engine and no purchase decision logic.

See `DEMO.md` for the offline rehearsal and the recovery steps.

## Checks

```text
npm test
npm run build
```
