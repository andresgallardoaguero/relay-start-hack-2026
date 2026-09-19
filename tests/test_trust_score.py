# Script: test_trust_score.py
# Purpose: Check that the trust score falls with every finding and never rises, stays in the band of its decision, accounts for every point and reads real traces
# Author: Jonas Lüthi
# Date: September 2026

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.aggregate import aggregate
from app.engine.pipeline import decide
from app.models.decision import (
    Decision,
    DecisionTrace,
    GuardFamily,
    GuardResult,
    GuardSignal,
    GuardVerdict,
    ReasonCode,
    TraceAmounts,
    TraceFacts,
    TraceIdentifiers,
    TraceLanguageModel,
    TraceMerchant,
    TraceTimings,
)
from app.models.events import read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.trust.score import (
    BAND_BY_DECISION,
    DOUBT_POINTS,
    FURTHER_DEDUCTIONS_CAP,
    FURTHER_FINDING_POINTS,
    LEADING_FINDING_POINTS,
    SIGNAL_POINTS,
    STARTING_POINTS,
    build_invalid_event_score,
    build_trust_evidence,
    score_trace,
    write_deduction_line,
)









#### Step 1: Define the shared helpers ####

# List the three uncertainty policies and the verdicts a guard can give
UNCERTAINTY_POLICIES = ("ask", "decline", "approve")
ALL_VERDICTS = tuple(GuardVerdict)



# Build one guard result, where the position gives it a number, an id and a family
def build_result(verdict, position = 1, reason_code = None, signal_strength = None, family = GuardFamily.SPENDING_LIMITS, guard_id = None):
    return GuardResult(
        guard_number = position,
        guard_id = guard_id if guard_id is not None else "stub_" + str(position),
        family = family,
        verdict = verdict,
        reason_code = reason_code,
        signal = GuardSignal(name = "stub_signal", strength = signal_strength) if signal_strength is not None else None,
    )



# Build a placeholder result, which is how a guard that is not built answers
def build_placeholder(position):
    return build_result(GuardVerdict.SKIP, position, reason_code = ReasonCode.GUARD_NOT_BUILT)



# Build a complete decision record around the given results, decided by the engine's own aggregation unless told otherwise
def build_trace(guard_results, uncertainty_policy = "ask", decision = None, reason_codes = None):
    outcome = aggregate(guard_results, uncertainty_policy)
    now = datetime(2026, 9, 19, 8, 0, 0, tzinfo = timezone.utc)
    return DecisionTrace(
        ids = TraceIdentifiers(authorization_id = "LA_TEST", source_authorization_id = "AU_TEST", request_id = "req_test", mandate_id = "TM_TEST", scenario_id = "SCEN9999"),
        received_at = now,
        decided_at = now,
        deadline_at = now + timedelta(seconds = 8),
        margin_ms = 8000.0,
        decision = decision if decision is not None else outcome.decision,
        reason_codes = reason_codes if reason_codes is not None else outcome.reason_codes,
        customer_message = outcome.customer_message,
        notes = outcome.notes,
        facts = TraceFacts(
            amounts = TraceAmounts(amount = 20.0, currency = "CHF", billing_amount_chf = 20.0),
            merchant = TraceMerchant(merchant_id = "ME_TEST", merchant_name = "Test shop", merchant_category = "groceries", merchant_country = "CH"),
        ),
        guards = list(guard_results),
        aggregation = outcome.aggregation_record,
        llm = TraceLanguageModel(),
        timings = TraceTimings(total_ms = 1.0),
    )



# Score a list of verdicts under a policy, with one PASS guard at the end so the trace always has an evaluated check
def score_verdicts(verdicts, uncertainty_policy, signal_strength = None):
    results = [build_result(verdict, position) for position, verdict in enumerate(verdicts, start = 1)]
    results.append(build_result(GuardVerdict.PASS, len(verdicts) + 1, signal_strength = signal_strength))
    return score_trace(build_trace(results, uncertainty_policy), uncertainty_policy)



# List every combination of the five verdicts over one to three results
def list_verdict_combinations():
    return [
        verdict_combination
        for length in (1, 2, 3)
        for verdict_combination in itertools.product(ALL_VERDICTS, repeat = length)
    ]









#### Step 2: The plain cases ####

# A purchase every check passes scores full trust, and a check that found nothing to check is not counted as evaluated
def test_clean_approval_scores_full_trust():
    trust = score_verdicts([GuardVerdict.PASS, GuardVerdict.SKIP], "ask")
    assert trust.score == STARTING_POINTS and trust.band == "trusted" and trust.label == "Trusted"
    assert trust.deductions == [] and trust.summary == "All 2 evaluated checks passed."
    assert trust.coverage.checks_total == 3 and trust.coverage.checks_evaluated == 2
    assert trust.coverage.checks_not_applicable == 1 and trust.coverage.checks_not_built == 0 and trust.coverage.coverage_share == 1.0



