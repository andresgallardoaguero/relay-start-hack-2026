# Script: test_pipeline.py
# Purpose: Check that the pipeline runs every guard in order, survives a broken guard, records a complete decision and never looks at an identifier
# Author: Andrés Gallardo
# Date: September 2026

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.engine.guards.base import PlaceholderGuard
from app.engine.guards.g_addon import AddonGuard
from app.engine.guards.g_category_scope import CategoryScopeGuard
from app.engine.guards.g_device import DeviceGuard
from app.engine.guards.g_duplicate_order import DuplicateOrderGuard
from app.engine.guards.g_geo import GeoGuard
from app.engine.guards.g_goal_fulfilled import GoalFulfilledGuard
from app.engine.guards.g_injection import InjectionGuard
from app.engine.guards.g_item_match import ItemMatchGuard
from app.engine.guards.g_item_shop_consistency import ItemShopConsistencyGuard
from app.engine.guards.g_lookalike_merchant import LookalikeMerchantGuard
from app.engine.guards.g_merchant_familiarity import MerchantFamiliarityGuard
from app.engine.guards.g_merchant_type import MerchantTypeGuard
from app.engine.guards.g_order_terms import OrderTermsGuard
from app.engine.guards.g_per_order_limit import PerOrderLimitGuard
from app.engine.guards.g_period_budget import PeriodBudgetGuard
from app.engine.guards.g_session_integrity import SessionIntegrityGuard
from app.engine.guards.g_split_order import SplitOrderGuard
from app.engine.guards.g_velocity import VelocityGuard
from app.engine.guards.registry import build_guard_pipeline, get_default_guard_pipeline, list_placeholder_guard_ids
from app.engine.pipeline import decide
from app.models.decision import (
    ENGINE_VERSION,
    TRACE_VERSION,
    Decision,
    EvidenceItem,
    GuardFamily,
    GuardResult,
    GuardVerdict,
    ReasonCode,
    build_decision_request_body,
)
from app.models.events import read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_empty_ledger_snapshot









#### Step 1: Define the expected pipeline ####

# Locate the engine folder from the location of this file
ENGINE_FOLDER = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine"



# State the execution order, where guard 22 runs before guard 14 and guard 16 runs last, so its question never hides the message of another guard
EXPECTED_EXECUTION_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 22, 14, 15, 17, 18, 19, 20, 21, 23, 16]



# State the id and the family of every guard by its number, written out here independently of the engine
EXPECTED_GUARDS_BY_NUMBER = {
    1: ("authority", "Spending limits"),
    2: ("arithmetic_integrity", "Spending limits"),
    3: ("per_order_limit", "Spending limits"),
    4: ("period_budget", "Spending limits"),
    5: ("split_order", "Spending limits"),
    6: ("category_scope", "Item and terms"),
    7: ("addon", "Item and terms"),
    8: ("merchant_type", "Seller"),
    9: ("lookalike_merchant", "Seller"),
    10: ("merchant_familiarity", "Seller"),
    11: ("device", "Session"),
    12: ("velocity", "Session"),
    13: ("geo", "Session"),
    14: ("session_integrity", "Session"),
    15: ("duplicate_order", "Repeats and manipulation"),
    16: ("goal_fulfilled", "Repeats and manipulation"),
    17: ("injection", "Repeats and manipulation"),
    18: ("item_match", "Item and terms"),
    19: ("order_terms", "Item and terms"),
    20: ("issuer_settings", "Spending limits"),
    21: ("item_shop_consistency", "Item and terms"),
    22: ("usual_purchase_pattern", "Session"),
    23: ("price_against_last_time", "Spending limits"),
}



# State the words that the engine may name in one single function only, and the word it may never name
IDENTIFIER_WORDS = ("scenario_id", "request_id", "source_authorization_id", "replay_order")
NEVER_ALLOWED_WORD = "replay_order"
ONLY_ALLOWED_FUNCTION_START = "def collect_trace_identifiers(event):"



