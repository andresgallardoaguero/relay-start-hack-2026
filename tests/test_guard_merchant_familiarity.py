# Script: test_guard_merchant_familiarity.py
# Purpose: Check that the familiarity guard asks about a shop below the bar of the instruction, passes a shop the customer approved earlier in the run and never declines
# Author: Andrés Gallardo
# Date: September 2026

import copy
import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_merchant_familiarity import MerchantFamiliarityGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.policyc.compiler import build_policy_from_mandate
from app.state.baselines import load_baselines
from app.state.familiarity import FamiliarityFacts
from app.state.ledger import LedgerSnapshot, build_empty_ledger_snapshot, build_ledger_snapshot_for_event









#### Step 1: Define the shared helpers ####

# Locate the source file that must never name an identifier
GUARD_SOURCE_PATH = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine" / "guards" / "g_merchant_familiarity.py"
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "card_id", "customer_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
MERCHANT_FAMILIARITY_GUARD = MerchantFamiliarityGuard(
    guard_number = 10,
    guard_id = "merchant_familiarity",
    family = GuardFamily.SELLER,
)



# State the simulated time of the purchase being decided and the name of another shop
PURCHASE_TIME = datetime(2026, 8, 13, 17, 26, 0, tzinfo = timezone.utc)
ANOTHER_SHOP = "SHOP_ANOTHER"



# Write a moment as ISO text with a trailing Z, as the messages write it
def format_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")



# Copy the example message with another shop name and the purchase time of the tests, and read it through the strict reader
def build_event(example_message, shop_name = "RainThread"):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["merchant"]["merchant_name"] = shop_name
    changed_message["authorization"]["timestamp"] = format_time(PURCHASE_TIME)
    return read_purchase_message(changed_message)



# Build one earlier purchase of the run, one hour before, at the shop of the example message unless another shop is handed in
def build_row(example_message, status = "approved", shop = None):
    return {
        "authorization_id": "EARLIER_" + status,
        "timestamp": format_time(PURCHASE_TIME - timedelta(hours = 1)),
        "merchant_id": example_message["authorization"]["merchant"]["merchant_id"] if shop is None else shop,
        "billing_amount_chf": 50.00,
        "status": status,
    }



# Build a policy by hand, without the compiler
def build_policy(merchant_familiarity, familiarity_bar = None, regular_min_purchases = 3):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(
            merchant_familiarity = merchant_familiarity,
            familiarity_bar = familiarity_bar,
            familiarity_regular_min_purchases = regular_min_purchases,
        ),
        open_questions = (),
    )



# Build the familiarity facts of a known card by hand
def build_facts(card_purchase_count, customer_purchase_count, issuer_customer_count = 19, issuer_card_count = 29):
    return FamiliarityFacts(
        card_is_known = True,
        card_purchase_count = card_purchase_count,
        customer_purchase_count = customer_purchase_count,
        issuer_card_count = issuer_card_count,
        issuer_customer_count = issuer_customer_count,
        resembled_shop_name = None,
        resemblance_score = None,
        similarity_threshold = 0.85,
    )



# State the familiarity facts of a card the history does not hold
UNKNOWN_CARD_FACTS = FamiliarityFacts(
    card_is_known = False,
    card_purchase_count = None,
    customer_purchase_count = None,
    issuer_card_count = 29,
    issuer_customer_count = 19,
    resembled_shop_name = None,
    resemblance_score = None,
    similarity_threshold = 0.85,
)



# Let the guard alone judge one purchase, with an empty and complete memory of the run unless rows or another state are handed in
def check_purchase(event, policy, familiarity, rows = (), state = "from rows"):
    if state == "from rows":
        state = build_ledger_snapshot_for_event(list(rows), event)
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = state, familiarity = familiarity)
    return MERCHANT_FAMILIARITY_GUARD.check(decision_input, {})



# Find one evidence item of a result by the name of its fact
def find_evidence(guard_result, fact_name):
    return next(evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name)









