# Script: test_guard_per_order_limit.py
# Purpose: Check every boundary of the limit per order, from exactly the limit to just above the tolerated overshoot, and the way from the instruction to the decision
# Author: Andrés Gallardo
# Date: September 2026

import copy
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_per_order_limit import PerOrderLimitGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_empty_ledger_snapshot









#### Step 1: Define the shared helpers ####

# Locate the two source files that must never name an identifier
BACKEND_APP_FOLDER = Path(__file__).resolve().parent.parent / "backend" / "app"
CHECKED_SOURCE_PATHS = (
    BACKEND_APP_FOLDER / "engine" / "guards" / "g_per_order_limit.py",
    BACKEND_APP_FOLDER / "policyc" / "order_limit.py",
)
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id")



# Build the guard as the registry does, from its number, its id and its family
PER_ORDER_LIMIT_GUARD = PerOrderLimitGuard(
    guard_number = 3,
    guard_id = "per_order_limit",
    family = GuardFamily.SPENDING_LIMITS,
)



# Copy the example message with another amount in Swiss francs, and read it through the strict reader.
# The amount in the purchase currency equals the franc amount unless a test hands in another one.
def build_event(example_message, billing_amount_chf, amount = None, currency = "CHF", instruction = None):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["billing_amount_chf"] = billing_amount_chf
    changed_message["authorization"]["amount"] = billing_amount_chf if amount is None else amount
    changed_message["authorization"]["currency"] = currency
    if instruction is not None:
        changed_message["mandate"]["instruction"] = instruction
    return read_purchase_message(changed_message)



# Build a policy by hand, without the compiler, where a limit of None leaves the limit empty
def build_policy(limit_text, inclusive = True, share_text = "0.10", reading = "read", uncertainty_policy = "ask"):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = uncertainty_policy,
        expectations = Expectations(
            per_order_limit_chf = None if limit_text is None else Decimal(limit_text),
            per_order_limit_inclusive = inclusive,
            per_order_limit_reading = reading,
            overshoot_tolerance_share = Decimal(share_text),
        ),
        open_questions = (),
    )



# Let the guard alone judge one purchase
def check_purchase(event, policy):
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event))
    return PER_ORDER_LIMIT_GUARD.check(decision_input, {})









#### Step 2: Check the boundaries ####

# List the limit, whether it is inclusive, the tolerance share, the amount and the verdict that must follow
BOUNDARY_CASES = [
    ("120", True, "0.10", 119.99, GuardVerdict.PASS),
    ("120", True, "0.10", 120.00, GuardVerdict.PASS),
    ("120", True, "0.10", 120.01, GuardVerdict.STEP_UP),
    ("120", True, "0.10", 132.00, GuardVerdict.STEP_UP),
    ("120", True, "0.10", 132.01, GuardVerdict.DECLINE),
    ("120", True, "0", 120.00, GuardVerdict.PASS),
    ("120", True, "0", 120.01, GuardVerdict.DECLINE),
    ("60", False, "0.10", 59.99, GuardVerdict.PASS),
    ("60", False, "0.10", 60.00, GuardVerdict.STEP_UP),
    ("60", False, "0.10", 66.00, GuardVerdict.STEP_UP),
    ("60", False, "0.10", 66.01, GuardVerdict.DECLINE),
    ("60", False, "0", 59.99, GuardVerdict.PASS),
    ("60", False, "0", 60.00, GuardVerdict.DECLINE),
    ("20", True, "0.10", 22.00, GuardVerdict.STEP_UP),
    ("20", True, "0.10", 22.01, GuardVerdict.DECLINE),
]



# State the reason code that belongs to each verdict of this guard
EXPECTED_REASON_CODE_BY_VERDICT = {
    GuardVerdict.PASS: None,
    GuardVerdict.STEP_UP: ReasonCode.SMALL_OVERSHOOT,
    GuardVerdict.DECLINE: ReasonCode.OVER_PER_ORDER_LIMIT,
}



# Check the verdict, the reason code and the number of evidence items at every boundary
@pytest.mark.parametrize("limit_text, inclusive, share_text, billing_amount_chf, expected_verdict", BOUNDARY_CASES)
def test_boundaries_of_the_limit(example_message, limit_text, inclusive, share_text, billing_amount_chf, expected_verdict):
    event = build_event(example_message, billing_amount_chf)
    guard_result = check_purchase(event, build_policy(limit_text, inclusive = inclusive, share_text = share_text))
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == EXPECTED_REASON_CODE_BY_VERDICT[expected_verdict]
    assert len(guard_result.evidence) == (1 if expected_verdict == GuardVerdict.PASS else 2)
    assert (guard_result.customer_message is None) == (expected_verdict == GuardVerdict.PASS)
    assert guard_result.guard_number == 3
    assert guard_result.guard_id == "per_order_limit"
    assert guard_result.family == GuardFamily.SPENDING_LIMITS