# State an instruction with a clear limit that the CHF 20 of the example message meets exactly and with a kind of goods that its cart line is,
# and the eighteen guards that are built and therefore answer on the facts instead of as placeholders, in execution order
CLEAR_LIMIT_INSTRUCTION = "Buy one grocery item for CHF 20 or less."
BUILT_GUARD_IDS = (
    "per_order_limit", "period_budget", "split_order", "category_scope", "addon", "merchant_type",
    "lookalike_merchant", "merchant_familiarity", "device", "velocity", "geo", "session_integrity",
    "duplicate_order", "injection", "item_match", "order_terms", "item_shop_consistency", "goal_fulfilled",
)









#### Step 2: Define the stub guards and the shared helpers ####

# Answer with a fixed verdict, under the guard's own number and id
@dataclass(frozen = True)
class FixedVerdictGuard:
    guard_number: int
    guard_id: str
    verdict: GuardVerdict
    reason_code: ReasonCode = None
    evidence: tuple = ()
    family: GuardFamily = GuardFamily.SESSION

    def check(self, decision_input, earlier_results):
        return GuardResult(
            guard_number = self.guard_number,
            guard_id = self.guard_id,
            family = self.family,
            verdict = self.verdict,
            reason_code = self.reason_code,
            evidence = list(self.evidence),
        )



# Raise an exception instead of answering
@dataclass(frozen = True)
class RaisingGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily = GuardFamily.SESSION

    def check(self, decision_input, earlier_results):
        raise RuntimeError("This guard is broken on purpose")



# Answer under the number and the id of another guard
@dataclass(frozen = True)
class ForeignIdentityGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily = GuardFamily.SESSION

    def check(self, decision_input, earlier_results):
        return GuardResult(
            guard_number = 1,
            guard_id = "authority",
            family = self.family,
            verdict = GuardVerdict.PASS,
        )



# Remember which earlier results arrived, and pass
@dataclass
class RecordingGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily = GuardFamily.SESSION
    seen_guard_ids: list = field(default_factory = list)
    seen_verdicts: list = field(default_factory = list)

    def check(self, decision_input, earlier_results):
        self.seen_guard_ids = list(earlier_results)
        self.seen_verdicts = [earlier_result.verdict for earlier_result in earlier_results.values()]
        return GuardResult(
            guard_number = self.guard_number,
            guard_id = self.guard_id,
            family = self.family,
            verdict = GuardVerdict.PASS,
        )



# Try to change the earlier results, which the pipeline hands over as read-only
@dataclass(frozen = True)
class WritingGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily = GuardFamily.SESSION

    def check(self, decision_input, earlier_results):
        earlier_results["forged"] = None
        return GuardResult(
            guard_number = self.guard_number,
            guard_id = self.guard_id,
            family = self.family,
            verdict = GuardVerdict.PASS,
        )



# Build a clock that always shows the same moment
def build_fixed_clock(fixed_moment):
    def read_fixed_clock():
        return fixed_moment
    return read_fixed_clock



# Read the example message as a typed event, with the uncertainty policy and the instruction a test asks for
def read_example_event(example_message, uncertainty_policy = "ask", instruction = CLEAR_LIMIT_INSTRUCTION):
    example_message["mandate"]["uncertainty_policy"] = uncertainty_policy
    example_message["mandate"]["instruction"] = instruction
    return read_purchase_message(example_message)



# Decide the example message with the given guards, under an instruction with a clear limit unless a test hands in another one.
# The run has no earlier purchase, which the engine has to be told, because a missing memory is never permission.
def decide_example(example_message, guards = None, uncertainty_policy = "ask", read_clock = None, instruction = CLEAR_LIMIT_INSTRUCTION):
    event = read_example_event(example_message, uncertainty_policy, instruction)
    policy = build_policy_from_mandate(event.mandate)
    return decide(event, policy, build_empty_ledger_snapshot(event), guards = guards, read_clock = read_clock)



