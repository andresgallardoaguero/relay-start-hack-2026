# Script: business_impact.py
# Purpose: Turn replay decisions into a transparent, assumption-based business impact comparison for the pitch
# Author: Viktor Vantsev
# Date: September 2026

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path









#### Step 1: Keep every financial assumption visible in one place ####

# These are illustrative assumptions, not Viseca facts. Replace them with figures supplied by Viseca.
FEE_RATE = Decimal("0.015")
DISPUTE_SHARE = {
    "small_overshoot": Decimal("0.05"),
    "split_order": Decimal("0.15"),
    "off_purpose_item": Decimal("0.15"),
    "over_order_limit": Decimal("0.15"),
    "wrong_attribute": Decimal("0.10"),
    "order_terms": Decimal("0.10"),
    "missing_fact": Decimal("0.10"),
    "wrong_item": Decimal("0.15"),
    "unrequested_addon": Decimal("0.40"),
    "wrong_merchant_type": Decimal("0.20"),
    "new_device": Decimal("0.20"),
    "session_anomaly": Decimal("0.50"),
    "unfamiliar_merchant": Decimal("0.10"),
    "duplicate": Decimal("0.35"),
    "lookalike_merchant": Decimal("0.70"),
    "injection": Decimal("0.30"),
    "familiarity_card_or_person": Decimal("0.10"),
}
DISPUTE_HANDLING_COST_CHF = Decimal("30.00")
WRITE_OFF_SHARE = Decimal("0.25")
FALSE_BLOCK_COST_CHF = Decimal("10.00")









#### Step 2: Locate the inputs and describe one evaluated purchase ####

REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
REPLAY_FOLDER = REPOSITORY_FOLDER / "outputs" / "replay"
VERDICT_TABLE_PATH = REPOSITORY_FOLDER / "data" / "processed" / "purchase_verdicts.csv"
DEFAULT_OUTPUT_PATH = REPLAY_FOLDER / "business_impact.md"
SETUPS = (
    ("No control", "all_none_*.jsonl"),
    ("Plain spending limit", "all_limit_only_*.jsonl"),
    ("Relay", "all_engine_*.jsonl"),
)
ALLOWED_DECISIONS = {"approve", "decline", "step_up"}
MONEY_PLACES = Decimal("0.01")


@dataclass(frozen = True)
class EvaluatedPurchase:
    source_authorization_id: str
    amount_chf: Decimal
    decision: str
    expected_decision: str
    problem_kind: str

    @property
    def needs_intervention(self):
        return self.expected_decision != "approve"

    @property
    def was_intervened(self):
        return self.decision != "approve"


@dataclass(frozen = True)
class SetupResult:
    name: str
    purchase_count: int
    approved_count: int
    approved_chf: Decimal
    needed_intervention_count: int
    correctly_intervened_count: int
    ordinary_count: int
    wrongly_intervened_count: int
    sample_value_chf: Decimal

    @property
    def value_per_1000_chf(self):
        return self.sample_value_chf * Decimal("1000") / Decimal(self.purchase_count)









#### Step 3: Read and validate the replay and verdict files ####

def latest_replay(pattern):
    candidates = sorted(REPLAY_FOLDER.glob(pattern))
    if not candidates:
        raise SystemExit("No replay matches " + pattern + " under " + str(REPLAY_FOLDER) + ". Run scripts/replay.py first.")
    return candidates[-1]


def resolve_input_path(path_text, fallback_pattern):
    if path_text is None:
        return latest_replay(fallback_pattern)
    path = Path(path_text)
    return path if path.is_absolute() else REPOSITORY_FOLDER / path


def read_verdicts(path):
    with path.open("r", encoding = "utf-8", newline = "") as verdict_file:
        rows = list(csv.DictReader(verdict_file))
    required_columns = {"authorization_id", "problem_kind", "decision_on_own_facts"}
    missing_columns = required_columns - set(rows[0] if rows else ())
    if missing_columns:
        raise SystemExit("Verdict table is missing columns: " + ", ".join(sorted(missing_columns)))
    verdicts = {row["authorization_id"]: row for row in rows}
    if len(verdicts) != len(rows):
        raise SystemExit("Verdict table contains a duplicate authorization_id.")
    missing_dispute_shares = sorted({row["problem_kind"] for row in rows if row["decision_on_own_facts"] != "approve"} - set(DISPUTE_SHARE))
    if missing_dispute_shares:
        raise SystemExit("DISPUTE_SHARE has no assumption for: " + ", ".join(missing_dispute_shares))
    return verdicts