# Check that a float with binary noise is compared as the money it stands for
def test_float_noise_does_not_break_an_inclusive_limit(example_message):
    event = build_event(example_message, 0.1 + 0.2)
    guard_result = check_purchase(event, build_policy("0.30"))
    assert event.authorization.billing_amount_chf != 0.3
    assert guard_result.verdict == GuardVerdict.PASS



# Check that the franc amount decides and never the amount in the purchase currency
def test_foreign_purchase_is_judged_on_its_franc_amount(example_message):
    passing_event = build_event(example_message, 247.00, amount = 260.00, currency = "EUR")
    declined_event = build_event(example_message, 275.50, amount = 290.00, currency = "EUR")
    passing_result = check_purchase(passing_event, build_policy("250"))
    declined_result = check_purchase(declined_event, build_policy("250"))
    assert passing_result.verdict == GuardVerdict.PASS
    assert declined_result.verdict == GuardVerdict.DECLINE
    assert declined_result.reason_code == ReasonCode.OVER_PER_ORDER_LIMIT
    assert declined_result.customer_message == "Declined. EUR 290.00, which is CHF 275.50, is CHF 25.50 above your limit of CHF 250.00."
    assert declined_result.evidence[0].value == 275.5



# Check that a foreign amount below the limit in numbers still declines when its franc amount is above it
def test_small_foreign_number_does_not_hide_a_large_franc_amount(example_message):
    event = build_event(example_message, 300.00, amount = 240.00, currency = "GBP")
    assert check_purchase(event, build_policy("250")).verdict == GuardVerdict.DECLINE









#### Step 3: Check the readings without a usable limit ####

# Check that an instruction without a limit skips the guard, with one evidence item and no reason code
def test_limit_not_stated_gives_skip(example_message):
    guard_result = check_purchase(build_event(example_message, 5000.00), build_policy(None, reading = "not_stated"))
    assert guard_result.verdict == GuardVerdict.SKIP
    assert guard_result.reason_code is None
    assert len(guard_result.evidence) == 1
    assert guard_result.evidence[0].value == "not_stated"
    assert guard_result.customer_message is None



# Check that an unclear limit, and a read limit that is missing, ask about the purchase and name its amount in francs
@pytest.mark.parametrize("reading", ["unclear", "read"])
def test_unclear_limit_gives_uncertain(example_message, reading):
    guard_result = check_purchase(build_event(example_message, 49.90), build_policy(None, reading = reading))
    assert guard_result.verdict == GuardVerdict.UNCERTAIN
    assert guard_result.reason_code == ReasonCode.ORDER_LIMIT_UNCLEAR
    assert "CHF 49.90" in guard_result.customer_message
    assert guard_result.customer_message.endswith("?")



# Check that an unclear limit asks the customer through the whole pipeline when the customer wants to be asked.
# The run has no earlier purchase, which the engine has to be told, because a missing memory asks on its own.
def test_unclear_limit_is_a_step_up_under_the_policy_ask(example_message):
    event = build_event(example_message, 49.90)
    decision_trace = decide(event, build_policy(None, reading = "unclear", uncertainty_policy = "ask"), build_empty_ledger_snapshot(event))
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.ORDER_LIMIT_UNCLEAR]
    assert decision_trace.aggregation.raised_by == ["per_order_limit"]
    assert decision_trace.aggregation.uncertainty_policy_applied is True
    assert "CHF 49.90" in decision_trace.customer_message



# Check that an unclear limit declines when the customer chose to decline whatever is uncertain
def test_unclear_limit_is_a_decline_under_the_policy_decline(example_message):
    event = build_event(example_message, 49.90)
    decision_trace = decide(event, build_policy(None, reading = "unclear", uncertainty_policy = "decline"), None)
    assert decision_trace.decision == Decision.DECLINE









#### Step 4: Check the evidence and the messages field by field ####