# Coverage is the evaluated checks over the checks that applied, so a placeholder or a failure lowers it and a check that did not apply does not
def test_coverage_counts_placeholders_and_failures_against_the_checks_that_applied():
    results = [
        build_result(GuardVerdict.PASS, 1),
        build_result(GuardVerdict.SKIP, 2),
        build_placeholder(3),
        build_placeholder(4),
        build_result(GuardVerdict.UNCERTAIN, 5, reason_code = ReasonCode.GUARD_ERROR),
    ]
    trust = score_trace(build_trace(results, "ask"), "ask")
    assert trust.coverage.checks_total == 5 and trust.coverage.checks_evaluated == 1
    assert trust.coverage.checks_not_applicable == 1 and trust.coverage.checks_not_built == 2 and trust.coverage.checks_failed == 1
    assert trust.coverage.coverage_share == 0.25



    # A trace in which no check applied has no score, and says so
    nothing_applied = score_trace(build_trace([build_result(GuardVerdict.SKIP, 1), build_placeholder(2)], "ask"), "ask")
    assert nothing_applied.score is None and nothing_applied.summary == "No check applied to this purchase, so nothing was assessed."
    assert nothing_applied.coverage.coverage_share == 0.0



# A question leaves 60 and a refusal leaves 30, and the finding that decided the purchase is named first
def test_question_and_refusal_place_the_score_in_their_band():
    question = score_verdicts([GuardVerdict.STEP_UP], "ask")
    assert question.score == 60 and question.band == "review" and question.label == "Needs your decision"
    assert question.deductions[0].kind == "leading_finding" and question.deductions[0].points == LEADING_FINDING_POINTS[Decision.STEP_UP]
    refusal = score_verdicts([GuardVerdict.DECLINE], "ask")
    assert refusal.score == 30 and refusal.band == "blocked" and refusal.label == "Blocked"
    assert refusal.deductions[0].points == LEADING_FINDING_POINTS[Decision.DECLINE]



# An unsettled check follows the customer's uncertainty policy, and a doubt the policy approves still shows in the score
def test_uncertain_verdict_follows_the_uncertainty_policy():
    assert score_verdicts([GuardVerdict.UNCERTAIN], "ask").score == 60
    assert score_verdicts([GuardVerdict.UNCERTAIN], "decline").score == 30
    approved_in_doubt = score_verdicts([GuardVerdict.UNCERTAIN], "approve")
    assert approved_in_doubt.score == STARTING_POINTS - DOUBT_POINTS and approved_in_doubt.band == "trusted"
    assert approved_in_doubt.deductions[0].kind == "doubt"
    assert "your policy approves in doubt" in approved_in_doubt.deductions[0].detail



# Further findings and signals take a little more, so three problems score lower than one
def test_further_findings_and_signals_take_a_little_more():
    two_questions = score_verdicts([GuardVerdict.STEP_UP, GuardVerdict.STEP_UP], "ask")
    assert two_questions.score == STARTING_POINTS - LEADING_FINDING_POINTS[Decision.STEP_UP] - FURTHER_FINDING_POINTS
    assert [deduction.kind for deduction in two_questions.deductions] == ["leading_finding", "further_finding"]
    with_signal = score_verdicts([GuardVerdict.STEP_UP], "ask", signal_strength = "normal")
    assert with_signal.score == 60 - SIGNAL_POINTS["normal"]
    with_strong_signal = score_verdicts([GuardVerdict.STEP_UP], "ask", signal_strength = "strong")
    assert with_strong_signal.score == 60 - SIGNAL_POINTS["strong"]
    assert with_strong_signal.summary == "Stub 1 asked for your decision. 1 signal lowers the score further."



# A signal on the guard that raised the decision is not counted a second time
def test_signal_on_a_raising_guard_is_not_counted_twice():
    results = [build_result(GuardVerdict.STEP_UP, 1, reason_code = ReasonCode.SMALL_OVERSHOOT, signal_strength = "strong")]
    trust = score_trace(build_trace(results, "ask"), "ask")
    assert trust.score == 60 and len(trust.deductions) == 1









#### Step 3: The cap and the properties ####

