# Script: test_guard_goal_fulfilled.py
# Purpose: Check that an order for the one requested thing after that thing was already bought carries a note, asks or is skipped as the policy says, and that nothing else does
# Author: Andrés Gallardo
# Date: September 2026

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_goal_fulfilled import GoalFulfilledGuard
from app.engine.guards.registry import get_default_guard_pipeline
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardResult, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy, RequestedItem
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_ledger_snapshot_for_event









#### Step 1: Define the shared helpers ####

# Locate the source file that must never name an identifier
GUARD_SOURCE_PATH = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine" / "guards" / "g_goal_fulfilled.py"
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "card_id", "customer_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
GOAL_FULFILLED_GUARD = GoalFulfilledGuard(
    guard_number = 16,
    guard_id = "goal_fulfilled",
    family = GuardFamily.REPEATS_AND_MANIPULATION,
)



# State the simulated time of the purchase being decided, and the time of the first purchase of the requested thing, which is 11.40 on the Swiss clock in summer
PURCHASE_TIME = datetime(2026, 8, 14, 11, 30, 0, tzinfo = timezone.utc)
FIRST_PURCHASE_TIME = datetime(2026, 8, 12, 9, 40, 0, tzinfo = timezone.utc)



# State the carts of the tests, where the monitor is the requested thing and the voucher is another thing
MONITOR_CART = [{"item_id": "ITEM_MONITOR", "item_name": "27-inch computer monitor", "quantity": 1}]
MONITOR_AND_PLAN_CART = MONITOR_CART + [{"item_id": "ITEM_PLAN", "item_name": "Extended protection plan", "quantity": 1}]
VOUCHER_CART = [{"item_id": "ITEM_VOUCHER", "item_name": "Digital gift voucher", "quantity": 1}]



# State the note and the question word for word
EXPECTED_NOTE = "You already bought this earlier, on 12 August at 11.40. This order would be a second one."
EXPECTED_QUESTION = EXPECTED_NOTE + " Approve it anyway?"
EXPECTED_MISSING_MEMORY_QUESTION = "Your earlier orders could not be read, so it cannot be checked whether you already bought this. Approve it anyway?"
OTHER_QUESTION ="The shop does not state its return policy. Approve anyway?"



# Write a moment as ISO text with a trailing Z, as the messages write it
def format_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")



# Copy the example message with the purchase time of the tests and with the given cart, where every line keeps the other fields of the example line,
# and read it through the strict reader
def build_event(example_message, cart_lines = MONITOR_CART):
    changed_message = copy.deepcopy(example_message)
    example_line = changed_message["authorization"]["items"][0]
    changed_message["authorization"]["timestamp"] = format_time(PURCHASE_TIME)
    changed_message["authorization"]["items"] = [
        {**example_line, "line_no": position, "item_id": cart_line["item_id"], "item_name": cart_line["item_name"], "quantity": cart_line["quantity"]}
        for position, cart_line in enumerate(cart_lines, start = 1)
    ]
    return read_purchase_message(changed_message)



# Build one earlier purchase of the run with its cart, at another shop than the shop of the example message, because the goal does not depend on the shop
def build_row(moment, cart_lines = MONITOR_CART, status = "approved", amount = 289.00):
    return {
        "authorization_id": "EARLIER_" + format_time(moment) + "_" + status,
        "timestamp": format_time(moment),
        "merchant_id": "SHOP_ANOTHER",
        "billing_amount_chf": amount,
        "status": status,
        "cart_lines": cart_lines,
    }



# Build a policy by hand, without the compiler, that asks for one monitor unless a test hands in other keywords, another number or no requested item at all.
# The action is the note unless a test hands in another one, because the note shows which earlier purchases fulfil the goal without any decision on top.
def build_policy(action = "note", kind_keywords = ("monitor",), goal_quantity = 1, asks_for_one_thing = True, uncertainty_policy = "ask"):
    requested_item = RequestedItem(kind_keywords = kind_keywords, max_quantity = 1, goal_quantity = goal_quantity) if asks_for_one_thing else None
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = uncertainty_policy,
        expectations = Expectations(
            requested_item = requested_item,
            goal_fulfilled_action = action,
        ),
        open_questions = (),
    )