def read_replay(path, verdicts):
    purchases = []
    seen_identifiers = set()
    for line_number, raw_line in enumerate(path.read_text(encoding = "utf-8").splitlines(), start = 1):
        if raw_line.strip() == "":
            continue
        try:
            record = json.loads(raw_line)["record"]
            source_id = record["source_authorization_id"]
            amount_chf = Decimal(str(record["billing_amount_chf"]))
            decision = record["decision"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"Cannot read {path} line {line_number}: {error}") from error
        if source_id in seen_identifiers:
            raise SystemExit(str(path) + " contains duplicate purchase " + source_id + ".")
        if source_id not in verdicts:
            raise SystemExit(str(path) + " contains purchase " + source_id + " with no reference verdict.")
        if decision not in ALLOWED_DECISIONS:
            raise SystemExit(str(path) + " contains unknown decision " + str(decision) + ".")
        if amount_chf < 0:
            raise SystemExit(str(path) + " contains a negative amount for " + source_id + ".")
        verdict = verdicts[source_id]
        purchases.append(EvaluatedPurchase(
            source_authorization_id = source_id,
            amount_chf = amount_chf,
            decision = decision,
            expected_decision = verdict["decision_on_own_facts"],
            problem_kind = verdict["problem_kind"],
        ))
        seen_identifiers.add(source_id)
    missing_purchases = sorted(set(verdicts) - seen_identifiers)
    if missing_purchases:
        raise SystemExit(str(path) + " is missing purchases: " + ", ".join(missing_purchases))
    return purchases


def validate_same_test_set(purchases_by_setup):
    first_name, first_purchases = next(iter(purchases_by_setup.items()))
    expected = {purchase.source_authorization_id: purchase.amount_chf for purchase in first_purchases}
    for setup_name, purchases in purchases_by_setup.items():
        observed = {purchase.source_authorization_id: purchase.amount_chf for purchase in purchases}
        if observed != expected:
            raise SystemExit(setup_name + " and " + first_name + " do not contain the same purchases and CHF amounts.")









#### Step 4: Apply the four-outcome value model ####

def purchase_value(purchase):
    if purchase.decision == "approve":
        fee_income = purchase.amount_chf * FEE_RATE
        if not purchase.needs_intervention:
            return fee_income
        dispute_cost = DISPUTE_SHARE[purchase.problem_kind] * (
            DISPUTE_HANDLING_COST_CHF + purchase.amount_chf * WRITE_OFF_SHARE
        )
        return fee_income - dispute_cost
    if not purchase.needs_intervention:
        return -FALSE_BLOCK_COST_CHF
    return Decimal("0")


def evaluate_setup(name, purchases):
    approved = [purchase for purchase in purchases if purchase.decision == "approve"]
    needing_intervention = [purchase for purchase in purchases if purchase.needs_intervention]
    ordinary = [purchase for purchase in purchases if not purchase.needs_intervention]
    return SetupResult(
        name = name,
        purchase_count = len(purchases),
        approved_count = len(approved),
        approved_chf = sum((purchase.amount_chf for purchase in approved), Decimal("0")),
        needed_intervention_count = len(needing_intervention),
        correctly_intervened_count = sum(purchase.was_intervened for purchase in needing_intervention),
        ordinary_count = len(ordinary),
        wrongly_intervened_count = sum(purchase.was_intervened for purchase in ordinary),
        sample_value_chf = sum((purchase_value(purchase) for purchase in purchases), Decimal("0")),
    )









#### Step 5: Write a slide-ready report without presenting assumptions as facts ####

def money(amount):
    rounded = amount.quantize(MONEY_PLACES, rounding = ROUND_HALF_UP)
    sign = "−" if rounded < 0 else ""
    return sign + "CHF " + f"{abs(rounded):,.2f}"


def decimal_text(value):
    return format(value.normalize(), "f")


def relative_path(path):
    try:
        return str(path.relative_to(REPOSITORY_FOLDER))
    except ValueError:
        return str(path)


