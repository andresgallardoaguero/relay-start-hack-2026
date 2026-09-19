# Business and jury figures for the deck

All figures refer to the 45 purchases of Viseca's five public scenarios.

## What Viseca measured on its own server

These come from Viseca's jury screen on 19 September 2026, after the latest completed run of each scenario.

| Figure | Value |
|---|---|
| Deviations from Viseca's expected decisions | none, on all five scenarios |
| Unwanted purchases stopped | 100 percent |
| Normal purchases approved | 100 percent |
| Share of purchases where the customer is asked | 28.9 percent, which is 13 of 45 |
| Decisions that missed the deadline of 8 seconds | 0 |
| Median time from request to answer, including the network | 76 ms |

Suggested line for the results slide. On Viseca's own judging data Relay has zero deviations, every unwanted purchase stopped and every normal purchase approved, in 76 milliseconds.

## What an agent purchase is worth to the issuer

This is an illustration with assumed figures, not a forecast. It shows the direction and the size of the effect, and Viseca's own figures belong in its place.

| Setup | Fee income on the 45 purchases | Expected dispute cost | Net per agent purchase |
|---|---|---|---|
| No control | CHF 134 | CHF 586 | minus CHF 10.06 |
| Plain spending limit | CHF 108 | CHF 483 | minus CHF 8.33 |
| Relay | CHF 45 | CHF 0 | plus CHF 1.01 |

Suggested headline. On this test set an agent purchase costs the issuer CHF 8 with a spending limit and earns CHF 1 with Relay.

How it is computed. An approved purchase earns a fee of 1.5 percent of its amount. An approved purchase that should have been stopped or questioned also carries an expected dispute cost, which is a dispute probability for its kind of problem times CHF 30 of handling plus 25 percent of the amount written off. The probabilities run from 5 percent for a small overshoot of the limit to 70 percent for a seller that imitates a known shop. An ordinary purchase that is wrongly stopped costs CHF 10, which happens under none of the three setups. A purchase that is stopped or put to the customer earns and costs nothing. The net of each setup is divided by 45.

Why the fee income of Relay is lower. 28 of the 45 test purchases are problems, so Relay approves fewer purchases than a spending limit. The fees it gives up are CHF 63, and the dispute cost it avoids is CHF 483.

The script is `scripts/business_impact.py`, and its full report with every assumption is `outputs/replay/business_impact.md`.

## The protection figures behind the charts

| Figure | No control | Plain spending limit | Relay |
|---|---|---|---|
| Risky purchases stopped or put to the customer | 0 of 28 | 6 of 28 | 28 of 28 |
| Risky spend kept from going through unchecked | CHF 0 | CHF 1,726 | CHF 5,899 |
| Ordinary purchases approved without friction | 17 of 17 | 17 of 17 | 17 of 17 |

The charts are in this folder, and the tables behind them are in `pitch_figures.md`.