# Let the guard alone judge one purchase against the earlier purchases of its run
def check_purchase(event, policy, rows):
    snapshot = build_ledger_snapshot_for_event(rows, event)
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = snapshot)
    return GOAL_FULFILLED_GUARD.check(decision_input, {})



# Find one evidence item of a result by the name of its fact
def find_evidence(guard_result, fact_name):
    return next(evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name)









#### Step 2: Check the first and the second purchase ####

# Check that the first purchase of the requested thing passes without a note
def test_first_purchase_of_the_requested_thing_passes_without_a_note(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy(), [])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.note is None
    assert guard_result.customer_message is None
    assert [(evidence_item.fact, evidence_item.value, evidence_item.comparator, evidence_item.threshold) for evidence_item in guard_result.evidence] == [
        ("earlier_purchases_of_requested_thing", 0, "<", 1),
        ("cart_holds_requested_thing", True, None, None),
    ]



# Check that the second purchase passes with the note under the action note, with the note word for word and the first purchase on the Swiss clock
def test_second_purchase_carries_the_note(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("note"), [build_row(FIRST_PURCHASE_TIME)])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code == ReasonCode.GOAL_ALREADY_FULFILLED
    assert guard_result.note == EXPECTED_NOTE
    assert guard_result.customer_message is None
    assert find_evidence(guard_result, "earlier_purchases_of_requested_thing").value == 1
    assert guard_result.guard_number == 16
    assert guard_result.guard_id == "goal_fulfilled"
    assert guard_result.family == GuardFamily.REPEATS_AND_MANIPULATION



# Check that the second purchase asks under the action step_up, with the question word for word and without a note
def test_second_purchase_asks_under_step_up(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("step_up"), [build_row(FIRST_PURCHASE_TIME)])
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.reason_code == ReasonCode.GOAL_ALREADY_FULFILLED
    assert guard_result.customer_message == EXPECTED_QUESTION
    assert guard_result.note is None



# Check that the second purchase is skipped under the action off
def test_second_purchase_skips_under_off(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("off"), [build_row(FIRST_PURCHASE_TIME)])
    assert guard_result.verdict == GuardVerdict.SKIP
    assert guard_result.reason_code is None
    assert guard_result.note is None



# Check that the note names the first of several earlier purchases, and that the hour follows the Swiss clock in winter as well
def test_note_names_the_first_purchase_in_swiss_local_time(example_message):
    rows = [build_row(FIRST_PURCHASE_TIME + timedelta(days = 1)), build_row(FIRST_PURCHASE_TIME)]
    summer_result = check_purchase(build_event(example_message), build_policy(), rows)
    winter_moment = datetime(2026, 1, 5, 23, 30, 0, tzinfo = timezone.utc)
    winter_result = check_purchase(build_event(example_message), build_policy(), [build_row(winter_moment)])
    assert summer_result.note == EXPECTED_NOTE
    assert find_evidence(summer_result, "earlier_purchases_of_requested_thing").value == 2
    assert winter_result.note == "You already bought this earlier, on 6 January at 00.30. This order would be a second one."



# Check that the guard never declines, under any action and however often the thing was bought
@pytest.mark.parametrize("action", ["off", "note", "step_up"])
def test_guard_never_declines(example_message, action):
    rows = [build_row(FIRST_PURCHASE_TIME + timedelta(hours = position)) for position in range(6)]
    assert check_purchase(build_event(example_message), build_policy(action), rows).verdict != GuardVerdict.DECLINE









#### Step 3: Check which earlier purchases fulfil the goal ####

# Check that an earlier purchase that is an open question, or that never went through, does not fulfil the goal
@pytest.mark.parametrize("status", ["pending", "declined", "expired", "cancelled"])
def test_earlier_purchase_that_was_not_approved_does_not_fulfil_the_goal(example_message, status):
    guard_result = check_purchase(build_event(example_message), build_policy(), [build_row(FIRST_PURCHASE_TIME, status = status)])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.note is None
    assert find_evidence(guard_result, "earlier_purchases_of_requested_thing").value == 0



