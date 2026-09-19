# Script: test_guard_period_budget.py
# Purpose: Check every boundary of the budget over a period, from exactly the budget to just above the tolerated overshoot, the missing memory and the message about the next amount that frees up
# Author: Andrés Gallardo
# Date: September 2026

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_period_budget import PeriodBudgetGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_ledger_snapshot_for_event









#### Step 1: Define the shared helpers ####

# Locate the source file that must never name an identifier
GUARD_SOURCE_PATH = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine" / "guards" / "g_period_budget.py"
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
PERIOD_BUDGET_GUARD = PeriodBudgetGuard(
    guard_number = 4,
    guard_id = "period_budget",
    family = GuardFamily.SPENDING_LIMITS,
)



# State the simulated time of the purchase being decided, which is 18.10 on the Swiss clock in summer
PURCHASE_TIME = datetime(2026, 8, 16, 16, 10, 0, tzinfo = timezone.utc)



# Write a moment as ISO text with a trailing Z, as the messages write it
def format_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")



# Copy the example message with another amount in Swiss francs and the purchase time of the tests, and read it through the strict reader
def build_event(example_message, billing_amount_chf, uncertainty_policy = "ask", instruction = None):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["billing_amount_chf"] = billing_amount_chf
    changed_message["authorization"]["amount"] = billing_amount_chf
    changed_message["authorization"]["timestamp"] = format_time(PURCHASE_TIME)
    changed_message["mandate"]["uncertainty_policy"] = uncertainty_policy
    if instruction is not None:
        changed_message["mandate"]["instruction"] = instruction
    return read_purchase_message(changed_message)



# Build one earlier purchase of the run at the shop of the example message
def build_row(example_message, time_before_purchase, amount, status = "approved"):
    return {
        "authorization_id": "EARLIER_" + str(time_before_purchase.total_seconds()) + "_" + str(amount),
        "timestamp": format_time(PURCHASE_TIME - time_before_purchase),
        "merchant_id": example_message["authorization"]["merchant"]["merchant_id"],
        "billing_amount_chf": amount,
        "status": status,
    }



# Build a policy by hand, without the compiler, where a limit of None leaves the budget empty
def build_policy(limit_text, period_days = 7, inclusive = True, share_text = "0.10", reading = "read", uncertainty_policy = "ask"):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = uncertainty_policy,
        expectations = Expectations(
            period_limit_chf = None if limit_text is None else Decimal(limit_text),
            period_days = period_days,
            period_limit_inclusive = inclusive,
            period_limit_reading = reading,
            overshoot_tolerance_share = Decimal(share_text),
        ),
        open_questions = (),
    )



# Let the guard alone judge one purchase against the earlier purchases of its run
def check_purchase(event, policy, rows):
    snapshot = build_ledger_snapshot_for_event(rows, event)
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = snapshot)
    return PERIOD_BUDGET_GUARD.check(decision_input, {})



# Find one evidence item of a result by the name of its fact
def find_evidence(guard_result, fact_name):
    return next(evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name)









#### Step 2: Check the boundaries ####

# List what was spent one day earlier, whether the budget of CHF 300 is inclusive, the tolerance share, the amount and the verdict that must follow
BOUNDARY_CASES = [
    (276.00, True, "0.10", 23.99, GuardVerdict.PASS),
    (276.00, True, "0.10", 24.00, GuardVerdict.PASS),
    (276.00, True, "0.10", 24.01, GuardVerdict.STEP_UP),
    (276.00, True, "0.10", 54.00, GuardVerdict.STEP_UP),
    (276.00, True, "0.10", 54.01, GuardVerdict.DECLINE),
    (276.00, True, "0", 24.00, GuardVerdict.PASS),
    (276.00, True, "0", 24.01, GuardVerdict.DECLINE),
    (276.00, False, "0.10", 23.99, GuardVerdict.PASS),
    (276.00, False, "0.10", 24.00, GuardVerdict.STEP_UP),
    (276.00, False, "0.10", 54.00, GuardVerdict.STEP_UP),
    (276.00, False, "0.10", 54.01, GuardVerdict.DECLINE),
    (276.00, False, "0", 24.00, GuardVerdict.DECLINE),
    (0.00, True, "0.10", 330.00, GuardVerdict.STEP_UP),
    (0.00, True, "0.10", 330.01, GuardVerdict.DECLINE),
]



# State the reason code that belongs to each verdict of this guard
EXPECTED_REASON_CODE_BY_VERDICT = {
    GuardVerdict.PASS: None,
    GuardVerdict.STEP_UP: ReasonCode.SMALL_OVERSHOOT,
    GuardVerdict.DECLINE: ReasonCode.OVER_PERIOD_LIMIT,
}



