# Script: test_aggregation.py
# Purpose: Check that the aggregation takes the highest severity of all guard results, can never be lowered and reports its reasons in order
# Author: Andrés Gallardo
# Date: September 2026

import itertools

import pytest

from app.engine.aggregate import DEFAULT_CUSTOMER_MESSAGES, SEVERITY_RANK, aggregate, measure_result_severity
from app.models.decision import Decision, GuardFamily, GuardResult, GuardVerdict, ReasonCode









#### Step 1: Define the shared helpers ####

# List the three uncertainty policies and the five verdicts
UNCERTAINTY_POLICIES = ("ask", "decline", "approve")
ALL_VERDICTS = tuple(GuardVerdict)



# State the expected severity of every verdict that decides on its own, written out here independently of the engine
EXPECTED_SEVERITY_BY_VERDICT = {
    GuardVerdict.PASS: Decision.APPROVE,
    GuardVerdict.SKIP: Decision.APPROVE,
    GuardVerdict.STEP_UP: Decision.STEP_UP,
    GuardVerdict.DECLINE: Decision.DECLINE,
}



# State the expected severity of an UNCERTAIN verdict under each uncertainty policy
EXPECTED_SEVERITY_OF_UNCERTAIN = {
    "ask": Decision.STEP_UP,
    "decline": Decision.DECLINE,
    "approve": Decision.APPROVE,
}



# Build one guard result with a verdict, where the position gives it a number and an id of its own
def build_result(verdict, position = 1, reason_code = None, customer_message = None, note = None):
    return GuardResult(
        guard_number = position,
        guard_id = "stub_" + str(position),
        family = GuardFamily.SESSION,
        verdict = verdict,
        reason_code = reason_code,
        customer_message = customer_message,
        note = note,
    )



# Look up the expected severity of one verdict under one uncertainty policy
def expected_severity(verdict, uncertainty_policy):
    if verdict == GuardVerdict.UNCERTAIN:
        return EXPECTED_SEVERITY_OF_UNCERTAIN[uncertainty_policy]
    return EXPECTED_SEVERITY_BY_VERDICT[verdict]



# Look up the highest expected severity of several verdicts, which is approve when there are none
def highest_expected_severity(verdicts, uncertainty_policy):
    expected_severities = [expected_severity(verdict, uncertainty_policy) for verdict in verdicts]
    return max([Decision.APPROVE] + expected_severities, key = SEVERITY_RANK.get)



# List every combination of the five verdicts over one to four results
def list_verdict_combinations():
    return [
        verdict_combination
        for result_count in range(1, 5)
        for verdict_combination in itertools.product(ALL_VERDICTS, repeat = result_count)
    ]



# Build the results of one combination, numbered by their position
def build_results(verdict_combination):
    return [build_result(verdict, position) for position, verdict in enumerate(verdict_combination, start = 1)]









#### Step 2: Check the property by enumeration ####

# Check the severity order itself
def test_severity_order_is_approve_then_step_up_then_decline():
    assert SEVERITY_RANK[Decision.APPROVE] < SEVERITY_RANK[Decision.STEP_UP] < SEVERITY_RANK[Decision.DECLINE]



# Check that the final severity equals the maximum of the individual severities, for every combination and every policy
def test_final_severity_is_the_maximum_of_the_individual_severities():
    verdict_combinations = list_verdict_combinations()
    assert len(verdict_combinations) == 5 + 25 + 125 + 625
    disagreeing_cases = [
        (verdict_combination, uncertainty_policy)
        for verdict_combination in verdict_combinations
        for uncertainty_policy in UNCERTAINTY_POLICIES
        if aggregate(build_results(verdict_combination), uncertainty_policy).decision != highest_expected_severity(verdict_combination, uncertainty_policy)
    ]
    assert disagreeing_cases == []



# Check that appending any further result never lowers the final severity, a failed guard included
def test_appending_a_result_never_lowers_the_final_severity():
    further_results = [build_result(verdict, position = 5) for verdict in ALL_VERDICTS] + [
        build_result(GuardVerdict.UNCERTAIN, position = 5, reason_code = ReasonCode.GUARD_ERROR),
    ]
    lowered_cases = [
        (verdict_combination, uncertainty_policy, further_result.verdict)
        for verdict_combination in list_verdict_combinations()
        for uncertainty_policy in UNCERTAINTY_POLICIES
        for further_result in further_results
        if SEVERITY_RANK[aggregate(build_results(verdict_combination) + [further_result], uncertainty_policy).decision]
        < SEVERITY_RANK[aggregate(build_results(verdict_combination), uncertainty_policy).decision]
    ]
    assert lowered_cases == []