# Check that an earlier purchase of another thing does not fulfil the goal, and neither does one whose cart is not known
def test_earlier_purchase_of_another_thing_does_not_fulfil_the_goal(example_message):
    rows = [build_row(FIRST_PURCHASE_TIME, cart_lines = VOUCHER_CART), build_row(FIRST_PURCHASE_TIME + timedelta(hours = 1), cart_lines = None)]
    guard_result = check_purchase(build_event(example_message), build_policy(), rows)
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.note is None
    assert find_evidence(guard_result, "earlier_purchases_of_requested_thing").value == 0



# Check that an earlier cart with the requested thing next to an extra fulfils the goal, because one of its lines held the thing
def test_earlier_cart_with_an_extra_fulfils_the_goal(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy(), [build_row(FIRST_PURCHASE_TIME, cart_lines = MONITOR_AND_PLAN_CART)])
    assert guard_result.note == EXPECTED_NOTE



# Check that every keyword has to be in one name, so a purchase of trail-running shoes does not fulfil a request for road-running shoes
def test_every_keyword_has_to_be_in_the_name(example_message):
    road_cart = [{"item_id": "ITEM_ROAD", "item_name": "Road-running shoes", "quantity": 1}]
    trail_cart = [{"item_id": "ITEM_TRAIL", "item_name": "Trail-running shoes", "quantity": 1}]
    policy = build_policy(kind_keywords = ("road", "running", "shoe"))
    after_trail_shoes = check_purchase(build_event(example_message, road_cart), policy, [build_row(FIRST_PURCHASE_TIME, cart_lines = trail_cart)])
    after_road_shoes = check_purchase(build_event(example_message, road_cart), policy, [build_row(FIRST_PURCHASE_TIME, cart_lines = road_cart)])
    assert after_trail_shoes.note is None
    assert after_road_shoes.note == EXPECTED_NOTE



# Check that without keywords every line is the requested thing, so any earlier approved purchase with a known cart fulfils the goal
def test_without_keywords_every_line_is_the_requested_thing(example_message):
    policy = build_policy(kind_keywords = ())
    with_a_known_cart = check_purchase(build_event(example_message), policy, [build_row(FIRST_PURCHASE_TIME, cart_lines = VOUCHER_CART)])
    with_an_unknown_cart = check_purchase(build_event(example_message), policy, [build_row(FIRST_PURCHASE_TIME, cart_lines = None)])
    assert with_a_known_cart.note == EXPECTED_NOTE
    assert with_an_unknown_cart.note is None



# Check that a goal of two is fulfilled by two earlier purchases and not by one
def test_goal_quantity_of_two_needs_two_earlier_purchases(example_message):
    policy = build_policy(goal_quantity = 2)
    one_row = [build_row(FIRST_PURCHASE_TIME)]
    two_rows = one_row + [build_row(FIRST_PURCHASE_TIME + timedelta(days = 1))]
    assert check_purchase(build_event(example_message), policy, one_row).note is None
    assert check_purchase(build_event(example_message), policy, two_rows).note == EXPECTED_NOTE









#### Step 4: Check the cart, the instruction and the missing memory ####

# Check that a cart without the requested thing gets no note, although the thing was already bought
def test_cart_without_the_requested_thing_gets_no_note(example_message):
    guard_result = check_purchase(build_event(example_message, VOUCHER_CART), build_policy(), [build_row(FIRST_PURCHASE_TIME)])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.note is None
    assert find_evidence(guard_result, "cart_holds_requested_thing").value is False
    assert find_evidence(guard_result, "earlier_purchases_of_requested_thing").value == 1



# Check that an instruction that asks for no single thing, and a requested item without a number to reach, skip the guard
def test_instruction_without_a_single_thing_skips(example_message):
    rows = [build_row(FIRST_PURCHASE_TIME)]
    without_a_requested_item = check_purchase(build_event(example_message), build_policy(asks_for_one_thing = False), rows)
    without_a_goal_quantity = check_purchase(build_event(example_message), build_policy(goal_quantity = None), rows)
    assert without_a_requested_item.verdict == GuardVerdict.SKIP
    assert without_a_goal_quantity.verdict == GuardVerdict.SKIP
    assert without_a_requested_item.note is None
    assert without_a_goal_quantity.note is None