# Check the verdict, the reason code, the total and the number of evidence items at every boundary.
# A total of exactly 300.00 passes, 300.01 asks, exactly 330.00 asks and 330.01 declines.
@pytest.mark.parametrize("spent_before, inclusive, share_text, billing_amount_chf, expected_verdict", BOUNDARY_CASES)
def test_boundaries_of_the_budget(example_message, spent_before, inclusive, share_text, billing_amount_chf, expected_verdict):
    event = build_event(example_message, billing_amount_chf)
    rows = [build_row(example_message, timedelta(days = 1), spent_before)]
    guard_result = check_purchase(event, build_policy("300", inclusive = inclusive, share_text = share_text), rows)
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == EXPECTED_REASON_CODE_BY_VERDICT[expected_verdict]
    assert len(guard_result.evidence) == (5 if expected_verdict == GuardVerdict.PASS else 6)
    assert (guard_result.customer_message is None) == (expected_verdict == GuardVerdict.PASS)
    assert Decimal(str(find_evidence(guard_result, "period_total_chf").value)) == Decimal(str(spent_before)) + Decimal(str(billing_amount_chf))
    assert guard_result.guard_number == 4
    assert guard_result.guard_id == "period_budget"
    assert guard_result.family == GuardFamily.SPENDING_LIMITS



# Check that amounts with binary noise are summed as the money they stand for
def test_float_noise_does_not_break_an_inclusive_budget(example_message):
    event = build_event(example_message, 0.2)
    rows = [build_row(example_message, timedelta(days = 1), 0.1)]
    guard_result = check_purchase(event, build_policy("0.30"), rows)
    assert 0.1 + 0.2 != 0.3
    assert guard_result.verdict == GuardVerdict.PASS



# Check that an open question never counts as spent, so CHF 100.00 pending leaves the total at 300.00
def test_pending_purchase_does_not_count(example_message):
    event = build_event(example_message, 24.00)
    rows = [
        build_row(example_message, timedelta(days = 1), 276.00),
        build_row(example_message, timedelta(hours = 2), 100.00, status = "pending"),
    ]
    guard_result = check_purchase(event, build_policy("300"), rows)
    assert guard_result.verdict == GuardVerdict.PASS
    assert find_evidence(guard_result, "period_total_chf").value == 300.0
    assert find_evidence(guard_result, "period_spent_before_chf").value == 276.0



# Check that a declined purchase never counts either
def test_declined_purchase_does_not_count(example_message):
    event = build_event(example_message, 24.00)
    rows = [
        build_row(example_message, timedelta(days = 1), 276.00),
        build_row(example_message, timedelta(hours = 2), 100.00, status = "declined"),
    ]
    assert check_purchase(event, build_policy("300"), rows).verdict == GuardVerdict.PASS



# Check that an order is approved once earlier ones have left the period, where exactly seven days ago has left and one second less has not
def test_order_passes_once_earlier_ones_leave_the_period(example_message):
    event = build_event(example_message, 88.00)
    rows_that_left = [
        build_row(example_message, timedelta(days = 7), 164.50),
        build_row(example_message, timedelta(days = 1), 135.50),
    ]
    rows_still_inside = [
        build_row(example_message, timedelta(days = 7) - timedelta(seconds = 1), 164.50),
        build_row(example_message, timedelta(days = 1), 135.50),
    ]
    result_after_leaving = check_purchase(event, build_policy("300"), rows_that_left)
    result_still_inside = check_purchase(event, build_policy("300"), rows_still_inside)
    assert result_after_leaving.verdict == GuardVerdict.PASS
    assert find_evidence(result_after_leaving, "period_total_chf").value == 223.5
    assert result_still_inside.verdict == GuardVerdict.DECLINE
    assert find_evidence(result_still_inside, "period_total_chf").value == 388.0



# Check that a budget without a number of days counts every approved purchase of the run
def test_budget_over_the_whole_mandate_counts_everything(example_message):
    event = build_event(example_message, 100.00)
    rows = [build_row(example_message, timedelta(days = 60), 850.00)]
    guard_result = check_purchase(event, build_policy("900", period_days = None), rows)
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert find_evidence(guard_result, "period_days").value is None
    assert guard_result.customer_message == (
        "This order of CHF 100.00 brings your spending under this instruction to CHF 950.00, "
        "which is CHF 50.00 above your budget of CHF 900.00. Approve anyway?"
    )









#### Step 3: Check the readings without a usable budget and the missing memory ####

