# Script: score.py
# Purpose: Derive one trust score from 0 to 100 for a decided purchase, taken only from the verdicts and signals of the guards, with every point accounted for
# Author: Jonas Lüthi
# Date: September 2026

from typing import Literal, Optional

from pydantic import Field

from app.engine.aggregate import SEVERITY_RANK, measure_result_severity
from app.models.decision import (
    ENGINE_VERSION,
    TRACE_VERSION,
    Decision,
    DecisionTrace,
    FrozenDecisionModel,
    GuardFamily,
    GuardVerdict,
    ReasonCode,
)









#### Step 1: Fix the scale and the points ####

# State the version of the score layout, so a reader of the record knows which table of points produced it
TRUST_SCORE_VERSION = "1"



# Every purchase starts with full trust, and every finding takes points away. Nothing can add points.
STARTING_POINTS = 100



# Take the points of the finding that decided the purchase, which is the strictest one.
# A refusal leaves 30 points and a question leaves 60, so the decision alone already places the score in its band.
LEADING_FINDING_POINTS = {
    Decision.DECLINE: 70,
    Decision.STEP_UP: 40,
}



# Take a few points for every further finding beyond the leading one, so three problems score lower than one
FURTHER_FINDING_POINTS = 5



# Take points for a check that could not be settled and that the customer's policy resolved in favour of the purchase.
# The purchase is approved, but the doubt stays visible in the score.
DOUBT_POINTS = 10



# Take points for a behavioral signal a guard noticed without raising the decision, by the strength the guard gave it
SIGNAL_POINTS = {
    "normal": 5,
    "strong": 10,
}



# Limit what the further findings, the doubts and the signals can take together, so the score never leaves the band of its decision.
# An approval therefore scores 75 or more, a question 35 to 60 and a refusal 5 to 30.
FURTHER_DEDUCTIONS_CAP = 25



# State where the bands of the scale begin, which is what the gauge on screen colors and what a reader of the evidence needs
TRUSTED_FROM = 70
REVIEW_FROM = 35



# Map the decision to the band and the band to the words the customer reads
BAND_BY_DECISION = {
    Decision.APPROVE: "trusted",
    Decision.STEP_UP: "review",
    Decision.DECLINE: "blocked",
}
LABEL_BY_BAND = {
    "trusted": "Trusted",
    "review": "Needs your decision",
    "blocked": "Blocked",
    "not_assessed": "Not assessed",
}



# Order the verdicts by strictness for the strictest verdict of a family, the same order the log screen uses for its chips
VERDICT_STRICTNESS = {
    GuardVerdict.DECLINE: 4,
    GuardVerdict.STEP_UP: 3,
    GuardVerdict.UNCERTAIN: 2,
    GuardVerdict.PASS: 1,
    GuardVerdict.SKIP: 0,
}



# Say why nothing was assessed, by the reason the engine gave about itself
NOT_ASSESSED_SENTENCES = {
    ReasonCode.INVALID_EVENT: "The purchase message could not be read, so no check ran.",
    ReasonCode.ENGINE_TIMEOUT_FALLBACK: "The checks did not finish in time, so nothing was assessed.",
    ReasonCode.GUARD_ERROR: "The checks could not run, so nothing was assessed.",
}
PLACEHOLDERS_ONLY_SENTENCE = "Every check is still a placeholder, so nothing was assessed."
NOTHING_APPLIED_SENTENCE = "No check applied to this purchase, so nothing was assessed."
DEFAULT_NOT_ASSESSED_SENTENCE = "No check ran, so nothing was assessed."



# Name the source written into the evidence items sent to the platform
EVIDENCE_SOURCE = "relay.trust_score.v" + TRUST_SCORE_VERSION









#### Step 2: Define the score record ####

# Describe one deduction, which is one line of the calculation the customer can read
class TrustDeduction(FrozenDecisionModel):

    kind: Literal["leading_finding", "further_finding", "doubt", "signal"]
    guard_id: str
    family: Optional[GuardFamily]
    verdict: Optional[GuardVerdict]
    reason_code: Optional[ReasonCode]
    signal_name: Optional[str] = None
    points: int = Field(ge = 0)
    capped: bool = False
    detail: str