# The further deductions are capped, so a question never falls into the band of a refusal and an approval stays trusted
def test_cap_keeps_the_score_in_the_band_of_its_decision():
    many_questions = score_verdicts([GuardVerdict.STEP_UP] * 9, "ask")
    assert many_questions.score == STARTING_POINTS - LEADING_FINDING_POINTS[Decision.STEP_UP] - FURTHER_DEDUCTIONS_CAP
    assert many_questions.score >= many_questions.scale.review_from
    assert sum(deduction.points for deduction in many_questions.deductions) == STARTING_POINTS - many_questions.score
    assert [deduction.capped for deduction in many_questions.deductions][-3:] == [True, True, True]



    # Six strong signals on an approval take 25 and not 60
    results = [build_result(GuardVerdict.PASS, position, signal_strength = "strong") for position in range(1, 7)]
    trust = score_trace(build_trace(results, "ask"), "ask")
    assert trust.score == STARTING_POINTS - FURTHER_DEDUCTIONS_CAP and trust.band == "trusted"
    assert trust.score >= trust.scale.trusted_from



# Adding a finding or a signal never raises the score, under every policy
@pytest.mark.parametrize("uncertainty_policy", UNCERTAINTY_POLICIES)
def test_score_never_rises_when_a_finding_is_added(uncertainty_policy):
    for verdicts in list_verdict_combinations():
        base_score = score_verdicts(list(verdicts), uncertainty_policy).score
        for added_verdict in (GuardVerdict.STEP_UP, GuardVerdict.DECLINE, GuardVerdict.UNCERTAIN):
            assert score_verdicts(list(verdicts) + [added_verdict], uncertainty_policy).score <= base_score
        for signal_strength in ("normal", "strong"):
            assert score_verdicts(list(verdicts), uncertainty_policy, signal_strength = signal_strength).score <= base_score



# The band always follows the decision, the score always lies in the band's range, and every point is accounted for
@pytest.mark.parametrize("uncertainty_policy", UNCERTAINTY_POLICIES)
def test_band_and_points_agree_with_the_decision_and_the_scale(uncertainty_policy):
    for verdicts in list_verdict_combinations():
        for signal_strength in (None, "normal", "strong"):
            results = [build_result(verdict, position) for position, verdict in enumerate(verdicts, start = 1)]
            results.append(build_result(GuardVerdict.PASS, len(verdicts) + 1, signal_strength = signal_strength))
            trace = build_trace(results, uncertainty_policy)
            trust = score_trace(trace, uncertainty_policy)
            assert trust.band == BAND_BY_DECISION[trace.decision]
            assert trust.basis.decision == trace.decision and trust.basis.uncertainty_policy == uncertainty_policy
            assert STARTING_POINTS - sum(deduction.points for deduction in trust.deductions) == trust.score
            if trust.band == "trusted":
                assert trust.scale.trusted_from <= trust.score <= STARTING_POINTS
            elif trust.band == "review":
                assert trust.scale.review_from <= trust.score < trust.scale.trusted_from
            else:
                assert 0 <= trust.score < trust.scale.review_from



# The same trace scores the same twice, and scores the same as its plain dictionary
def test_score_is_deterministic_and_reads_a_plain_dictionary():
    results = [build_result(GuardVerdict.STEP_UP, 1, reason_code = ReasonCode.OFF_SCOPE_ITEM), build_result(GuardVerdict.PASS, 2, signal_strength = "normal")]
    trace = build_trace(results, "ask")
    first = score_trace(trace, "ask")
    second = score_trace(trace.model_dump(mode = "json"), "ask")
    assert first == second and first.score == 55









#### Step 4: Records that were not assessed ####

# A record without evaluated checks, such as a timeout fallback or a trace of placeholders only, is not assessed
def test_records_without_evaluated_checks_are_not_assessed():
    fallback = build_trace([], "ask", decision = Decision.STEP_UP, reason_codes = [ReasonCode.ENGINE_TIMEOUT_FALLBACK])
    trust = score_trace(fallback, "ask")
    assert trust.score is None and trust.band == "not_assessed" and trust.label == "Not assessed"
    assert trust.summary == "The checks did not finish in time, so nothing was assessed."
    assert trust.deductions == [] and trust.coverage.checks_evaluated == 0



    # Twenty-three placeholders count as nothing evaluated
    placeholders_only = build_trace([build_placeholder(position) for position in range(1, 24)], "ask")
    trust = score_trace(placeholders_only, "ask")
    assert trust.score is None and trust.coverage.checks_total == 23 and trust.coverage.checks_not_built == 23
    assert trust.summary == "Every check is still a placeholder, so nothing was assessed." and trust.coverage.coverage_share == 0.0



    # A message that could not be read has no trace and is not assessed either
    invalid = build_invalid_event_score("ask")
    assert invalid.score is None and invalid.band == "not_assessed"
    assert invalid.summary == "The purchase message could not be read, so no check ran."
    assert invalid.basis.decision == Decision.STEP_UP and invalid.basis.uncertainty_policy == "ask"