# Check that an instruction without a budget skips the guard, also without any memory of the run
def test_no_budget_gives_skip_with_state_none(example_message):
    event = build_event(example_message, 5000.00)
    decision_input = DecisionInput(event = event, policy = build_policy(None, reading = "not_stated"), facts = build_fact_sheet(event), state = None)
    guard_result = PERIOD_BUDGET_GUARD.check(decision_input, {})
    assert guard_result.verdict == GuardVerdict.SKIP
    assert guard_result.reason_code is None
    assert guard_result.customer_message is None



# Check that an unclear budget, and a read budget that is missing, ask about the purchase
@pytest.mark.parametrize("reading", ["unclear", "read"])
def test_unclear_budget_gives_uncertain(example_message, reading):
    guard_result = check_purchase(build_event(example_message, 49.90), build_policy(None, reading = reading), [])
    assert guard_result.verdict == GuardVerdict.UNCERTAIN
    assert guard_result.reason_code == ReasonCode.PERIOD_LIMIT_UNCLEAR
    assert "CHF 49.90" in guard_result.customer_message



# Check that a budget without a memory of the run, with something that is no memory, and with an incomplete memory is never judged
def test_budget_without_a_usable_memory_gives_uncertain(example_message):
    event = build_event(example_message, 24.00)
    policy = build_policy("300")
    incomplete_snapshot = build_ledger_snapshot_for_event(
        [{"authorization_id": "BROKEN", "timestamp": format_time(PURCHASE_TIME - timedelta(days = 1)), "merchant_id": "SHOP", "billing_amount_chf": None, "status": "approved"}],
        event,
    )
    assert incomplete_snapshot.is_complete is False
    for unusable_state in (None, {"entries": []}, incomplete_snapshot):
        decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = unusable_state)
        guard_result = PERIOD_BUDGET_GUARD.check(decision_input, {})
        assert guard_result.verdict == GuardVerdict.UNCERTAIN
        assert guard_result.reason_code == ReasonCode.LEDGER_UNAVAILABLE
        assert find_evidence(guard_result, "period_total_chf").value is None
        assert find_evidence(guard_result, "period_total_chf").threshold == 300.0
        assert "CHF 300.00" in guard_result.customer_message



# Check that a missing memory asks under ask, declines under decline and still asks under approve, so it never approves
@pytest.mark.parametrize(
    "uncertainty_policy, expected_decision",
    [("ask", Decision.STEP_UP), ("decline", Decision.DECLINE), ("approve", Decision.STEP_UP)],
)
def test_missing_memory_never_approves(example_message, uncertainty_policy, expected_decision):
    event = build_event(example_message, 24.00, uncertainty_policy = uncertainty_policy)
    decision_trace = decide(event, build_policy("300", uncertainty_policy = uncertainty_policy), None)
    assert decision_trace.decision == expected_decision
    assert decision_trace.reason_codes == [ReasonCode.LEDGER_UNAVAILABLE]
    assert decision_trace.aggregation.raised_by == ["period_budget", "duplicate_order"]









#### Step 4: Check the evidence and the messages ####

# State the four approved orders of a full period, the first of which was paid at 11.12 on the Swiss clock on 10 August
def build_rows_of_a_full_period(example_message):
    return [
        build_row(example_message, PURCHASE_TIME - datetime(2026, 8, 10, 9, 12, 0, tzinfo = timezone.utc), 44.50),
        build_row(example_message, PURCHASE_TIME - datetime(2026, 8, 11, 18, 35, 0, tzinfo = timezone.utc), 120.00),
        build_row(example_message, PURCHASE_TIME - datetime(2026, 8, 13, 17, 20, 0, tzinfo = timezone.utc), 70.00),
        build_row(example_message, PURCHASE_TIME - datetime(2026, 8, 15, 9, 30, 0, tzinfo = timezone.utc), 65.50),
    ]



# Check the question about an order of CHF 24 on a full period, which names the overshoot and the moment an amount frees up in Swiss local time
def test_question_names_the_overshoot_and_the_next_release(example_message):
    event = build_event(example_message, 24.00)
    guard_result = check_purchase(event, build_policy("300"), build_rows_of_a_full_period(example_message))
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.reason_code == ReasonCode.SMALL_OVERSHOOT
    assert guard_result.customer_message == (
        "This order of CHF 24.00 brings your spending over the last 7 days to CHF 324.00, which is CHF 24.00 above your budget of CHF 300.00. "
        "CHF 44.50 frees up on 17 August at 11.12. Approve anyway?"
    )



# Check the evidence of that question field by field
def test_question_carries_its_evidence(example_message):
    event = build_event(example_message, 24.00)
    guard_result = check_purchase(event, build_policy("300"), build_rows_of_a_full_period(example_message))
    assert [(evidence_item.fact, evidence_item.value, evidence_item.comparator, evidence_item.threshold) for evidence_item in guard_result.evidence] == [
        ("period_total_chf", 324.0, "<=", 300.0),
        ("period_spent_before_chf", 300.0, None, None),
        ("period_days", 7, None, None),
        ("billing_amount_chf", 24.0, None, None),
        ("platform_approved_spend_in_period_chf", example_message["context"]["approved_spend_in_period_chf"], None, None),
        ("overshoot_share", 0.08, "<=", 0.1),
    ]