# Describe one family of checks, with its strictest verdict and the points it took
class TrustFamilyScore(FrozenDecisionModel):

    family: GuardFamily
    strictest_verdict: GuardVerdict
    points_lost: int = Field(ge = 0)



# Count how many checks were evaluated, how many found nothing to check, how many are still placeholders and how many failed.
# coverage_share is the evaluated checks over the checks that applied, so a placeholder or a failure lowers it and a check that did not apply does not.
class TrustCoverage(FrozenDecisionModel):

    checks_total: int = Field(ge = 0)
    checks_evaluated: int = Field(ge = 0)
    checks_not_applicable: int = Field(ge = 0)
    checks_not_built: int = Field(ge = 0)
    checks_failed: int = Field(ge = 0)
    coverage_share: float = Field(ge = 0, le = 1)



# Name what the score was computed from, so a reader can trace it back to the record
class TrustBasis(FrozenDecisionModel):

    decision: Decision
    uncertainty_policy: str
    engine_version: str
    trace_version: str
    llm_mode: str



# State the scale, so a screen or a reader of the evidence draws the bands from the record and not from a constant of its own
class TrustScale(FrozenDecisionModel):

    start: int = STARTING_POINTS
    trusted_from: int = TRUSTED_FROM
    review_from: int = REVIEW_FROM



# Describe the trust score of one purchase, where score is empty when nothing was assessed
class TrustScore(FrozenDecisionModel):

    version: str = TRUST_SCORE_VERSION
    score: Optional[int] = Field(default = None, ge = 0, le = 100)
    band: Literal["trusted", "review", "blocked", "not_assessed"]
    label: str
    summary: str
    scale: TrustScale = Field(default_factory = TrustScale)
    deductions: list[TrustDeduction] = Field(default_factory = list)
    families: list[TrustFamilyScore] = Field(default_factory = list)
    coverage: TrustCoverage
    basis: TrustBasis









#### Step 3: Write the words of the calculation ####

# Write a guard id or a signal name in plain words, as in "per order limit"
def describe_guard(guard_id):
    return guard_id.replace("_", " ")



# Write a reason code in plain words, as in "small overshoot", or nothing when there is none
def describe_reason(reason_code):
    if reason_code is None:
        return ""
    return " (" + reason_code.value.lower().replace("_", " ") + ")"



# Write what one guard did at its measured severity, which is the line the customer reads next to the points
def describe_finding(guard_result, severity, uncertainty_policy):
    guard_name = describe_guard(guard_result.guard_id).capitalize()
    reason = describe_reason(guard_result.reason_code)
    if guard_result.reason_code == ReasonCode.GUARD_ERROR:
        return guard_name + " could not run, and a failed check never approves" + reason
    if guard_result.verdict == GuardVerdict.UNCERTAIN:
        policy_words = {"ask": "asks", "decline": "declines", "approve": "approves"}.get(uncertainty_policy, "decides")
        return guard_name + " could not be settled, and your policy " + policy_words + " in doubt" + reason
    if severity == Decision.DECLINE:
        return guard_name + " refused this purchase" + reason
    return guard_name + " asked for your decision" + reason



# Write what one guard noticed without raising the decision
def describe_signal(guard_result):
    return describe_guard(guard_result.guard_id).capitalize() + " noticed a signal: " + describe_guard(guard_result.signal.name) + " (" + guard_result.signal.strength + ")"