# Check the question about a purchase of CHF 126 against a limit of CHF 120
def test_question_carries_its_evidence_and_its_message(example_message):
    guard_result = check_purchase(build_event(example_message, 126.00), build_policy("120"))
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.reason_code == ReasonCode.SMALL_OVERSHOOT
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "billing_amount_chf",
            "value": 126.0,
            "comparator": "<=",
            "threshold": 120.0,
            "source": "authorization.billing_amount_chf against policy.expectations.per_order_limit_chf",
        },
        {
            "fact": "overshoot_share",
            "value": 0.05,
            "comparator": "<=",
            "threshold": 0.1,
            "source": "policy.expectations.overshoot_tolerance_share",
        },
    ]
    assert all(isinstance(evidence_item.value, float) and isinstance(evidence_item.threshold, float) for evidence_item in guard_result.evidence)
    assert guard_result.customer_message == "CHF 126.00 is CHF 6.00 above your limit of CHF 120.00. Approve anyway?"



# Check the decline of a purchase of CHF 138 against a limit of CHF 120
def test_decline_carries_its_evidence_and_its_message(example_message):
    guard_result = check_purchase(build_event(example_message, 138.00), build_policy("120"))
    assert guard_result.verdict == GuardVerdict.DECLINE
    assert guard_result.reason_code == ReasonCode.OVER_PER_ORDER_LIMIT
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "billing_amount_chf",
            "value": 138.0,
            "comparator": "<=",
            "threshold": 120.0,
            "source": "authorization.billing_amount_chf against policy.expectations.per_order_limit_chf",
        },
        {
            "fact": "overshoot_share",
            "value": 0.15,
            "comparator": "<=",
            "threshold": 0.1,
            "source": "policy.expectations.overshoot_tolerance_share",
        },
    ]
    assert guard_result.customer_message == "Declined. CHF 138.00 is CHF 18.00 above your limit of CHF 120.00."



# Check the two messages about an amount equal to a limit the customer wanted to stay under
def test_amount_equal_to_an_exclusive_limit_is_explained(example_message):
    event = build_event(example_message, 60.00)
    asked_result = check_purchase(event, build_policy("60", inclusive = False))
    declined_result = check_purchase(event, build_policy("60", inclusive = False, share_text = "0"))
    assert asked_result.evidence[0].comparator == "<"
    assert asked_result.evidence[1].value == 0.0
    assert asked_result.customer_message == "CHF 60.00 is exactly your limit of CHF 60.00, and your instruction asked to stay under it. Approve anyway?"
    assert declined_result.customer_message == "Declined. CHF 60.00 is exactly your limit of CHF 60.00, and your instruction asked to stay under it."









#### Step 5: Check the way from the instruction to the decision ####

# Check the grocery sentence end to end, through the compiler and the whole pipeline
@pytest.mark.parametrize(
    "billing_amount_chf, expected_decision, expected_reason_codes",
    [
        (20.00, Decision.APPROVE, []),
        (21.00, Decision.STEP_UP, [ReasonCode.SMALL_OVERSHOOT]),
        (23.00, Decision.DECLINE, [ReasonCode.OVER_PER_ORDER_LIMIT]),
    ],
)
def test_instruction_to_decision_end_to_end(example_message, billing_amount_chf, expected_decision, expected_reason_codes):
    event = build_event(example_message, billing_amount_chf, instruction = "Buy one grocery item for CHF 20 or less.")
    policy = build_policy_from_mandate(event.mandate)
    decision_trace = decide(event, policy, build_empty_ledger_snapshot(event))
    assert policy.expectations.per_order_limit_chf == Decimal("20")
    assert decision_trace.decision == expected_decision
    assert decision_trace.reason_codes == expected_reason_codes
    assert len(decision_trace.guards) == 23
    assert decision_trace.facts.amounts.billing_amount_chf == billing_amount_chf



# Check that a tolerance of zero, handed to the compiler, turns the question into a decline
def test_zero_tolerance_end_to_end(example_message):
    event = build_event(example_message, 21.00, instruction = "Buy one grocery item for CHF 20 or less.")
    policy = build_policy_from_mandate(event.mandate, overshoot_tolerance_share = Decimal("0"))
    assert decide(event, policy, build_empty_ledger_snapshot(event)).decision == Decision.DECLINE









#### Step 6: Check that the guard and the reader never name an identifier ####

# Check the source text of both files for the words that would tie a decision to a test case
@pytest.mark.parametrize("source_path", CHECKED_SOURCE_PATHS, ids = [source_path.name for source_path in CHECKED_SOURCE_PATHS])
def test_source_names_no_identifier(source_path):
    source_text = source_path.read_text(encoding = "utf-8").lower()
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text]
    assert words_found == []