# Check that the decline starts with the word Declined, names the overshoot and the next release, and asks nothing
def test_decline_names_the_overshoot_and_asks_nothing(example_message):
    event = build_event(example_message, 93.50)
    guard_result = check_purchase(event, build_policy("300"), build_rows_of_a_full_period(example_message))
    assert guard_result.verdict == GuardVerdict.DECLINE
    assert guard_result.customer_message == (
        "Declined. This order of CHF 93.50 brings your spending over the last 7 days to CHF 393.50, which is CHF 93.50 above your budget of CHF 300.00. "
        "CHF 44.50 frees up on 17 August at 11.12."
    )
    assert "?" not in guard_result.customer_message



# Check that the release is told on the Swiss winter clock as well, which is one hour ahead of UTC
def test_release_is_told_in_swiss_winter_time(example_message):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["billing_amount_chf"] = 24.00
    changed_message["authorization"]["amount"] = 24.00
    changed_message["authorization"]["timestamp"] = "2026-12-20T10:00:00Z"
    event = read_purchase_message(changed_message)
    rows = [{"authorization_id": "EARLIER", "timestamp": "2026-12-14T09:12:00Z", "merchant_id": "SHOP", "billing_amount_chf": 290.00, "status": "approved"}]
    guard_result = check_purchase(event, build_policy("300"), rows)
    assert "CHF 290.00 frees up on 21 December at 10.12." in guard_result.customer_message



# Check the two messages about a total equal to a budget the customer wanted to stay under
def test_total_equal_to_an_exclusive_budget_is_explained(example_message):
    event = build_event(example_message, 24.00)
    rows = [build_row(example_message, timedelta(days = 1), 276.00)]
    asked_result = check_purchase(event, build_policy("300", inclusive = False), rows)
    assert find_evidence(asked_result, "period_total_chf").comparator == "<"
    assert find_evidence(asked_result, "overshoot_share").value == 0.0
    assert "which is exactly your budget of CHF 300.00, and your instruction asked to stay under it." in asked_result.customer_message
    assert asked_result.customer_message.endswith("Approve anyway?")



# Check that the platform's own figure is recorded and decides nothing, also when it disagrees or is missing
@pytest.mark.parametrize("platform_spend", [0.0, 9999.0, None])
def test_platform_figure_decides_nothing(example_message, platform_spend):
    changed_message = copy.deepcopy(example_message)
    changed_message["context"]["approved_spend_in_period_chf"] = platform_spend
    event = build_event(changed_message, 24.00)
    guard_result = check_purchase(event, build_policy("300"), [build_row(example_message, timedelta(days = 1), 276.00)])
    assert guard_result.verdict == GuardVerdict.PASS
    assert find_evidence(guard_result, "platform_approved_spend_in_period_chf").value == platform_spend









#### Step 5: Check the way from the instruction to the decision ####

# Check the household sentence end to end, through the compiler and the whole pipeline, on a full period
@pytest.mark.parametrize(
    "billing_amount_chf, expected_decision, expected_reason_codes",
    [
        (24.00, Decision.STEP_UP, [ReasonCode.SMALL_OVERSHOOT]),
        (93.50, Decision.DECLINE, [ReasonCode.OVER_PERIOD_LIMIT]),
    ],
)
def test_instruction_to_decision_end_to_end(example_message, billing_amount_chf, expected_decision, expected_reason_codes):
    instruction = "Order our household groceries for delivery. Keep each order at or below CHF 120 including delivery, and keep the total across any seven days at or below CHF 300. Ask me when uncertain."
    event = build_event(example_message, billing_amount_chf, instruction = instruction)
    policy = build_policy_from_mandate(event.mandate)
    decision_trace = decide(event, policy, build_ledger_snapshot_for_event(build_rows_of_a_full_period(example_message), event))
    assert policy.expectations.period_limit_chf == Decimal("300")
    assert policy.expectations.period_days == 7
    assert decision_trace.decision == expected_decision
    assert decision_trace.reason_codes == expected_reason_codes
    assert decision_trace.aggregation.raised_by == ["period_budget"]









#### Step 6: Check that the guard never names an identifier ####

# Check the source text of the guard for the words that would tie a decision to a test case or to an identifier
def test_source_names_no_identifier():
    source_text = GUARD_SOURCE_PATH.read_text(encoding = "utf-8").lower()
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text]
    assert words_found == []