# Write the one sentence under the score
def write_summary(band, leading_deduction, further_deductions, coverage):
    further_count = len([deduction for deduction in further_deductions if deduction.kind != "signal"])
    signal_count = len([deduction for deduction in further_deductions if deduction.kind == "signal"])
    additions = []
    if further_count > 0:
        additions.append(str(further_count) + (" further finding" if further_count == 1 else " further findings"))
    if signal_count > 0:
        additions.append(str(signal_count) + (" signal" if signal_count == 1 else " signals"))
    addition_text = " and ".join(additions)



    # An approval speaks about the checks that ran, and a question or a refusal about the finding that decided it
    if band == "trusted":
        if not additions:
            return "All " + str(coverage.checks_evaluated) + " evaluated checks passed."
        return "Every check passed, with " + addition_text + " noted."
    lead = leading_deduction.detail + "."
    if additions:
        verb = " lowers" if further_count + signal_count == 1 else " lower"
        return lead + " " + addition_text[0].upper() + addition_text[1:] + verb + " the score further."
    return lead









#### Step 4: Read the trace ####

# Accept the record as the engine's model or as the plain dictionary a store or a file holds
def as_trace(trace):
    if isinstance(trace, DecisionTrace):
        return trace
    return DecisionTrace.model_validate(trace)



# Tell a placeholder guard, which answered SKIP because it is not built, from a check that ran
def is_placeholder(guard_result):
    return guard_result.verdict == GuardVerdict.SKIP and guard_result.reason_code == ReasonCode.GUARD_NOT_BUILT



# Tell a check that ran and found nothing to check, which is a SKIP by the guard's own reading, from a placeholder
def is_not_applicable(guard_result):
    return guard_result.verdict == GuardVerdict.SKIP and guard_result.reason_code != ReasonCode.GUARD_NOT_BUILT



# Count the checks of a trace, where a check counts as evaluated when it gave a verdict of its own
def count_coverage(guard_results):
    placeholders = [guard_result for guard_result in guard_results if is_placeholder(guard_result)]
    not_applicable = [guard_result for guard_result in guard_results if is_not_applicable(guard_result)]
    failures = [guard_result for guard_result in guard_results if guard_result.reason_code == ReasonCode.GUARD_ERROR]
    evaluated = len(guard_results) - len(placeholders) - len(not_applicable) - len(failures)
    required = len(guard_results) - len(not_applicable)
    if len(guard_results) == 0:
        coverage_share = 0.0
    elif required == 0:
        coverage_share = 1.0
    else:
        coverage_share = round(evaluated / required, 3)
    return TrustCoverage(
        checks_total = len(guard_results),
        checks_evaluated = evaluated,
        checks_not_applicable = len(not_applicable),
        checks_not_built = len(placeholders),
        checks_failed = len(failures),
        coverage_share = coverage_share,
    )



# Build an empty coverage, for a record that has no trace at all
def empty_coverage():
    return TrustCoverage(checks_total = 0, checks_evaluated = 0, checks_not_applicable = 0, checks_not_built = 0, checks_failed = 0, coverage_share = 0.0)



# Describe the basis of a score from a trace
def read_basis(trace, uncertainty_policy):
    return TrustBasis(
        decision = trace.decision,
        uncertainty_policy = str(uncertainty_policy),
        engine_version = trace.engine_version,
        trace_version = trace.trace_version,
        llm_mode = str(trace.llm.mode),
    )



# Give every family its strictest verdict and the points it took
def score_families(guard_results, deductions):
    family_scores = []
    for family in GuardFamily:
        verdicts = [guard_result.verdict for guard_result in guard_results if guard_result.family == family]
        strictest_verdict = max(verdicts, key = VERDICT_STRICTNESS.get) if verdicts else GuardVerdict.SKIP
        points_lost = sum(deduction.points for deduction in deductions if deduction.family == family)
        family_scores.append(TrustFamilyScore(family = family, strictest_verdict = strictest_verdict, points_lost = points_lost))
    return family_scores









#### Step 5: Compute the score ####