# A guard that failed counts as a failed check and as a finding, and never as a pass, whatever the policy
def test_failed_guard_counts_as_failed_and_as_finding():
    results = [build_result(GuardVerdict.UNCERTAIN, 1, reason_code = ReasonCode.GUARD_ERROR), build_result(GuardVerdict.PASS, 2)]
    trust = score_trace(build_trace(results, "approve"), "approve")
    assert trust.coverage.checks_failed == 1
    assert trust.score == 60 and trust.band == "review"
    assert "could not run" in trust.deductions[0].detail



# A decision the engine raised above every guard names the engine as the leading finding
def test_engine_raise_above_the_guards_is_named():
    results = [build_result(GuardVerdict.PASS, 1)]
    trace = build_trace(results, "ask", decision = Decision.DECLINE, reason_codes = [ReasonCode.OVER_PERIOD_LIMIT])
    trust = score_trace(trace, "ask")
    assert trust.score == 30 and trust.deductions[0].guard_id == "engine"
    assert trust.deductions[0].detail == "The engine refused this purchase (over period limit)"









#### Step 5: Families, evidence and a real trace ####

# Every family reports its strictest verdict and the points it took
def test_families_carry_their_strictest_verdict_and_points():
    results = [
        build_result(GuardVerdict.STEP_UP, 1, reason_code = ReasonCode.SMALL_OVERSHOOT, family = GuardFamily.SPENDING_LIMITS),
        build_result(GuardVerdict.PASS, 2, family = GuardFamily.SPENDING_LIMITS),
        build_result(GuardVerdict.PASS, 3, signal_strength = "normal", family = GuardFamily.SELLER),
        build_placeholder(4),
    ]
    trust = score_trace(build_trace(results, "ask"), "ask")
    by_family = {family_score.family: family_score for family_score in trust.families}
    assert [family_score.family for family_score in trust.families] == list(GuardFamily)
    assert by_family[GuardFamily.SPENDING_LIMITS].strictest_verdict == GuardVerdict.STEP_UP and by_family[GuardFamily.SPENDING_LIMITS].points_lost == 40
    assert by_family[GuardFamily.SELLER].strictest_verdict == GuardVerdict.PASS and by_family[GuardFamily.SELLER].points_lost == 5
    assert by_family[GuardFamily.SESSION].strictest_verdict == GuardVerdict.SKIP and by_family[GuardFamily.SESSION].points_lost == 0



# The evidence items keep the five fields of every guard's evidence and carry plain values only
def test_evidence_items_keep_the_shape_of_the_platform():
    results = [build_result(GuardVerdict.STEP_UP, 1, reason_code = ReasonCode.SMALL_OVERSHOOT, guard_id = "per_order_limit"), build_result(GuardVerdict.PASS, 2, signal_strength = "normal", guard_id = "device")]
    trust = score_trace(build_trace(results, "ask"), "ask")
    evidence = build_trust_evidence(trust)
    assert [item["fact"] for item in evidence] == ["trust_score", "trust_band", "trust_deductions"]
    for item in evidence:
        assert set(item) == {"fact", "value", "comparator", "threshold", "source"}
        assert all(value is None or isinstance(value, (str, int, float, bool)) for value in item.values())
    assert evidence[0]["value"] == 55 and evidence[0]["threshold"] == trust.scale.trusted_from
    assert evidence[1]["value"] == "review"
    assert evidence[2]["value"] == "per_order_limit SMALL_OVERSHOOT -40; device signal:stub_signal -5"



    # The same line comes from the plain dictionary a store holds, and an approval without deductions says none
    assert write_deduction_line(trust.model_dump(mode = "json")) == evidence[2]["value"]
    assert write_deduction_line(score_verdicts([GuardVerdict.PASS], "ask")) == "none"



# A trace of the real pipeline scores the same as its dictionary and reports all 23 checks
def test_real_pipeline_trace_is_scored(example_message):
    event = read_purchase_message(example_message)
    policy = build_policy_from_mandate(event.mandate)
    trace = decide(event, policy, None)
    trust = score_trace(trace, event.mandate.uncertainty_policy)
    assert trust == score_trace(trace.model_dump(mode = "json"), event.mandate.uncertainty_policy)
    assert isinstance(trust.score, int) and trust.band == BAND_BY_DECISION[trace.decision]
    assert trust.coverage.checks_total == 23 and trust.coverage.checks_evaluated >= 1
    assert trust.coverage.checks_evaluated + trust.coverage.checks_not_applicable + trust.coverage.checks_not_built + trust.coverage.checks_failed == 23
    assert 0 < trust.coverage.coverage_share <= 1
    assert trust.basis.engine_version == trace.engine_version and trust.basis.llm_mode == "off"
