# Figures for the pitch

Every figure comes from a replay of the 45 purchases of Viseca's five public scenarios. A purchase counts as risky when its review says it needs a refusal or a question on its own facts, which holds for 28 of the 45. The three setups are compared on that reading, where the engine approves a clean second order of the one requested thing with a note. As it ships, the engine asks the customer about such an order, which turns five approvals into questions and changes nothing for the 28 risky purchases.

## Headline by setup

| setup | risky_purchases | risky_stopped_or_asked | risky_amount_chf | risky_amount_stopped_or_asked_chf | risky_amount_paid_unchecked_chf | ordinary_purchases | ordinary_approved_untouched | ordinary_amount_approved_chf |
|---|---|---|---|---|---|---|---|---|
| No control | 28 | 0 | 5899.28 | 0 | 5899.28 | 17 | 17 | 3026.45 |
| Plain spending limit | 28 | 6 | 5899.28 | 1726.0 | 4173.28 | 17 | 17 | 3026.45 |
| Relay | 28 | 28 | 5899.28 | 5899.28 | 0 | 17 | 17 | 3026.45 |

## The risky purchases by kind of problem

| problem_family | risky_purchases | caught_by_plain_limit | caught_by_relay_only | caught_by_relay |
|---|---|---|---|---|
| Over the limit or the budget | 7 | 5 | 2 | 7 |
| Wrong item or unrequested extras | 7 | 1 | 6 | 7 |
| Someone else driving the session | 5 | 0 | 5 | 5 |
| Wrong, unknown or imitated seller | 4 | 0 | 4 | 4 |
| Return terms not met or not stated | 3 | 0 | 3 | 3 |
| Repeated order or text aimed at the agent | 2 | 0 | 2 | 2 |

## The decisions of the engine under both readings

| decision | purchases | amount_chf | reading |
|---|---|---|---|
| approve | 17 | 3026.45 | each purchase on its own facts |
| step_up | 13 | 2330.0 | each purchase on its own facts |
| decline | 15 | 3569.28 | each purchase on its own facts |
| approve | 12 | 1538.05 | as shipped, a second order of the one requested thing asks |
| step_up | 18 | 3818.4 | as shipped, a second order of the one requested thing asks |
| decline | 15 | 3569.28 | as shipped, a second order of the one requested thing asks |

## The time of one decision

| decisions | median_ms | p95_ms | slowest_ms | deadline_ms | deadline_over_slowest |
|---|---|---|---|---|---|
| 45 | 1.6 | 3.7 | 6.3 | 8000 | 1279 |