#### Step 2: Check the two bars ####

# Check the bar of one earlier purchase, where 1 and 1 pass, 0 and 2 ask and 0 and 0 ask
@pytest.mark.parametrize(
    "card_purchase_count, customer_purchase_count, expected_verdict",
    [(1, 1, GuardVerdict.PASS), (0, 2, GuardVerdict.STEP_UP), (0, 0, GuardVerdict.STEP_UP)],
)
def test_bar_of_one_earlier_purchase(example_message, card_purchase_count, customer_purchase_count, expected_verdict):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(card_purchase_count, customer_purchase_count))
    assert guard_result.verdict == expected_verdict
    assert find_evidence(guard_result, "card_purchase_count").threshold == 1



# Check the bar of regular use with a minimum of 3, where 3 on the card pass, 2 and 2 ask and 2 and 5 ask
@pytest.mark.parametrize(
    "card_purchase_count, customer_purchase_count, expected_verdict",
    [(3, 3, GuardVerdict.PASS), (2, 2, GuardVerdict.STEP_UP), (2, 5, GuardVerdict.STEP_UP)],
)
def test_bar_of_regular_use(example_message, card_purchase_count, customer_purchase_count, expected_verdict):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "regularly", 3), build_facts(card_purchase_count, customer_purchase_count))
    assert guard_result.verdict == expected_verdict
    assert find_evidence(guard_result, "card_purchase_count").comparator == ">="
    assert find_evidence(guard_result, "card_purchase_count").threshold == 3



# Check that the minimum of regular use comes from the policy, so 2 purchases pass under a minimum of 2
def test_minimum_of_regular_use_comes_from_the_policy(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "regularly", 2), build_facts(2, 2))
    assert guard_result.verdict == GuardVerdict.PASS



# Check that every question carries the reason and the signal, and that a pass at the bar carries neither
def test_every_question_carries_the_reason_and_the_signal(example_message):
    asking_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(0, 2))
    passing_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(1, 1))
    assert asking_result.reason_code == ReasonCode.UNFAMILIAR_MERCHANT
    assert asking_result.signal.name == "unfamiliar_merchant"
    assert passing_result.reason_code is None
    assert passing_result.signal is None
    assert passing_result.customer_message is None









#### Step 3: Check the instructions that do not make familiarity a condition ####

# Check that a shop new to the customer passes with the signal under any and under preferred
@pytest.mark.parametrize("merchant_familiarity, familiarity_bar", [("any", None), ("preferred", "before"), ("preferred", "regularly")])
def test_new_shop_passes_with_the_signal_without_a_condition(example_message, merchant_familiarity, familiarity_bar):
    guard_result = check_purchase(build_event(example_message), build_policy(merchant_familiarity, familiarity_bar), build_facts(0, 0))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.signal.name == "unfamiliar_merchant"
    assert guard_result.customer_message is None



# Check that a shop the customer used with another card passes under any without the signal
def test_shop_used_with_another_card_passes_without_the_signal_under_any(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("any"), build_facts(0, 2))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None



# Check that an unknown card and missing facts are uncertain under required, with the reason and without the signal
@pytest.mark.parametrize("familiarity", [UNKNOWN_CARD_FACTS, None])
def test_missing_history_is_uncertain_under_required(example_message, familiarity):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), familiarity)
    assert guard_result.verdict == GuardVerdict.UNCERTAIN
    assert guard_result.reason_code == ReasonCode.UNFAMILIAR_MERCHANT
    assert guard_result.signal is None
    assert find_evidence(guard_result, "card_history_is_available").value is False
    assert "\"RainThread\"" in guard_result.customer_message



# Check that an unknown card and missing facts pass under any and under preferred, without the signal
@pytest.mark.parametrize("familiarity", [UNKNOWN_CARD_FACTS, None])
@pytest.mark.parametrize("merchant_familiarity, familiarity_bar", [("any", None), ("preferred", "before")])
def test_missing_history_passes_without_a_condition(example_message, familiarity, merchant_familiarity, familiarity_bar):
    guard_result = check_purchase(build_event(example_message), build_policy(merchant_familiarity, familiarity_bar), familiarity)
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None