#### Step 3: Check the uncertainty policy, the failed guard and the empty list ####

# Check that UNCERTAIN follows the uncertainty policy
@pytest.mark.parametrize(
    "uncertainty_policy, expected_decision",
    [("ask", Decision.STEP_UP), ("decline", Decision.DECLINE), ("approve", Decision.APPROVE)],
)
def test_uncertain_follows_the_uncertainty_policy(uncertainty_policy, expected_decision):
    aggregation_outcome = aggregate([build_result(GuardVerdict.UNCERTAIN)], uncertainty_policy)
    assert aggregation_outcome.decision == expected_decision
    assert aggregation_outcome.aggregation_record.uncertainty_policy_applied is True



# Check that a failed guard asks the customer under ask and under approve, and declines under decline
@pytest.mark.parametrize(
    "uncertainty_policy, expected_decision",
    [("ask", Decision.STEP_UP), ("approve", Decision.STEP_UP), ("decline", Decision.DECLINE)],
)
def test_failed_guard_never_approves(uncertainty_policy, expected_decision):
    failed_result = build_result(GuardVerdict.UNCERTAIN, reason_code = ReasonCode.GUARD_ERROR)
    aggregation_outcome = aggregate([failed_result], uncertainty_policy)
    assert aggregation_outcome.decision == expected_decision
    assert aggregation_outcome.reason_codes == [ReasonCode.GUARD_ERROR]
    assert aggregation_outcome.aggregation_record.raised_by == ["stub_1"]



# Check that a missing memory of the run never approves either, because it is a failure of the engine and no doubt about the purchase
@pytest.mark.parametrize(
    "uncertainty_policy, expected_decision",
    [("ask", Decision.STEP_UP), ("approve", Decision.STEP_UP), ("decline", Decision.DECLINE)],
)
def test_missing_ledger_never_approves(uncertainty_policy, expected_decision):
    missing_ledger_result = build_result(GuardVerdict.UNCERTAIN, reason_code = ReasonCode.LEDGER_UNAVAILABLE)
    aggregation_outcome = aggregate([missing_ledger_result], uncertainty_policy)
    assert aggregation_outcome.decision == expected_decision
    assert aggregation_outcome.reason_codes == [ReasonCode.LEDGER_UNAVAILABLE]
    assert aggregation_outcome.aggregation_record.raised_by == ["stub_1"]



# Check that a failed guard stays above approve whatever verdict it carries
def test_failed_guard_stays_above_approve_with_every_verdict():
    approving_cases = [
        (verdict, uncertainty_policy)
        for verdict in ALL_VERDICTS
        for uncertainty_policy in UNCERTAINTY_POLICIES
        if measure_result_severity(build_result(verdict, reason_code = ReasonCode.GUARD_ERROR), uncertainty_policy) == Decision.APPROVE
    ]
    assert approving_cases == []



# Check that an empty list of results gives a plain approval
def test_empty_list_of_results_gives_approve():
    aggregation_outcome = aggregate([], "ask")
    assert aggregation_outcome.decision == Decision.APPROVE
    assert aggregation_outcome.reason_codes == []
    assert aggregation_outcome.notes == []
    assert aggregation_outcome.customer_message == DEFAULT_CUSTOMER_MESSAGES[Decision.APPROVE]
    assert aggregation_outcome.aggregation_record.initial == Decision.APPROVE
    assert aggregation_outcome.aggregation_record.final == Decision.APPROVE
    assert aggregation_outcome.aggregation_record.raised_by == []
    assert aggregation_outcome.aggregation_record.uncertainty_policy_applied is False



# Check that an unknown uncertainty policy is refused
def test_unknown_uncertainty_policy_is_refused():
    with pytest.raises(AssertionError, match = "Unknown uncertainty policy"):
        aggregate([build_result(GuardVerdict.PASS)], "sometimes")









#### Step 4: Check the reason codes, the raising guards, the messages and the notes ####