# Write a trace as a plain dictionary without the measured running times, which differ from call to call
def dump_trace_without_running_times(decision_trace):
    trace_as_dictionary = decision_trace.model_dump(mode = "json")
    guards_without_running_times = [
        {field_name: field_value for field_name, field_value in guard_as_dictionary.items() if field_name != "elapsed_ms"}
        for guard_as_dictionary in trace_as_dictionary["guards"]
    ]
    return {
        field_name: (guards_without_running_times if field_name == "guards" else field_value)
        for field_name, field_value in trace_as_dictionary.items()
        if field_name != "timings"
    }









#### Step 3: Check the default pipeline ####

# Check that the example message is approved by 5 placeholder guards and the eighteen built guards, in the stated order
def test_default_pipeline_approves_with_5_skipped_guards_and_eighteen_built_guards(example_message):
    decision_trace = decide_example(example_message)
    assert decision_trace.decision == Decision.APPROVE
    assert decision_trace.reason_codes == []
    assert decision_trace.notes == []
    assert decision_trace.customer_message != ""
    assert len(decision_trace.guards) == 23
    assert [guard_result.guard_number for guard_result in decision_trace.guards] == EXPECTED_EXECUTION_ORDER
    assert sorted(guard_result.guard_number for guard_result in decision_trace.guards) == list(range(1, 24))
    assert all(guard_result.elapsed_ms >= 0 for guard_result in decision_trace.guards)

    # Expect SKIP with the reason that the guard is not built from the 5 placeholders, without evidence and without a message
    placeholder_results = [guard_result for guard_result in decision_trace.guards if guard_result.guard_id not in BUILT_GUARD_IDS]
    assert len(placeholder_results) == 5
    assert {guard_result.verdict for guard_result in placeholder_results} == {GuardVerdict.SKIP}
    assert {guard_result.reason_code for guard_result in placeholder_results} == {ReasonCode.GUARD_NOT_BUILT}
    assert all(guard_result.evidence == [] and guard_result.customer_message is None for guard_result in placeholder_results)

    # Expect PASS from the limit per order, because CHF 20 meets a limit of CHF 20 or less,
    # SKIP from the period budget, because the instruction names none,
    # PASS from the split order check, because the run has no earlier order, with four evidence items,
    # PASS from the category scope, because the instruction asks for groceries and the one cart line is groceries,
    # PASS from the add-on guard, because the cart holds the one unit the instruction asks for,
    # SKIP from the merchant type, because the instruction names no kind of shop,
    # PASS from the lookalike check and from the familiarity check, because the card of the example has no history and the instruction asks for no familiar shop,
    # PASS without a signal from the device, the velocity and the country check, because a card without a history has no new device and no new country
    # and no purchase attempt came before this one, and PASS from the session check on no signal at all, with the count against both numbers and the sensitivity,
    # PASS from the check for repeated orders, because the run has no earlier order, with four evidence items,
    # PASS from the check for text aimed at the agent, because the four texts of the shop are ordinary shop text,
    # SKIP from the item match, because "grocery" names a kind of goods and no particular thing,
    # SKIP from the order terms, because the instruction asks for no return period,
    # PASS from the item and shop check, because a groceries line is sold by a groceries shop,
    # and at the end PASS without a question from the check for a goal already bought, because the one thing the instruction asks for was not bought before, with two evidence items
    built_results = [guard_result for guard_result in decision_trace.guards if guard_result.guard_id in BUILT_GUARD_IDS]
    assert [guard_result.guard_id for guard_result in built_results] == list(BUILT_GUARD_IDS)
    assert [guard_result.verdict for guard_result in built_results] == [
        GuardVerdict.PASS, GuardVerdict.SKIP, GuardVerdict.PASS, GuardVerdict.PASS, GuardVerdict.PASS, GuardVerdict.SKIP,
        GuardVerdict.PASS, GuardVerdict.PASS, GuardVerdict.PASS, GuardVerdict.PASS, GuardVerdict.PASS, GuardVerdict.PASS,
        GuardVerdict.PASS, GuardVerdict.PASS,
        GuardVerdict.SKIP, GuardVerdict.SKIP, GuardVerdict.PASS, GuardVerdict.PASS,
    ]
    assert [guard_result.reason_code for guard_result in built_results] == [None] * 18
    assert [len(guard_result.evidence) for guard_result in built_results] == [1, 1, 4, 1, 1, 1, 1, 1, 1, 1, 1, 3, 4, 1, 1, 1, 1, 2]
    assert [guard_result.customer_message for guard_result in built_results] == [None] * 18
    assert [guard_result.signal for guard_result in built_results] == [None] * 18
    assert [guard_result.note for guard_result in built_results] == [None] * 18