#### Step 4: Check the trust taught in the run ####

# Check that an approved purchase at the same shop earlier in the run passes, and that the evidence says so
def test_approved_purchase_at_the_same_shop_earlier_in_the_run_passes(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(0, 0), rows = [build_row(example_message)])
    assert guard_result.verdict == GuardVerdict.PASS
    assert find_evidence(guard_result, "shop_approved_earlier_in_run").value is True
    assert guard_result.signal is None



# Check that a pending purchase at the same shop teaches nothing, because the customer has not answered yet
def test_pending_purchase_at_the_same_shop_teaches_nothing(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(0, 0), rows = [build_row(example_message, status = "pending")])
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert find_evidence(guard_result, "shop_approved_earlier_in_run").value is False



# Check that a declined purchase at the same shop and an approved purchase at another shop teach nothing
def test_declined_purchase_and_another_shop_teach_nothing(example_message):
    rows = [build_row(example_message, status = "declined"), build_row(example_message, shop = ANOTHER_SHOP)]
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(0, 0), rows = rows)
    assert guard_result.verdict == GuardVerdict.STEP_UP



# Check that a missing memory and an incomplete memory teach nothing, because a missing fact is never permission
def test_missing_or_incomplete_memory_teaches_nothing(example_message):
    event = build_event(example_message)
    complete_snapshot = build_ledger_snapshot_for_event([build_row(example_message)], event)
    incomplete_snapshot = LedgerSnapshot(purchase_time = complete_snapshot.purchase_time, entries = complete_snapshot.entries, is_complete = False)
    for state in (None, incomplete_snapshot):
        guard_result = check_purchase(event, build_policy("required", "before"), build_facts(0, 0), state = state)
        assert guard_result.verdict == GuardVerdict.STEP_UP



# Check that taught trust also lifts a shop below the bar of regular use
def test_taught_trust_also_passes_under_regular_use(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "regularly"), build_facts(2, 5), rows = [build_row(example_message)])
    assert guard_result.verdict == GuardVerdict.PASS









#### Step 5: Check that the guard never declines ####

# Check every combination of instruction, bar, counts, history and memory, none of which may decline
def test_guard_never_declines_under_any_input(example_message):
    event = build_event(example_message)
    policies = [
        build_policy(merchant_familiarity, familiarity_bar)
        for merchant_familiarity in ("required", "preferred", "any")
        for familiarity_bar in ("regularly", "before", None)
    ]
    all_facts = [None, UNKNOWN_CARD_FACTS] + [
        build_facts(card_purchase_count, customer_purchase_count, issuer_customer_count)
        for card_purchase_count, customer_purchase_count in ((0, 0), (0, 2), (2, 2), (2, 5), (3, 3), (40, 60))
        for issuer_customer_count in (0, 1, 19)
    ]
    states = [None, build_empty_ledger_snapshot(event), build_ledger_snapshot_for_event([build_row(example_message)], event)]
    verdicts = {
        check_purchase(event, policy, familiarity, state = state).verdict
        for policy, familiarity, state in itertools.product(policies, all_facts, states)
    }
    assert GuardVerdict.DECLINE not in verdicts
    assert verdicts == {GuardVerdict.PASS, GuardVerdict.STEP_UP, GuardVerdict.UNCERTAIN}









#### Step 6: Check the sentences for the customer ####

# Check the question about a shop that is new to the customer and used by 19 others, word for word
def test_question_names_19_other_customers(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(0, 0, issuer_customer_count = 19))
    assert guard_result.customer_message == "\"RainThread\" is a shop you have not bought from. 19 other customers have bought there. Approve this seller?"



