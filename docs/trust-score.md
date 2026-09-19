# The trust score

One number from 0 to 100 for every decided purchase, so the customer sees at a glance how far Relay trusts it without reading every check. It is derived from the decision record only, from the verdicts and the signals of the 23 guards, and every point it takes away is listed. It is not a model output and it has no learned weights.

The score never replaces the decision. The decision is `approve`, `decline` or `step_up`, as the platform expects. The score summarizes the record behind that decision, and it travels with the decision as evidence in the same format the platform documents.

## Where it is calculated

| What | Where |
|---|---|
| The calculation | `backend/app/trust/score.py`, function `score_trace(trace, uncertainty_policy)`. The table of points is at the top of the file |
| When it runs | `backend/app/worker/loop.py`, `handle_envelope()`, right after `decide()` and before the record is stored and the answer is sent |
| Where it is kept | The decision record, field `trust_score`, column `trust_score_json` of the store in `backend/app/state/store.py` |
| How it reaches the screens | `GET /api/decisions`, `GET /api/pending` and the `decision` and `resolution` events of the stream carry the record with `trust_score`. `frontend/src/api.ts` puts it on the screen trace as `trust` |
| Where it is drawn | `frontend/src/TrustGauge.tsx`. A tachometer on every inbox card, a bar in every log row, and the calculation line by line when a log row is expanded |
| How it reaches the platform | Three evidence items in the decision body, see below. `TRUST_SCORE_IN_EVIDENCE=false` in `backend/.env` leaves them out |
| The report | `scripts/trust_report.py` scores a replay or audit file and writes a table and a visualization into `outputs/trust/` |
| The tests | `tests/test_trust_score.py` for the calculation, `tests/test_trust_in_records.py` for the record, the answer, the store and the web API |

## How it is calculated

The score starts at 100. Every finding takes points away. Nothing adds points. That mirrors the engine's own rule that severity can only rise, so the score can only fall.

1. **The finding that decided the purchase.** The first guard, in execution order, whose measured severity equals the decision. A refusal takes 70, a question takes 40, an approval takes nothing. The severity of a guard is measured exactly as the aggregation measures it, through `measure_result_severity` in `backend/app/engine/aggregate.py`, so an `UNCERTAIN` verdict follows the customer's uncertainty policy here as well.
2. **Further findings.** Every other guard above approve takes 5.
3. **Doubts.** A guard that could not settle its check and that the customer's policy resolved in favour of the purchase, which only happens under `uncertainty_policy: approve`, takes 10. The purchase is approved, and the doubt stays visible.
4. **Signals.** A behavioral signal a guard noticed without raising the decision takes 5 when the guard called it normal and 10 when it called it strong. A signal on the guard that decided the purchase is not counted a second time.
5. **The cap.** Steps 2 to 4 together take at most 25. The deductions are listed in order, the first 25 points count, and the rest are listed with 0 points and marked as capped. This keeps the score inside the band of its decision.

| Decision | Score range | Band | Label on screen |
|---|---|---|---|
| `approve` | 75 to 100 | `trusted` | Trusted |
| `step_up` | 35 to 60 | `review` | Needs your decision |
| `decline` | 5 to 30 | `blocked` | Blocked |
| No check evaluated | none | `not_assessed` | Not assessed |

The gauge colors the scale green from 70, amber from 35 and red below, and reads those two thresholds from the record under `scale`, not from a constant of its own.

**Not assessed.** A record without a single evaluated or failed check has no score. That is the timeout fallback, a failed engine, a message that could not be read, a trace in which every guard is still a placeholder, and a trace in which no check applied. The summary says which of these it was. A low number would read as a judgement, and nothing was judged. A failed check does count, because a failure is a finding and never a pass.

**What counts as evaluated.** A guard that gave a verdict of its own, `PASS`, `STEP_UP`, `DECLINE` or `UNCERTAIN`, was evaluated. A guard that answered `SKIP` with the reason `GUARD_NOT_BUILT` is a placeholder and counts as not built. A guard that answered `SKIP` for a reason of its own, such as a merchant type the instruction does not name, did not apply, and is neither a gap nor a check. A guard that raised an exception counts as failed. The record carries `coverage` with the total, the evaluated, the not applicable, the not built and the failed checks, plus `coverage_share`, the evaluated checks over the checks that applied, so a placeholder or a failure lowers it and a check that did not apply does not. The screen says "8 of 22 applicable checks evaluated" next to the score.

### Worked examples from the public purchases

From the replay of the 45 public purchases with the seven guards that exist today, which are the limit per order, the period budget, the split order, the category scope, the add-on, the merchant type and the item and shop consistency, under "ask me when uncertain".