# Check that the unchanged example message, which speaks of a stated limit and states none, asks the customer
def test_unchanged_example_message_asks_about_the_unclear_limit(example_message):
    unchanged_instruction = example_message["mandate"]["instruction"]
    assert unchanged_instruction == "Buy one requested item within my stated limit. Ask me when uncertain."
    decision_trace = decide_example(example_message, instruction = unchanged_instruction)
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.ORDER_LIMIT_UNCLEAR]
    assert decision_trace.aggregation.raised_by == ["per_order_limit"]
    assert decision_trace.aggregation.uncertainty_policy_applied is True
    assert "CHF 20.00" in decision_trace.customer_message



# Check the id and the family of each of the 23 guards
def test_every_guard_has_its_id_and_its_family(example_message):
    decision_trace = decide_example(example_message)
    observed_guards_by_number = {
        guard_result.guard_number: (guard_result.guard_id, guard_result.family.value)
        for guard_result in decision_trace.guards
    }
    assert observed_guards_by_number == EXPECTED_GUARDS_BY_NUMBER



# Check that the registry builds 5 placeholders and the eighteen built guards, as a tuple in execution order
def test_registry_lists_5_placeholders_and_eighteen_built_guards():
    guards = build_guard_pipeline()
    assert isinstance(guards, tuple)
    assert len(guards) == 23
    assert [guard.guard_number for guard in guards] == EXPECTED_EXECUTION_ORDER
    built_guards = [guard for guard in guards if not isinstance(guard, PlaceholderGuard)]
    assert len(built_guards) == 18
    assert [type(built_guard) for built_guard in built_guards] == [
        PerOrderLimitGuard, PeriodBudgetGuard, SplitOrderGuard, CategoryScopeGuard, AddonGuard, MerchantTypeGuard,
        LookalikeMerchantGuard, MerchantFamiliarityGuard, DeviceGuard, VelocityGuard, GeoGuard, SessionIntegrityGuard,
        DuplicateOrderGuard, InjectionGuard, ItemMatchGuard, OrderTermsGuard, ItemShopConsistencyGuard, GoalFulfilledGuard,
    ]
    assert [(built_guard.guard_number, built_guard.guard_id, built_guard.family) for built_guard in built_guards] == [
        (3, "per_order_limit", GuardFamily.SPENDING_LIMITS),
        (4, "period_budget", GuardFamily.SPENDING_LIMITS),
        (5, "split_order", GuardFamily.SPENDING_LIMITS),
        (6, "category_scope", GuardFamily.ITEM_AND_TERMS),
        (7, "addon", GuardFamily.ITEM_AND_TERMS),
        (8, "merchant_type", GuardFamily.SELLER),
        (9, "lookalike_merchant", GuardFamily.SELLER),
        (10, "merchant_familiarity", GuardFamily.SELLER),
        (11, "device", GuardFamily.SESSION),
        (12, "velocity", GuardFamily.SESSION),
        (13, "geo", GuardFamily.SESSION),
        (14, "session_integrity", GuardFamily.SESSION),
        (15, "duplicate_order", GuardFamily.REPEATS_AND_MANIPULATION),
        (17, "injection", GuardFamily.REPEATS_AND_MANIPULATION),
        (18, "item_match", GuardFamily.ITEM_AND_TERMS),
        (19, "order_terms", GuardFamily.ITEM_AND_TERMS),
        (21, "item_shop_consistency", GuardFamily.ITEM_AND_TERMS),
        (16, "goal_fulfilled", GuardFamily.REPEATS_AND_MANIPULATION),
    ]
    assert list_placeholder_guard_ids(guards) == [
        EXPECTED_GUARDS_BY_NUMBER[guard_number][0]
        for guard_number in EXPECTED_EXECUTION_ORDER
        if EXPECTED_GUARDS_BY_NUMBER[guard_number][0] not in BUILT_GUARD_IDS
    ]
    assert len(list_placeholder_guard_ids(guards)) == 5
    assert list_placeholder_guard_ids([FixedVerdictGuard(1, "stub_pass", GuardVerdict.PASS)]) == []