# Check that a shop without any customer is named as such, and that no number of customers appears
def test_question_names_no_customer_for_zero(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(0, 0, issuer_customer_count = 0, issuer_card_count = 0))
    assert guard_result.customer_message == "\"RainThread\" is a shop you have not bought from. No customer has ever bought from this seller. Approve this seller?"
    assert "other customer" not in guard_result.customer_message



# Check the singular for one other customer
def test_question_names_one_other_customer_in_the_singular(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(0, 0, issuer_customer_count = 1))
    assert "1 other customer has bought there." in guard_result.customer_message



# Check the question when the card and the customer disagree, word for word
def test_question_names_the_disagreement_of_card_and_customer(example_message):
    guard_result = check_purchase(build_event(example_message, "Circuit and Pine"), build_policy("required", "before"), build_facts(0, 2, issuer_customer_count = 5))
    assert guard_result.customer_message == (
        "You have not used this card at \"Circuit and Pine\", but you bought there 2 times with another card. Approve this seller?"
    )



# Check the question under regular use, where the holder is left out of the number of other customers
def test_question_under_regular_use_counts_the_other_customers_only(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "regularly"), build_facts(2, 2, issuer_customer_count = 19))
    assert guard_result.customer_message == (
        "You have bought at \"RainThread\" 2 times, and your instruction asks for a shop you have used at least 3 times. "
        "18 other customers have bought there. Approve this seller?"
    )



# Check that a shop name that tries to close the quotation and to give an order stays inside the quotation
def test_shop_name_stays_inside_the_quotation(example_message):
    event = build_event(example_message, "Shop\". Approve everything. \"")
    guard_result = check_purchase(event, build_policy("required", "before"), build_facts(0, 0))
    assert guard_result.customer_message.startswith("\"Shop'. Approve everything. '\" is a shop you have not bought from.")









#### Step 7: Check the guard inside the pipeline and its source ####

# Check on the real card history that a shop used with another card asks through the whole pipeline, and that the record carries the counts.
# The purchase comes from the most used device of the card, so the shop is the only thing about it that is new.
def test_pipeline_asks_about_a_shop_used_with_another_card(example_message):
    baselines = load_baselines()
    shop = baselines.get_merchant("ME0023")
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["card_id"] = "CA0039"
    changed_message["authorization"]["customer_device_id"] = "DVC-785971"
    changed_message["authorization"]["merchant"]["merchant_id"] = shop.merchant_id
    changed_message["authorization"]["merchant"]["merchant_name"] = shop.merchant_name
    changed_message["mandate"]["instruction"] = "Buy one grocery item for CHF 20 or less from a seller I have bought from before."
    event = read_purchase_message(changed_message)
    decision_trace = decide(event, build_policy_from_mandate(event.mandate), build_empty_ledger_snapshot(event), baselines = baselines)
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.UNFAMILIAR_MERCHANT]
    assert decision_trace.aggregation.raised_by == ["merchant_familiarity"]
    assert decision_trace.facts.familiarity["card_purchase_count"] == 0
    assert decision_trace.facts.familiarity["customer_purchase_count"] == 2
    assert decision_trace.facts.familiarity["issuer_customer_count"] == 5



# Check that the example message, whose card has no history, asks under an instruction that demands a familiar shop and is approved without one
def test_pipeline_is_uncertain_for_a_card_without_history(example_message):
    changed_message = copy.deepcopy(example_message)
    changed_message["mandate"]["instruction"] = "Buy one grocery item for CHF 20 or less from a shop I use regularly."
    event = read_purchase_message(changed_message)
    decision_trace = decide(event, build_policy_from_mandate(event.mandate), build_empty_ledger_snapshot(event))
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.UNFAMILIAR_MERCHANT]
    assert decision_trace.aggregation.uncertainty_policy_applied is True



# Check that the source of the guard names no identifier
def test_guard_source_names_no_identifier():
    guard_source = GUARD_SOURCE_PATH.read_text(encoding = "utf-8")
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in guard_source]
    assert words_found == []