| Purchase | Decision | Calculation | Score |
|---|---|---|---|
| AU0001, a grocery item for CHF 20 | approve | 100, nothing taken | 100 |
| AU0004, CHF 126 against a limit of 120 | step_up | 100 − 40 per order limit asked (small overshoot) | 60 |
| AU0006, a second order at the same shop minutes later | step_up | 100 − 40 split order asked (split order suspected) | 60 |
| AU0007, a cosmetics line in a grocery basket | step_up | 100 − 40 category scope asked (off scope item) − 5 addon asked (unrequested addon) − 5 item shop consistency signal | 50 |
| AU0010, CHF 138 against a limit of 120, on a full week | decline | 100 − 70 per order limit refused (over per order limit) − 5 period budget (over period limit) | 25 |
| AU0022, a shop of the wrong kind | decline | 100 − 70 merchant type refused (merchant type mismatch) − 5 item shop consistency signal | 25 |
| AU0041, over the limit with an extra and an item outside the scope | decline | 100 − 70 per order limit refused − 5 category scope − 5 addon − 5 item shop consistency | 15 |

A question the customer then approves keeps its score. The screen shows the customer's answer as the outcome and the score as Relay's own reading before the answer.

## The record

`trust_score` on every decision record, as JSON.

```json
{
  "version": "1",
  "score": 60,
  "band": "review",
  "label": "Needs your decision",
  "summary": "Per order limit asked for your decision (small overshoot).",
  "scale": {"start": 100, "trusted_from": 70, "review_from": 35},
  "deductions": [
    {
      "kind": "leading_finding",
      "guard_id": "per_order_limit",
      "family": "Spending limits",
      "verdict": "STEP_UP",
      "reason_code": "SMALL_OVERSHOOT",
      "signal_name": null,
      "points": 40,
      "capped": false,
      "detail": "Per order limit asked for your decision (small overshoot)"
    }
  ],
  "families": [
    {"family": "Spending limits", "strictest_verdict": "STEP_UP", "points_lost": 40},
    {"family": "Item and terms", "strictest_verdict": "PASS", "points_lost": 0},
    {"family": "Seller", "strictest_verdict": "SKIP", "points_lost": 0},
    {"family": "Session", "strictest_verdict": "SKIP", "points_lost": 0},
    {"family": "Repeats and manipulation", "strictest_verdict": "SKIP", "points_lost": 0}
  ],
  "coverage": {"checks_total": 23, "checks_evaluated": 8, "checks_not_applicable": 1, "checks_not_built": 14, "checks_failed": 0, "coverage_share": 0.364},
  "basis": {"decision": "step_up", "uncertainty_policy": "ask", "engine_version": "relay-0.1.0", "trace_version": "1", "llm_mode": "off"}
}
```

`deductions.kind` is one of `leading_finding`, `further_finding`, `doubt` and `signal`. `score` is `null` and `band` is `not_assessed` when no check was evaluated. The sum of `points` over the deductions is always `scale.start` minus `score`.

## In the answer to the platform

The platform takes `reason_codes`, `customer_message`, `evidence` and `engine_version` next to the decision. The score is written into `evidence` as three items with the same five fields as the evidence of every guard, so the answer keeps the documented format.

```json
[
  {"fact": "trust_score", "value": 60, "comparator": ">=", "threshold": 70, "source": "relay.trust_score.v1"},
  {"fact": "trust_band", "value": "review", "comparator": null, "threshold": null, "source": "relay.trust_score.v1"},
  {"fact": "trust_deductions", "value": "per_order_limit SMALL_OVERSHOOT -40", "comparator": null, "threshold": null, "source": "relay.trust_score.v1"}
]
```

The threshold of the first item is the line from which a purchase is trusted. A repeated delivery answered from the store sends the same three items. Should the live platform refuse evidence it does not know, `TRUST_SCORE_IN_EVIDENCE=false` leaves the three items out and changes nothing else. The score is still computed, stored and shown.

## How it behaves as the engine grows

The score reads the record and knows no guard by name, so every guard that is built enters it by itself.

- A guard that moves from placeholder to built stops counting as not built and starts counting with its verdict. The coverage line on screen counts up.
- The session guards for device, velocity, country and the usual size record signals. Each signal takes 5 or 10 points on an approval, up to the cap, and once the session integrity guard raises the decision on them, that guard is the leading finding and its signals are not counted again.
- The ledger guards for the period budget, the split order, the duplicate and the already-bought note answer with verdicts like every other guard. A note alone takes nothing.
- The model part changes nothing in the calculation. `basis.llm_mode` records the mode the trace carried, and a fact the model could not extract shows as an `UNCERTAIN` verdict of the guard that needed it, which the score reads like any other.
- A change of the points or of the bands is a new `version`, so a stored record always says which table produced it.

## The report

```text
backend/.venv/bin/python scripts/trust_report.py
backend/.venv/bin/python scripts/trust_report.py --input outputs/replay/all_engine_<stamp>.jsonl
backend/.venv/bin/python scripts/trust_report.py --input outputs/audit/run_<id>.jsonl
```

Without an option the script takes the newest engine replay in `outputs/replay/`. It writes `outputs/trust/trust_report_<stamp>.md` with one row per purchase and the calculation of each, and `outputs/trust/trust_report_<stamp>.html` with a bar per purchase on the scale, colored by band, with the deductions next to it. The console prints the count per band and the mean score per decision.