# Check that the default pipeline is built once per process, so every decision runs the same guard objects
def test_default_pipeline_is_the_same_object_twice():
    first_pipeline = get_default_guard_pipeline()
    second_pipeline = get_default_guard_pipeline()
    assert first_pipeline is second_pipeline
    assert isinstance(first_pipeline, tuple)
    assert len(first_pipeline) == 23



# Check the fixed parts of the record on the example message
def test_record_carries_the_versions_the_identifiers_and_the_facts(example_message):
    decision_trace = decide_example(example_message)
    assert decision_trace.trace_version == TRACE_VERSION == "1"
    assert decision_trace.engine_version == ENGINE_VERSION == "relay-0.1.0"
    assert decision_trace.ids.model_dump() == {
        "authorization_id": "AU_EXAMPLE_0001",
        "source_authorization_id": "AU_EXAMPLE_0001",
        "request_id": "req_example_0001",
        "mandate_id": "TM_EXAMPLE_0001",
        "scenario_id": "SCEN0000",
    }
    assert decision_trace.facts.model_dump() == {
        "amounts": {"amount": 20.0, "currency": "CHF", "billing_amount_chf": 20.0},
        "merchant": {"merchant_id": "ME_EXAMPLE_0001", "merchant_name": "Example Market", "merchant_category": "groceries", "merchant_country": "CH"},
        "familiarity": {
            "card_is_known": False,
            "card_purchase_count": None,
            "customer_purchase_count": None,
            "issuer_card_count": 0,
            "issuer_customer_count": 0,
            "resembled_shop_name": None,
            "resemblance_score": None,
            "similarity_threshold": 0.85,
            "session": {
                "card_is_known": False,
                "history_purchase_count": None,
                "device_purchase_count": None,
                "local_hour": 10,
                "local_time_text": "10.59",
                "hour_purchase_count": None,
                "shop_country": "CH",
                "country_purchase_count": None,
                "recent_attempt_count": 0,
                "is_recurring_channel": False,
            },
        },
        "extracted_item_facts": None,
        "cart_lines": [{"item_id": "IT_EXAMPLE_0001", "item_name": "Example grocery item", "quantity": 1}],
    }
    assert decision_trace.llm.model_dump() == {"mode": "off", "calls": []}
    assert decision_trace.resolution is None
    assert decision_trace.aggregation.model_dump(mode = "json") == {
        "initial": "approve",
        "final": "approve",
        "raised_by": [],
        "uncertainty_policy_applied": False,
    }
    assert decision_trace.timings.total_ms >= 0









#### Step 4: Check the clock ####

