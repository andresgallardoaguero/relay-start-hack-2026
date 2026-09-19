# Script: aggregate.py
# Purpose: Turn the results of all guards into one decision, where severity starts at approve and can only rise
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.models.decision import AggregationRecord, Decision, GuardVerdict, ReasonCode









#### Step 1: Define the severity order ####

# Order the three decisions by severity, where approve is the lowest and decline the highest
SEVERITY_RANK = {
    Decision.APPROVE: 0,
    Decision.STEP_UP: 1,
    Decision.DECLINE: 2,
}



# Map every verdict that decides on its own to its severity, where UNCERTAIN is missing because the customer's policy settles it
SEVERITY_BY_VERDICT = {
    GuardVerdict.PASS: Decision.APPROVE,
    GuardVerdict.SKIP: Decision.APPROVE,
    GuardVerdict.STEP_UP: Decision.STEP_UP,
    GuardVerdict.DECLINE: Decision.DECLINE,
}



# Map the customer's uncertainty policy to the severity of an UNCERTAIN verdict
SEVERITY_BY_UNCERTAINTY_POLICY = {
    "ask": Decision.STEP_UP,
    "decline": Decision.DECLINE,
    "approve": Decision.APPROVE,
}



# Name the reasons that report a failure of the engine itself, which are a guard that broke and a memory of the run that is missing.
# Neither is a doubt about the purchase, so neither may ever approve.
ENGINE_FAILURE_REASON_CODES = (ReasonCode.GUARD_ERROR, ReasonCode.LEDGER_UNAVAILABLE)



# Map the uncertainty policy to the lowest severity of a guard that failed, which is never approve
GUARD_ERROR_SEVERITY_BY_UNCERTAINTY_POLICY = {
    "ask": Decision.STEP_UP,
    "decline": Decision.DECLINE,
    "approve": Decision.STEP_UP,
}



# State the sentence the customer reads when the raising guard gave no message of its own
DEFAULT_CUSTOMER_MESSAGES = {
    Decision.APPROVE: "Approved. This purchase is within your instruction.",
    Decision.STEP_UP: "Please confirm this purchase. One check could not clear it without you.",
    Decision.DECLINE: "Declined. This purchase breaks your instruction.",
}









#### Step 2: Measure the severity of one result ####

# Return the higher of two severities
def pick_higher_severity(first_severity, second_severity):
    return max(first_severity, second_severity, key = SEVERITY_RANK.get)



# Measure the severity of one result under the customer's uncertainty policy
def measure_result_severity(guard_result, uncertainty_policy):
    assert uncertainty_policy in SEVERITY_BY_UNCERTAINTY_POLICY, "Unknown uncertainty policy " + str(uncertainty_policy)

    # Read the severity of the verdict, where UNCERTAIN follows the uncertainty policy
    if guard_result.verdict == GuardVerdict.UNCERTAIN:
        verdict_severity = SEVERITY_BY_UNCERTAINTY_POLICY[uncertainty_policy]
    else:
        verdict_severity = SEVERITY_BY_VERDICT[guard_result.verdict]



    # Lift a failure of the engine to at least step_up, so a failure never approves whatever the policy says
    if guard_result.reason_code in ENGINE_FAILURE_REASON_CODES:
        return pick_higher_severity(verdict_severity, GUARD_ERROR_SEVERITY_BY_UNCERTAINTY_POLICY[uncertainty_policy])
    return verdict_severity









#### Step 3: Combine all results into one decision ####

# Describe the outcome of the aggregation
@dataclass(frozen = True)
class AggregationOutcome:
    decision: Decision
    reason_codes: list
    customer_message: str
    notes: list
    aggregation_record: AggregationRecord



# Combine the results, given in execution order, into one decision with its reasons, message and notes
def aggregate(guard_results, uncertainty_policy):

    # Measure every result and take the highest severity, starting at approve
    severities = [measure_result_severity(guard_result, uncertainty_policy) for guard_result in guard_results]
    final_severity = max([Decision.APPROVE] + severities, key = SEVERITY_RANK.get)



    # List the reason codes of every result above approve, highest severity first and execution order within one severity
    results_above_approve = [
        (guard_result, severity)
        for guard_result, severity in zip(guard_results, severities)
        if SEVERITY_RANK[severity] > SEVERITY_RANK[Decision.APPROVE]
    ]
    results_by_falling_severity = sorted(
        results_above_approve,
        key = lambda result_and_severity: SEVERITY_RANK[result_and_severity[1]],
        reverse = True,
    )
    reason_codes = list(dict.fromkeys(
        guard_result.reason_code
        for guard_result, severity in results_by_falling_severity
        if guard_result.reason_code is not None
    ))



    # Name the guards at the final severity in execution order, which is nobody for an approval
    raising_results = [
        guard_result
        for guard_result, severity in results_above_approve
        if severity == final_severity
    ]
    raised_by = [guard_result.guard_id for guard_result in raising_results]



    # Take the message of the first raising guard, and the default sentence when it gave none or nothing was raised
    customer_message = DEFAULT_CUSTOMER_MESSAGES[final_severity]
    if raising_results and raising_results[0].customer_message is not None:
        customer_message = raising_results[0].customer_message



    # Collect the note of every result that has one, in execution order, also for an approval
    notes = [guard_result.note for guard_result in guard_results if guard_result.note is not None]



    # Record how the decision came about
    aggregation_record = AggregationRecord(
        initial = Decision.APPROVE,
        final = final_severity,
        raised_by = raised_by,
        uncertainty_policy_applied = any(guard_result.verdict == GuardVerdict.UNCERTAIN for guard_result in guard_results),
    )
    return AggregationOutcome(
        decision = final_severity,
        reason_codes = reason_codes,
        customer_message = customer_message,
        notes = notes,
        aggregation_record = aggregation_record,
    )