# Build the score of a purchase that was not assessed, which is every record without an evaluated or a failed check
def build_not_assessed_score(reason_codes, basis, coverage = None):
    if coverage is None:
        coverage = empty_coverage()
    sentence = DEFAULT_NOT_ASSESSED_SENTENCE
    if coverage.checks_total > 0 and coverage.checks_not_built == coverage.checks_total:
        sentence = PLACEHOLDERS_ONLY_SENTENCE
    elif coverage.checks_total > 0 and coverage.checks_not_applicable > 0:
        sentence = NOTHING_APPLIED_SENTENCE
    for reason_code in reason_codes:
        if reason_code in NOT_ASSESSED_SENTENCES:
            sentence = NOT_ASSESSED_SENTENCES[reason_code]
            break
    return TrustScore(
        score = None,
        band = "not_assessed",
        label = LABEL_BY_BAND["not_assessed"],
        summary = sentence,
        coverage = coverage,
        basis = basis,
    )



# Build the score of a message that could not be read, which has no trace at all
def build_invalid_event_score(uncertainty_policy = "unknown"):
    basis = TrustBasis(
        decision = Decision.STEP_UP,
        uncertainty_policy = str(uncertainty_policy),
        engine_version = ENGINE_VERSION,
        trace_version = TRACE_VERSION,
        llm_mode = "off",
    )
    return build_not_assessed_score([ReasonCode.INVALID_EVENT], basis)



# Take the further deductions in order until the cap is reached, and mark the rest as capped with no points
def apply_cap(further_deductions):
    points_left = FURTHER_DEDUCTIONS_CAP
    capped_deductions = []
    for deduction in further_deductions:
        points_taken = min(deduction.points, points_left)
        points_left = points_left - points_taken
        capped_deductions.append(deduction.model_copy(update = {"points": points_taken, "capped": points_taken < deduction.points}))
    return capped_deductions



# Compute the trust score of one decision record under the customer's uncertainty policy.
# The score starts at 100. The finding that decided the purchase takes the most, every further finding, doubt and signal takes a little more,
# and the further deductions together are capped so the score stays in the band of the decision. Nothing ever adds points.
def score_trace(trace, uncertainty_policy = "ask"):
    trace = as_trace(trace)
    basis = read_basis(trace, uncertainty_policy)
    coverage = count_coverage(trace.guards)



    # A record without an evaluated or a failed check, such as a timeout fallback, was not assessed.
    # A failed check counts here, because a failure is a finding and never a pass.
    if coverage.checks_evaluated + coverage.checks_failed == 0:
        return build_not_assessed_score(trace.reason_codes, basis, coverage)



    # Measure every guard the way the aggregation does, so the score and the decision rest on the same reading of the verdicts
    severities = {guard_result.guard_id: measure_result_severity(guard_result, uncertainty_policy) for guard_result in trace.guards}
    final_decision = trace.decision
    final_rank = SEVERITY_RANK[final_decision]



    # The leading finding is the first guard, in execution order, at the severity of the decision.
    # When the decision is above every guard, which the engine may raise on its own, the engine itself is named.
    leading_deduction = None
    leading_guard_id = None
    if final_decision != Decision.APPROVE:
        raising_results = [guard_result for guard_result in trace.guards if SEVERITY_RANK[severities[guard_result.guard_id]] == final_rank]
        if raising_results:
            leading_result = raising_results[0]
            leading_guard_id = leading_result.guard_id
            leading_deduction = TrustDeduction(
                kind = "leading_finding",
                guard_id = leading_result.guard_id,
                family = leading_result.family,
                verdict = leading_result.verdict,
                reason_code = leading_result.reason_code,
                points = LEADING_FINDING_POINTS[final_decision],
                detail = describe_finding(leading_result, final_decision, uncertainty_policy),
            )
        else:
            first_reason = trace.reason_codes[0] if trace.reason_codes else None
            leading_deduction = TrustDeduction(
                kind = "leading_finding",
                guard_id = "engine",
                family = None,
                verdict = None,
                reason_code = first_reason,
                points = LEADING_FINDING_POINTS[final_decision],
                detail = "The engine " + ("refused this purchase" if final_decision == Decision.DECLINE else "asked for your decision") + describe_reason(first_reason),
            )



    # Every other guard above approve is a further finding, every unsettled guard the policy approved is a doubt,
    # and a signal counts only on a guard that did not raise the decision itself
    further_deductions = []
    for guard_result in trace.guards:
        if guard_result.guard_id == leading_guard_id:
            continue
        severity = severities[guard_result.guard_id]
        if SEVERITY_RANK[severity] > SEVERITY_RANK[Decision.APPROVE]:
            further_deductions.append(TrustDeduction(
                kind = "further_finding",
                guard_id = guard_result.guard_id,
                family = guard_result.family,
                verdict = guard_result.verdict,
                reason_code = guard_result.reason_code,
                points = FURTHER_FINDING_POINTS,
                detail = describe_finding(guard_result, severity, uncertainty_policy),
            ))
            continue
        if guard_result.verdict == GuardVerdict.UNCERTAIN:
            further_deductions.append(TrustDeduction(
                kind = "doubt",
                guard_id = guard_result.guard_id,
                family = guard_result.family,
                verdict = guard_result.verdict,
                reason_code = guard_result.reason_code,
                points = DOUBT_POINTS,
                detail = describe_finding(guard_result, severity, uncertainty_policy),
            ))
        if guard_result.signal is not None:
            further_deductions.append(TrustDeduction(
                kind = "signal",
                guard_id = guard_result.guard_id,
                family = guard_result.family,
                verdict = guard_result.verdict,
                reason_code = guard_result.reason_code,
                signal_name = guard_result.signal.name,
                points = SIGNAL_POINTS[guard_result.signal.strength],
                detail = describe_signal(guard_result),
            ))
    further_deductions = apply_cap(further_deductions)



    # Add the points up, where every listed deduction carries exactly the points it took
    deductions = ([leading_deduction] if leading_deduction is not None else []) + further_deductions
    score = STARTING_POINTS - sum(deduction.points for deduction in deductions)
    band = BAND_BY_DECISION[final_decision]
    return TrustScore(
        score = score,
        band = band,
        label = LABEL_BY_BAND[band],
        summary = write_summary(band, leading_deduction, further_deductions, coverage),
        deductions = deductions,
        families = score_families(trace.guards, deductions),
        coverage = coverage,
        basis = basis,
    )