# Check that the margin is exact with a fixed clock, before and after the deadline of 09.00.08
@pytest.mark.parametrize(
    "fixed_moment, expected_margin_ms",
    [
        (datetime(2026, 8, 12, 9, 0, 2, 500000, tzinfo = timezone.utc), 5500.0),
        (datetime(2026, 8, 12, 9, 0, 8, tzinfo = timezone.utc), 0.0),
        (datetime(2026, 8, 12, 9, 0, 9, 250000, tzinfo = timezone.utc), -1250.0),
    ],
)
def test_margin_is_exact_with_a_fixed_clock(example_message, fixed_moment, expected_margin_ms):
    decision_trace = decide_example(example_message, read_clock = build_fixed_clock(fixed_moment))
    assert decision_trace.received_at == fixed_moment
    assert decision_trace.decided_at == fixed_moment
    assert decision_trace.deadline_at == datetime(2026, 8, 12, 9, 0, 8, tzinfo = timezone.utc)
    assert decision_trace.margin_ms == expected_margin_ms



# Check that a late decision is still decided on its facts, because the clock reaches no guard
def test_late_clock_does_not_change_the_decision(example_message):
    late_moment = datetime(2026, 8, 12, 9, 5, 0, tzinfo = timezone.utc)
    decision_trace = decide_example(example_message, read_clock = build_fixed_clock(late_moment))
    assert decision_trace.margin_ms < 0
    assert decision_trace.decision == Decision.APPROVE



# Check that the real clock gives aware moments in order when no clock is handed in
def test_real_clock_gives_ordered_moments(example_message):
    decision_trace = decide_example(example_message)
    assert decision_trace.received_at.tzinfo is not None
    assert decision_trace.received_at <= decision_trace.decided_at









#### Step 5: Check the broken guards ####

# Check that a guard that raises is recorded, asks the customer and does not stop the guards after it
def test_raising_guard_is_recorded_and_the_pipeline_continues(example_message):
    recording_guard = RecordingGuard(3, "stub_recording")
    guards = [FixedVerdictGuard(1, "stub_pass", GuardVerdict.PASS), RaisingGuard(2, "stub_raising"), recording_guard]
    decision_trace = decide_example(example_message, guards = guards, uncertainty_policy = "ask")
    assert [guard_result.guard_id for guard_result in decision_trace.guards] == ["stub_pass", "stub_raising", "stub_recording"]
    failed_result = decision_trace.guards[1]
    assert failed_result.guard_number == 2
    assert failed_result.verdict == GuardVerdict.UNCERTAIN
    assert failed_result.reason_code == ReasonCode.GUARD_ERROR
    assert [evidence_item.value for evidence_item in failed_result.evidence] == ["RuntimeError"]
    assert decision_trace.guards[2].verdict == GuardVerdict.PASS
    assert recording_guard.seen_guard_ids == ["stub_pass", "stub_raising"]
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.GUARD_ERROR]
    assert decision_trace.aggregation.raised_by == ["stub_raising"]



# Check that a guard that raises never approves, whatever the uncertainty policy says
@pytest.mark.parametrize(
    "uncertainty_policy, expected_decision",
    [("ask", Decision.STEP_UP), ("approve", Decision.STEP_UP), ("decline", Decision.DECLINE)],
)
def test_raising_guard_never_approves(example_message, uncertainty_policy, expected_decision):
    decision_trace = decide_example(example_message, guards = [RaisingGuard(1, "stub_raising")], uncertainty_policy = uncertainty_policy)
    assert decision_trace.decision == expected_decision



# Check that a result under the number and the id of another guard is recorded as a failure of the guard that gave it
def test_foreign_identity_is_recorded_as_a_guard_error(example_message):
    decision_trace = decide_example(example_message, guards = [ForeignIdentityGuard(7, "stub_foreign")])
    assert len(decision_trace.guards) == 1
    failed_result = decision_trace.guards[0]
    assert failed_result.guard_number == 7
    assert failed_result.guard_id == "stub_foreign"
    assert failed_result.verdict == GuardVerdict.UNCERTAIN
    assert failed_result.reason_code == ReasonCode.GUARD_ERROR
    assert decision_trace.decision == Decision.STEP_UP