def build_report(results, input_paths, written_at):
    result_by_name = {result.name: result for result in results}
    relay_advantage = result_by_name["Relay"].value_per_1000_chf - result_by_name["Plain spending limit"].value_per_1000_chf
    direction = "more" if relay_advantage >= 0 else "less"
    headline_amount = money(abs(relay_advantage))

    measured_rows = [
        "| Setup | Purchases approved | Approved amount | Purchases needing intervention: stopped or questioned | Ordinary purchases wrongly stopped or questioned |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    value_rows = [
        "| Setup | Expected issuer value on 45 purchases | Expected issuer value per 1,000 purchases | Difference from plain spending limit |",
        "| --- | ---: | ---: | ---: |",
    ]
    limit_value = result_by_name["Plain spending limit"].value_per_1000_chf
    for result in results:
        measured_rows.append(
            "| " + result.name
            + " | " + str(result.approved_count) + " of " + str(result.purchase_count)
            + " | " + money(result.approved_chf)
            + " | " + str(result.correctly_intervened_count) + " of " + str(result.needed_intervention_count)
            + " | " + str(result.wrongly_intervened_count) + " of " + str(result.ordinary_count)
            + " |"
        )
        difference = result.value_per_1000_chf - limit_value
        value_rows.append(
            "| " + result.name
            + " | " + money(result.sample_value_chf)
            + " | " + money(result.value_per_1000_chf)
            + " | " + money(difference)
            + " |"
        )

    dispute_rows = [
        "| Problem kind | Assumed dispute share |",
        "| --- | ---: |",
    ] + [
        "| `" + problem_kind + "` | " + decimal_text(share * Decimal("100")) + "% |"
        for problem_kind, share in sorted(DISPUTE_SHARE.items())
    ]

    input_lines = ["- " + setup_name + ": `" + relative_path(path) + "`" for setup_name, path in input_paths.items()]
    return "\n".join([
        "# Business impact — three-way comparison",
        "",
        "Generated " + written_at.strftime("%Y-%m-%d %H:%M UTC") + ".",
        "",
        "## Presentation headline",
        "",
        "**On this synthetic test set, per 1,000 agent purchases, Relay has about " + headline_amount + " " + direction + " expected issuer value than a plain spending limit.**",
        "",
        "This is a scenario-model result, not a forecast of real customer behaviour. Replace the assumptions below with Viseca figures before presenting it as a business estimate.",
        "",
        "## Measured protection KPI",
        "",
        *measured_rows,
        "",
        "A purchase needs intervention when its reviewed `decision_on_own_facts` is `decline` or `step_up`. Both a decline and a question count as an intervention; an approval does not.",
        "",
        "## Assumption-based business KPI",
        "",
        *value_rows,
        "",
        "Value formula per purchase:",
        "",
        "- approved purchase: transaction amount × fee rate;",
        "- purchase needing intervention but approved: fee income minus dispute share × (handling cost + transaction amount × write-off share);",
        "- ordinary purchase stopped or questioned: false-block cost, with no fee income;",
        "- purchase needing intervention and stopped or questioned: zero incremental value or cost.",
        "",
        "The 45-purchase value is scaled by `1000 / 45`. A question is conservatively treated as preventing payment in this simple model; production conversion after a customer answer is not available in the synthetic data.",
        "",
        "## Financial assumptions — replace with Viseca figures",
        "",
        "| Setting | Assumption |",
        "| --- | ---: |",
        "| `FEE_RATE` | " + decimal_text(FEE_RATE * Decimal("100")) + "% of approved amount |",
        "| `DISPUTE_HANDLING_COST_CHF` | " + money(DISPUTE_HANDLING_COST_CHF) + " per dispute |",
        "| `WRITE_OFF_SHARE` | " + decimal_text(WRITE_OFF_SHARE * Decimal("100")) + "% of disputed amount |",
        "| `FALSE_BLOCK_COST_CHF` | " + money(FALSE_BLOCK_COST_CHF) + " beyond the lost fee |",
        "",
        *dispute_rows,
        "",
        "## Inputs",
        "",
        *input_lines,
        "- Reviewed verdicts: `" + relative_path(VERDICT_TABLE_PATH) + "`",
        "",
    ])









#### Step 6: Run from the command line ####

def parse_command_line(arguments = None):
    parser = argparse.ArgumentParser(description = "Compare no control, a spending limit and Relay, then estimate issuer value per 1,000 purchases.")
    parser.add_argument("--no-control", help = "Path to an all_none replay JSONL; newest run is used by default")
    parser.add_argument("--limit-only", help = "Path to an all_limit_only replay JSONL; newest run is used by default")
    parser.add_argument("--engine", help = "Path to an all_engine replay JSONL; newest run is used by default")
    parser.add_argument("--output", default = str(DEFAULT_OUTPUT_PATH), help = "Markdown report path")
    return parser.parse_args(arguments)


def main(arguments = None):
    options = parse_command_line(arguments)
    explicit_paths = {
        "No control": options.no_control,
        "Plain spending limit": options.limit_only,
        "Relay": options.engine,
    }
    input_paths = {
        setup_name: resolve_input_path(explicit_paths[setup_name], fallback_pattern)
        for setup_name, fallback_pattern in SETUPS
    }
    verdicts = read_verdicts(VERDICT_TABLE_PATH)
    purchases_by_setup = {
        setup_name: read_replay(input_path, verdicts)
        for setup_name, input_path in input_paths.items()
    }
    validate_same_test_set(purchases_by_setup)
    results = [evaluate_setup(setup_name, purchases_by_setup[setup_name]) for setup_name, _ in SETUPS]

    output_path = Path(options.output)
    if not output_path.is_absolute():
        output_path = REPOSITORY_FOLDER / output_path
    output_path.parent.mkdir(parents = True, exist_ok = True)
    report = build_report(results, input_paths, datetime.now(timezone.utc))
    output_path.write_text(report, encoding = "utf-8")

    print(report)
    print("Report written to " + str(output_path))
    return results


if __name__ == "__main__":
    main()
