# Frontend demo operator guide

## Safe rehearsal

1. Start the backend with `LEASH_MODE=offline` and confirm `GET /api/status` says `"leash_mode": "offline"`.
2. Start the frontend with `VITE_USE_MOCK_API=false npm run dev`.
3. Confirm the blue banner says **OFFLINE SIMULATOR** before starting a scenario.
4. Confirm a policy, start `SCEN0001`, and let the interface return to the approval inbox.
5. Answer each question within the visible 120-second window using `assumed_customer_answer` in `data/processed/purchase_verdicts.csv`.
6. Open the decision log and expand a row to show its five guard families, evidence and deadline margin.

## Recovery

- If the stream disconnects, keep the page open. It reconnects automatically and reloads the inbox and log.
- If the interface cannot resolve a purchase, use the backend's documented `POST /api/resolve/{live_authorization_id}` fallback immediately.
- If the backend becomes unavailable, use **Try again** after it restarts. Existing records remain in the backend store.
- Never switch to live mode merely to diagnose the interface. Reproduce the issue in offline mode.