# Check that reason codes run from the highest severity down, in execution order within one severity and without duplicates
def test_reason_codes_run_from_the_highest_severity_down():
    guard_results = [
        build_result(GuardVerdict.STEP_UP, position = 1, reason_code = ReasonCode.UNFAMILIAR_MERCHANT),
        build_result(GuardVerdict.PASS, position = 2, reason_code = ReasonCode.RE_QUOTE_COMPLIANT),
        build_result(GuardVerdict.DECLINE, position = 3, reason_code = ReasonCode.PROHIBITED_ITEM),
        build_result(GuardVerdict.STEP_UP, position = 4, reason_code = ReasonCode.SMALL_OVERSHOOT),
        build_result(GuardVerdict.DECLINE, position = 5, reason_code = ReasonCode.LOOKALIKE_MERCHANT),
        build_result(GuardVerdict.STEP_UP, position = 6, reason_code = ReasonCode.UNFAMILIAR_MERCHANT),
        build_result(GuardVerdict.SKIP, position = 7, reason_code = ReasonCode.GUARD_NOT_BUILT),
        build_result(GuardVerdict.DECLINE, position = 8),
    ]
    aggregation_outcome = aggregate(guard_results, "ask")
    assert aggregation_outcome.decision == Decision.DECLINE
    assert aggregation_outcome.reason_codes == [
        ReasonCode.PROHIBITED_ITEM,
        ReasonCode.LOOKALIKE_MERCHANT,
        ReasonCode.UNFAMILIAR_MERCHANT,
        ReasonCode.SMALL_OVERSHOOT,
    ]



# Check that raised_by names exactly the guards at the final severity, in execution order
def test_raised_by_names_the_guards_at_the_final_severity():
    guard_results = [
        build_result(GuardVerdict.STEP_UP, position = 1),
        build_result(GuardVerdict.DECLINE, position = 2),
        build_result(GuardVerdict.PASS, position = 3),
        build_result(GuardVerdict.DECLINE, position = 4),
    ]
    assert aggregate(guard_results, "ask").aggregation_record.raised_by == ["stub_2", "stub_4"]
    assert aggregate(guard_results[:1], "ask").aggregation_record.raised_by == ["stub_1"]
    assert aggregate(guard_results[2:3], "ask").aggregation_record.raised_by == []



# Check that an UNCERTAIN result settled as approve raises nothing and gives no reason code
def test_uncertain_settled_as_approve_raises_nothing():
    aggregation_outcome = aggregate([build_result(GuardVerdict.UNCERTAIN, reason_code = ReasonCode.RETURN_TERMS_UNKNOWN)], "approve")
    assert aggregation_outcome.decision == Decision.APPROVE
    assert aggregation_outcome.reason_codes == []
    assert aggregation_outcome.aggregation_record.raised_by == []
    assert aggregation_outcome.aggregation_record.uncertainty_policy_applied is True



# Check the default sentence of each decision when no guard gave a message
@pytest.mark.parametrize(
    "verdict, expected_decision",
    [(GuardVerdict.PASS, Decision.APPROVE), (GuardVerdict.STEP_UP, Decision.STEP_UP), (GuardVerdict.DECLINE, Decision.DECLINE)],
)
def test_default_message_per_decision(verdict, expected_decision):
    aggregation_outcome = aggregate([build_result(verdict)], "ask")
    assert aggregation_outcome.decision == expected_decision
    assert aggregation_outcome.customer_message == DEFAULT_CUSTOMER_MESSAGES[expected_decision]
    assert len(set(DEFAULT_CUSTOMER_MESSAGES.values())) == 3



# Check that the message of the first raising guard wins over the default and over every other guard
def test_message_of_the_first_raising_guard_wins():
    guard_results = [
        build_result(GuardVerdict.STEP_UP, position = 1, customer_message = "Message of a lower severity."),
        build_result(GuardVerdict.DECLINE, position = 2, customer_message = "Message of the first raising guard."),
        build_result(GuardVerdict.DECLINE, position = 3, customer_message = "Message of the second raising guard."),
    ]
    assert aggregate(guard_results, "ask").customer_message == "Message of the first raising guard."



# Check that the message of a passing guard never replaces the default sentence of an approval
def test_approval_uses_the_default_message():
    guard_results = [build_result(GuardVerdict.PASS, customer_message = "Message of a passing guard.")]
    assert aggregate(guard_results, "ask").customer_message == DEFAULT_CUSTOMER_MESSAGES[Decision.APPROVE]



# Check that notes pass through in execution order, also when the decision is an approval
def test_notes_pass_through_an_approval():
    guard_results = [
        build_result(GuardVerdict.PASS, position = 1, note = "A 27-inch monitor was already bought on 12 August."),
        build_result(GuardVerdict.PASS, position = 2),
        build_result(GuardVerdict.SKIP, position = 3, note = "Second note."),
    ]
    aggregation_outcome = aggregate(guard_results, "ask")
    assert aggregation_outcome.decision == Decision.APPROVE
    assert aggregation_outcome.notes == ["A 27-inch monitor was already bought on 12 August.", "Second note."]