#### Step 6: Write the score into the answer to the platform ####

# Read a field of a score given as the model or as the plain dictionary a store holds
def read_score_field(trust_score, field_name):
    if isinstance(trust_score, TrustScore):
        return getattr(trust_score, field_name)
    return trust_score.get(field_name)



# Write the deductions as one line of text, as in "per_order_limit SMALL_OVERSHOOT -40"
def write_deduction_line(trust_score):
    deductions = read_score_field(trust_score, "deductions")
    parts = []
    for deduction in deductions:
        guard_id = deduction.guard_id if isinstance(deduction, TrustDeduction) else deduction["guard_id"]
        reason_code = deduction.reason_code if isinstance(deduction, TrustDeduction) else deduction.get("reason_code")
        signal_name = deduction.signal_name if isinstance(deduction, TrustDeduction) else deduction.get("signal_name")
        points = deduction.points if isinstance(deduction, TrustDeduction) else deduction["points"]
        reason_text = reason_code.value if isinstance(reason_code, ReasonCode) else reason_code
        label = guard_id + " " + (reason_text if reason_text else ("signal:" + signal_name if signal_name else "signal"))
        parts.append(label + " -" + str(points))
    return "; ".join(parts) if parts else "none"



# Build the evidence items that carry the score in the answer to the platform.
# They have the same five fields as the evidence of every guard, so the answer keeps the format the platform documents.
def build_trust_evidence(trust_score):
    return [
        {
            "fact": "trust_score",
            "value": read_score_field(trust_score, "score"),
            "comparator": ">=",
            "threshold": TRUSTED_FROM,
            "source": EVIDENCE_SOURCE,
        },
        {
            "fact": "trust_band",
            "value": read_score_field(trust_score, "band"),
            "comparator": None,
            "threshold": None,
            "source": EVIDENCE_SOURCE,
        },
        {
            "fact": "trust_deductions",
            "value": write_deduction_line(trust_score),
            "comparator": None,
            "threshold": None,
            "source": EVIDENCE_SOURCE,
        },
    ]
