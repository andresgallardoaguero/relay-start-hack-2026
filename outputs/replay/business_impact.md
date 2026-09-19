# Business impact — three-way comparison

Generated 2026-09-19 11:30 UTC.

## Presentation headline

**On this synthetic test set, per 1,000 agent purchases, Relay has about CHF 9,336.63 more expected issuer value than a plain spending limit.**

This is a scenario-model result, not a forecast of real customer behaviour. Replace the assumptions below with Viseca figures before presenting it as a business estimate.

## Measured protection KPI

| Setup | Purchases approved | Approved amount | Purchases needing intervention: stopped or questioned | Ordinary purchases wrongly stopped or questioned |
| --- | ---: | ---: | ---: | ---: |
| No control | 45 of 45 | CHF 8,925.73 | 0 of 28 | 0 of 17 |
| Plain spending limit | 39 of 45 | CHF 7,199.73 | 6 of 28 | 0 of 17 |
| Relay | 17 of 45 | CHF 3,026.45 | 28 of 28 | 0 of 17 |

A purchase needs intervention when its reviewed `decision_on_own_facts` is `decline` or `step_up`. Both a decline and a question count as an intervention; an approval does not.

## Assumption-based business KPI

| Setup | Expected issuer value on 45 purchases | Expected issuer value per 1,000 purchases | Difference from plain spending limit |
| --- | ---: | ---: | ---: |
| No control | −CHF 452.55 | −CHF 10,056.65 | −CHF 1,728.83 |
| Plain spending limit | −CHF 374.75 | −CHF 8,327.81 | CHF 0.00 |
| Relay | CHF 45.40 | CHF 1,008.82 | CHF 9,336.63 |

Value formula per purchase:

- approved purchase: transaction amount × fee rate;
- purchase needing intervention but approved: fee income minus dispute share × (handling cost + transaction amount × write-off share);
- ordinary purchase stopped or questioned: false-block cost, with no fee income;
- purchase needing intervention and stopped or questioned: zero incremental value or cost.

The 45-purchase value is scaled by `1000 / 45`. A question is conservatively treated as preventing payment in this simple model; production conversion after a customer answer is not available in the synthetic data.

## Financial assumptions — replace with Viseca figures

| Setting | Assumption |
| --- | ---: |
| `FEE_RATE` | 1.5% of approved amount |
| `DISPUTE_HANDLING_COST_CHF` | CHF 30.00 per dispute |
| `WRITE_OFF_SHARE` | 25% of disputed amount |
| `FALSE_BLOCK_COST_CHF` | CHF 10.00 beyond the lost fee |

| Problem kind | Assumed dispute share |
| --- | ---: |
| `duplicate` | 35% |
| `familiarity_card_or_person` | 10% |
| `injection` | 30% |
| `lookalike_merchant` | 70% |
| `missing_fact` | 10% |
| `new_device` | 20% |
| `off_purpose_item` | 15% |
| `order_terms` | 10% |
| `over_order_limit` | 15% |
| `session_anomaly` | 50% |
| `small_overshoot` | 5% |
| `split_order` | 15% |
| `unfamiliar_merchant` | 10% |
| `unrequested_addon` | 40% |
| `wrong_attribute` | 10% |
| `wrong_item` | 15% |
| `wrong_merchant_type` | 20% |

## Inputs

- No control: `outputs/replay/all_none_20260919-004448.jsonl`
- Plain spending limit: `outputs/replay/all_limit_only_20260919-082917.jsonl`
- Relay: `outputs/replay/all_engine_20260919-112319.jsonl`
- Reviewed verdicts: `data/processed/purchase_verdicts.csv`