# Check that the compiler gives a recurring instruction no goal and an instruction for one thing a goal of one, with the action of the settings
def test_compiler_sets_the_goal_and_the_action(example_message):
    recurring_message = copy.deepcopy(example_message)
    recurring_message["mandate"]["instruction"] = "Order our household groceries for delivery. Keep each order at or below CHF 120 including delivery. Ask me when uncertain."
    single_message = copy.deepcopy(example_message)
    single_message["mandate"]["instruction"] = "Buy the 27-inch monitor I chose for CHF 400 or less. Ask me when uncertain."
    recurring_expectations = build_policy_from_mandate(read_purchase_message(recurring_message).mandate).expectations
    single_expectations = build_policy_from_mandate(read_purchase_message(single_message).mandate).expectations
    assert recurring_expectations.requested_item is None
    assert single_expectations.requested_item.goal_quantity == 1
    assert single_expectations.goal_fulfilled_action == "step_up"
    assert single_expectations.duplicate_window_hours == 48
    assert str(single_expectations.duplicate_amount_share) == "0.10"



# Check that a policy that states no action asks the customer, so a second order of the one requested thing never goes through on a default
def test_policy_without_a_stated_action_asks(example_message):
    requested_monitor = RequestedItem(kind_keywords = ("monitor",), max_quantity = 1, goal_quantity = 1)
    assert Expectations().goal_fulfilled_action == "step_up"
    policy = InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(requested_item = requested_monitor),
        open_questions = (),
    )
    guard_result = check_purchase(build_event(example_message), policy, [build_row(FIRST_PURCHASE_TIME)])
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.customer_message == EXPECTED_QUESTION
    assert guard_result.note is None



# Build the three states that are no usable memory, which are no state at all, a state of the wrong kind and a memory with a row that could not be read
def build_unusable_states(event):
    incomplete_snapshot = build_ledger_snapshot_for_event(
        [{"authorization_id": "BROKEN", "timestamp": None, "merchant_id": "SHOP", "billing_amount_chf": 70.0, "status": "approved"}],
        event,
    )
    assert incomplete_snapshot.is_complete is False
    return (None, [], incomplete_snapshot)



# Let the guard alone judge one purchase on a state that is no usable memory
def check_purchase_on_state(event, policy, unusable_state):
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = unusable_state)
    return GOAL_FULFILLED_GUARD.check(decision_input, {})



# Check that a missing or an incomplete memory passes without a note under the action note and says so in the evidence, because a note is no decision
def test_missing_memory_passes_without_a_note_under_note(example_message):
    event = build_event(example_message)
    guard_results = [check_purchase_on_state(event, build_policy("note"), unusable_state) for unusable_state in build_unusable_states(event)]
    assert [guard_result.verdict for guard_result in guard_results] == [GuardVerdict.PASS] * 3
    assert [guard_result.reason_code for guard_result in guard_results] == [None] * 3
    assert [guard_result.note for guard_result in guard_results] == [None] * 3
    assert [guard_result.customer_message for guard_result in guard_results] == [None] * 3
    assert [[(evidence_item.fact, evidence_item.value) for evidence_item in guard_result.evidence] for guard_result in guard_results] == [[("ledger_is_usable", False)]] * 3



# Check that a missing or an incomplete memory is uncertain under the action step_up, with the question word for word, because a missing memory is never permission
def test_missing_memory_is_uncertain_under_step_up(example_message):
    event = build_event(example_message)
    guard_results = [check_purchase_on_state(event, build_policy("step_up"), unusable_state) for unusable_state in build_unusable_states(event)]
    assert [guard_result.verdict for guard_result in guard_results] == [GuardVerdict.UNCERTAIN] * 3
    assert [guard_result.reason_code for guard_result in guard_results] == [ReasonCode.LEDGER_UNAVAILABLE] * 3
    assert [guard_result.note for guard_result in guard_results] == [None] * 3
    assert [guard_result.customer_message for guard_result in guard_results] == [EXPECTED_MISSING_MEMORY_QUESTION] * 3
    assert [[(evidence_item.fact, evidence_item.value) for evidence_item in guard_result.evidence] for guard_result in guard_results] == [[("ledger_is_usable", False)]] * 3