# Check that a guard cannot change the earlier results, because the attempt fails and is recorded
def test_earlier_results_are_read_only(example_message):
    guards = [FixedVerdictGuard(1, "stub_pass", GuardVerdict.PASS), WritingGuard(2, "stub_writing")]
    decision_trace = decide_example(example_message, guards = guards)
    assert [guard_result.guard_id for guard_result in decision_trace.guards] == ["stub_pass", "stub_writing"]
    assert decision_trace.guards[1].reason_code == ReasonCode.GUARD_ERROR
    assert [evidence_item.value for evidence_item in decision_trace.guards[1].evidence] == ["TypeError"]









#### Step 6: Check what a guard receives and that severity only rises ####

# Check that a guard receives the results of the guards before it and none of the guards after it
def test_guard_receives_only_the_results_before_it(example_message):
    first_recording_guard = RecordingGuard(1, "stub_first")
    middle_recording_guard = RecordingGuard(3, "stub_middle")
    guards = [
        first_recording_guard,
        FixedVerdictGuard(2, "stub_step_up", GuardVerdict.STEP_UP),
        middle_recording_guard,
        FixedVerdictGuard(4, "stub_decline", GuardVerdict.DECLINE),
    ]
    decide_example(example_message, guards = guards)
    assert first_recording_guard.seen_guard_ids == []
    assert middle_recording_guard.seen_guard_ids == ["stub_first", "stub_step_up"]
    assert middle_recording_guard.seen_verdicts == [GuardVerdict.PASS, GuardVerdict.STEP_UP]



# Check that a decline followed by a pass stays a decline
def test_decline_followed_by_pass_stays_a_decline(example_message):
    guards = [
        FixedVerdictGuard(1, "stub_decline", GuardVerdict.DECLINE, ReasonCode.PROHIBITED_ITEM),
        FixedVerdictGuard(2, "stub_pass", GuardVerdict.PASS),
    ]
    decision_trace = decide_example(example_message, guards = guards)
    assert decision_trace.decision == Decision.DECLINE
    assert decision_trace.reason_codes == [ReasonCode.PROHIBITED_ITEM]
    assert decision_trace.aggregation.raised_by == ["stub_decline"]
    assert [guard_result.verdict for guard_result in decision_trace.guards] == [GuardVerdict.DECLINE, GuardVerdict.PASS]









#### Step 7: Check the two projections of the record ####

# Check that the full record survives the conversion to plain values and to JSON text
def test_record_survives_json(example_message):
    decision_trace = decide_example(example_message)
    trace_as_dictionary = decision_trace.model_dump(mode = "json")
    trace_as_text = json.dumps(trace_as_dictionary)
    assert json.loads(trace_as_text) == trace_as_dictionary
    assert trace_as_dictionary["decision"] == "approve"
    assert trace_as_dictionary["deadline_at"] == "2026-08-12T09:00:08Z"
    assert trace_as_dictionary["guards"][0]["family"] == "Spending limits"
    assert trace_as_dictionary["guards"][0]["verdict"] == "SKIP"
    assert trace_as_dictionary["guards"][0]["reason_code"] == "GUARD_NOT_BUILT"
    assert len(trace_as_dictionary["guards"]) == 23



# Check that the compact body has the six keys and carries the evidence of the raising guard only
def test_request_body_has_six_keys_and_the_evidence_of_the_raising_guard(example_message):
    raising_evidence = EvidenceItem(fact = "billing_amount_chf", value = 20.0, comparator = "<=", threshold = 10.0, source = "authorization")
    other_evidence = EvidenceItem(fact = "merchant_country", value = "CH", comparator = None, threshold = None, source = "authorization")
    guards = [
        FixedVerdictGuard(1, "stub_step_up", GuardVerdict.STEP_UP, ReasonCode.UNFAMILIAR_MERCHANT, (other_evidence,)),
        FixedVerdictGuard(2, "stub_decline", GuardVerdict.DECLINE, ReasonCode.OVER_PER_ORDER_LIMIT, (raising_evidence,)),
    ]
    request_body = build_decision_request_body(decide_example(example_message, guards = guards))
    assert sorted(request_body) == ["authorization_id", "customer_message", "decision", "engine_version", "evidence", "reason_codes"]
    assert request_body["authorization_id"] == "AU_EXAMPLE_0001"
    assert request_body["decision"] == "decline"
    assert request_body["reason_codes"] == ["OVER_PER_ORDER_LIMIT", "UNFAMILIAR_MERCHANT"]
    assert request_body["engine_version"] == "relay-0.1.0"
    assert request_body["evidence"] == [
        {"fact": "billing_amount_chf", "value": 20.0, "comparator": "<=", "threshold": 10.0, "source": "authorization"},
    ]
    assert json.loads(json.dumps(request_body)) == request_body



# Check that the compact body of an approval has no evidence
def test_request_body_of_an_approval_has_no_evidence(example_message):
    request_body = build_decision_request_body(decide_example(example_message))
    assert request_body["decision"] == "approve"
    assert request_body["reason_codes"] == []
    assert request_body["evidence"] == []



# Check that two calls with the same input and a fixed clock give equal records apart from the measured running times
def test_same_input_gives_equal_records(example_message):
    fixed_clock = build_fixed_clock(datetime(2026, 8, 12, 9, 0, 1, tzinfo = timezone.utc))
    first_trace = decide_example(example_message, read_clock = fixed_clock)
    second_trace = decide_example(example_message, read_clock = fixed_clock)
    assert dump_trace_without_running_times(first_trace) == dump_trace_without_running_times(second_trace)
    assert "guards" in dump_trace_without_running_times(first_trace)
    assert "timings" not in dump_trace_without_running_times(first_trace)









#### Step 8: Check that the engine never looks at an identifier ####

# List the lines of one file that name an identifier word, as pairs of line number and word
def list_lines_naming_identifiers(source_lines):
    return [
        (line_number, identifier_word)
        for line_number, line_text in enumerate(source_lines)
        for identifier_word in IDENTIFIER_WORDS
        if identifier_word in line_text
    ]



# Find the first and the last line of the one function that may name the identifiers
def find_allowed_function_range(source_lines):
    function_start = source_lines.index(ONLY_ALLOWED_FUNCTION_START)
    lines_after_start = list(enumerate(source_lines))[function_start + 1:]
    function_end = next(
        line_number
        for line_number, line_text in lines_after_start
        if line_text.strip() != "" and not line_text.startswith(" ")
    )
    return function_start, function_end



# Check every source file of the engine, where the identifiers appear in one function only and the delivery order nowhere
def test_engine_names_identifiers_in_one_function_only():
    source_paths = sorted(ENGINE_FOLDER.rglob("*.py"))
    assert len(source_paths) >= 6
    source_lines_by_path = {source_path: source_path.read_text(encoding = "utf-8").splitlines() for source_path in source_paths}

    # Expect the delivery order to appear in no file at all
    files_naming_the_delivery_order = [
        source_path.name
        for source_path, source_lines in source_lines_by_path.items()
        if any(NEVER_ALLOWED_WORD in line_text for line_text in source_lines)
    ]
    assert files_naming_the_delivery_order == []



    # Expect the identifiers in the pipeline file only, and there inside the one allowed function only
    pipeline_path = ENGINE_FOLDER / "pipeline.py"
    files_naming_identifiers = [
        source_path
        for source_path, source_lines in source_lines_by_path.items()
        if list_lines_naming_identifiers(source_lines)
    ]
    assert files_naming_identifiers == [pipeline_path]
    pipeline_lines = source_lines_by_path[pipeline_path]
    function_start, function_end = find_allowed_function_range(pipeline_lines)
    lines_outside_the_function = [
        (line_number, identifier_word)
        for line_number, identifier_word in list_lines_naming_identifiers(pipeline_lines)
        if not function_start < line_number < function_end
    ]
    assert lines_outside_the_function == []