# Check that the action off and an instruction without a single thing skip before the memory is looked at
def test_missing_memory_is_not_looked_at_when_the_guard_skips(example_message):
    event = build_event(example_message)
    switched_off = check_purchase_on_state(event, build_policy("off"), None)
    without_a_requested_item = check_purchase_on_state(event, build_policy("step_up", asks_for_one_thing = False), None)
    assert switched_off.verdict == GuardVerdict.SKIP
    assert without_a_requested_item.verdict == GuardVerdict.SKIP
    assert switched_off.reason_code is None
    assert without_a_requested_item.reason_code is None









#### Step 5: Check the whole pipeline ####

# Check that the note reaches the record of an approval and leaves the decision and its reasons alone
def test_note_reaches_the_record_of_an_approval(example_message):
    event = build_event(example_message)
    decision_trace = decide(event, build_policy("note"), build_ledger_snapshot_for_event([build_row(FIRST_PURCHASE_TIME)], event))
    assert decision_trace.decision == Decision.APPROVE
    assert decision_trace.reason_codes == []
    assert decision_trace.notes == [EXPECTED_NOTE]
    assert decision_trace.aggregation.raised_by == []



# Check that the action step_up asks through the whole pipeline, raised by this guard alone
def test_step_up_asks_through_the_pipeline(example_message):
    event = build_event(example_message)
    decision_trace = decide(event, build_policy("step_up"), build_ledger_snapshot_for_event([build_row(FIRST_PURCHASE_TIME)], event))
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.GOAL_ALREADY_FULFILLED]
    assert decision_trace.customer_message == EXPECTED_QUESTION
    assert decision_trace.aggregation.raised_by == ["goal_fulfilled"]



# Check that a missing memory under the action step_up asks through the whole pipeline and never approves, also under the uncertainty policy approve,
# because a memory that could not be read is a failure of the engine and no doubt about the purchase
def test_missing_memory_never_approves_under_step_up(example_message):
    event = build_event(example_message)
    decision_trace = decide(event, build_policy("step_up", uncertainty_policy = "approve"), None, guards = [GOAL_FULFILLED_GUARD])
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.LEDGER_UNAVAILABLE]
    assert decision_trace.customer_message == EXPECTED_MISSING_MEMORY_QUESTION
    assert decision_trace.aggregation.raised_by == ["goal_fulfilled"]
    assert decision_trace.aggregation.uncertainty_policy_applied is True



# Ask about the return policy with a message of its own, as a guard that runs before this one
@dataclass(frozen = True)
class OtherQuestionGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily = GuardFamily.ITEM_AND_TERMS

    def check(self, decision_input, earlier_results):
        return GuardResult(
            guard_number = self.guard_number,
            guard_id = self.guard_id,
            family = self.family,
            verdict = GuardVerdict.STEP_UP,
            reason_code = ReasonCode.RETURN_TERMS_UNKNOWN,
            customer_message = OTHER_QUESTION,
        )



# Check that the customer reads the question of another guard when one asks as well, and that this guard then adds its reason last
def test_question_of_another_guard_is_the_one_the_customer_reads(example_message):
    event = build_event(example_message)
    guards = [OtherQuestionGuard(19, "stub_other_question"), GOAL_FULFILLED_GUARD]
    decision_trace = decide(event, build_policy("step_up"), build_ledger_snapshot_for_event([build_row(FIRST_PURCHASE_TIME)], event), guards = guards)
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.RETURN_TERMS_UNKNOWN, ReasonCode.GOAL_ALREADY_FULFILLED]
    assert decision_trace.customer_message == OTHER_QUESTION
    assert decision_trace.aggregation.raised_by == ["stub_other_question", "goal_fulfilled"]



# Check that the default pipeline runs this guard last, which is what lets every other question come first
def test_default_pipeline_runs_this_guard_last():
    assert get_default_guard_pipeline()[-1] == GOAL_FULFILLED_GUARD









#### Step 6: Check that the guard never names an identifier ####

# Check the source text of the guard for the words that would tie a decision to a test case or to an identifier
def test_source_names_no_identifier():
    source_text = GUARD_SOURCE_PATH.read_text(encoding = "utf-8").lower()
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text]
    assert words_found == []
