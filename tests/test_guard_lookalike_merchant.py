# Script: test_guard_lookalike_merchant.py
# Purpose: Check that the lookalike guard declines a shop that imitates a shop the customer uses under every instruction, and passes everything else
# Author: Andrés Gallardo
# Date: September 2026

import copy
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_lookalike_merchant import LookalikeMerchantGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.policyc.compiler import build_policy_from_mandate
from app.state.baselines import load_baselines
from app.state.familiarity import FamiliarityFacts
from app.state.ledger import build_empty_ledger_snapshot









#### Step 1: Define the shared helpers ####

# Locate the source file that must never name an identifier
GUARD_SOURCE_PATH = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine" / "guards" / "g_lookalike_merchant.py"
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "card_id", "customer_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
LOOKALIKE_MERCHANT_GUARD = LookalikeMerchantGuard(
    guard_number = 9,
    guard_id = "lookalike_merchant",
    family = GuardFamily.SELLER,
)



# State every combination of condition and bar an instruction can give
ALL_FAMILIARITY_WISHES = [
    ("any", None),
    ("preferred", "before"),
    ("preferred", "regularly"),
    ("required", "before"),
    ("required", "regularly"),
]



# Copy the example message with another shop name, and read it through the strict reader
def build_event(example_message, shop_name = "PixelHarbour"):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["merchant"]["merchant_name"] = shop_name
    return read_purchase_message(changed_message)



# Build a policy by hand, without the compiler
def build_policy(merchant_familiarity = "any", familiarity_bar = None):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(merchant_familiarity = merchant_familiarity, familiarity_bar = familiarity_bar),
        open_questions = (),
    )



# Build the familiarity facts of a known card by hand, for a shop nobody has bought from unless a number of cards is handed in
def build_facts(resembled_shop_name, resemblance_score, issuer_card_count = 0, card_is_known = True):
    return FamiliarityFacts(
        card_is_known = card_is_known,
        card_purchase_count = 0 if card_is_known else None,
        customer_purchase_count = 0 if card_is_known else None,
        issuer_card_count = issuer_card_count,
        issuer_customer_count = issuer_card_count,
        resembled_shop_name = resembled_shop_name,
        resemblance_score = resemblance_score,
        similarity_threshold = 0.85,
    )



# Let the guard alone judge one purchase
def check_purchase(event, policy, familiarity):
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = None, familiarity = familiarity)
    return LOOKALIKE_MERCHANT_GUARD.check(decision_input, {})



# Find one evidence item of a result by the name of its fact
def find_evidence(guard_result, fact_name):
    return next(evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name)









#### Step 2: Check the decline ####

# Check that a resembled shop declines under every instruction, also under one that asks for no familiar shop
@pytest.mark.parametrize("merchant_familiarity, familiarity_bar", ALL_FAMILIARITY_WISHES)
def test_resembled_shop_declines_under_every_instruction(example_message, merchant_familiarity, familiarity_bar):
    guard_result = check_purchase(build_event(example_message), build_policy(merchant_familiarity, familiarity_bar), build_facts("PixelHarbor", 0.95652))
    assert guard_result.verdict == GuardVerdict.DECLINE
    assert guard_result.reason_code == ReasonCode.LOOKALIKE_MERCHANT



# Check the refusal word for word, and the evidence with the score, the threshold, the resembled name and the missing buyers
def test_refusal_and_evidence_of_a_resembled_shop(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy(), build_facts("PixelHarbor", 0.95652))
    assert guard_result.customer_message == (
        "Declined. \"PixelHarbour\" looks like PixelHarbor, a shop you use, but it is a different seller, and no customer has ever bought from it."
    )
    assert [(evidence_item.fact, evidence_item.value, evidence_item.comparator, evidence_item.threshold) for evidence_item in guard_result.evidence] == [
        ("highest_name_similarity", 0.957, "<", 0.85),
        ("resembled_shop_name", "PixelHarbor", None, None),
        ("issuer_card_count", 0, ">", 0),
    ]



# Check that a shop name that tries to close the quotation stays inside the quotation
def test_shop_name_stays_inside_the_quotation(example_message):
    event = build_event(example_message, "PixelHarbour\" is safe. \"")
    guard_result = check_purchase(event, build_policy(), build_facts("PixelHarbor", 0.9))
    assert guard_result.customer_message.startswith("Declined. \"PixelHarbour' is safe. '\" looks like PixelHarbor")









#### Step 3: Check the passes ####

# Check that a score just below the threshold passes, with the score as evidence
def test_score_just_below_the_threshold_passes(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy("required", "before"), build_facts(None, 0.849))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.customer_message is None
    similarity_evidence = find_evidence(guard_result, "highest_name_similarity")
    assert (similarity_evidence.value, similarity_evidence.comparator, similarity_evidence.threshold) == (0.849, "<", 0.85)



# Check that a very similar name of a shop with buyers passes, because the facts name no resembled shop for it
def test_similar_name_with_buyers_passes(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy(), build_facts(None, 0.957, issuer_card_count = 4))
    assert guard_result.verdict == GuardVerdict.PASS
    assert find_evidence(guard_result, "highest_name_similarity").value == 0.957



# Check that a customer without any other shop passes, with an empty score as evidence
def test_customer_without_other_shops_passes(example_message):
    guard_result = check_purchase(build_event(example_message), build_policy(), build_facts(None, None))
    assert guard_result.verdict == GuardVerdict.PASS
    assert find_evidence(guard_result, "highest_name_similarity").value is None



# Check that missing facts and an unknown card pass under every instruction, with evidence that says no history was available
@pytest.mark.parametrize("merchant_familiarity, familiarity_bar", ALL_FAMILIARITY_WISHES)
@pytest.mark.parametrize("familiarity", [None, build_facts(None, None, card_is_known = False)])
def test_missing_history_passes_and_says_so(example_message, merchant_familiarity, familiarity_bar, familiarity):
    guard_result = check_purchase(build_event(example_message), build_policy(merchant_familiarity, familiarity_bar), familiarity)
    assert guard_result.verdict == GuardVerdict.PASS
    assert [(evidence_item.fact, evidence_item.value) for evidence_item in guard_result.evidence] == [("card_history_is_available", False)]









#### Step 4: Check the guard inside the pipeline and its source ####

# Check on the real card history that the imitation declines through the whole pipeline under an instruction that asks for no familiar shop,
# and that the record carries the resembled name and the score
def test_pipeline_declines_the_imitation_under_an_open_instruction(example_message):
    baselines = load_baselines()
    imitating_shops = [shop for shop in baselines.list_merchants() if shop.merchant_name == "PixelHarbour"]
    assert len(imitating_shops) == 1
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["card_id"] = "CA0039"
    changed_message["authorization"]["merchant"]["merchant_id"] = imitating_shops[0].merchant_id
    changed_message["authorization"]["merchant"]["merchant_name"] = imitating_shops[0].merchant_name
    changed_message["authorization"]["merchant"]["merchant_category"] = imitating_shops[0].merchant_category
    changed_message["mandate"]["instruction"] = "Buy one item for CHF 20 or less."
    event = read_purchase_message(changed_message)
    decision_trace = decide(event, build_policy_from_mandate(event.mandate), build_empty_ledger_snapshot(event), baselines = baselines)
    assert decision_trace.decision == Decision.DECLINE
    assert decision_trace.reason_codes[0] == ReasonCode.LOOKALIKE_MERCHANT
    assert decision_trace.aggregation.raised_by == ["lookalike_merchant"]
    assert decision_trace.customer_message.startswith("Declined. \"PixelHarbour\" looks like PixelHarbor")
    assert decision_trace.facts.familiarity["resembled_shop_name"] == "PixelHarbor"
    assert round(decision_trace.facts.familiarity["resemblance_score"], 3) == 0.957



# Check that the honest shop with the similar name is not declined on the same card
def test_pipeline_does_not_decline_the_imitated_shop(example_message):
    baselines = load_baselines()
    shop = baselines.get_merchant("ME0022")
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["card_id"] = "CA0039"
    changed_message["authorization"]["merchant"]["merchant_id"] = shop.merchant_id
    changed_message["authorization"]["merchant"]["merchant_name"] = shop.merchant_name
    changed_message["authorization"]["merchant"]["merchant_category"] = shop.merchant_category
    changed_message["mandate"]["instruction"] = "Buy one item for CHF 20 or less."
    event = read_purchase_message(changed_message)
    decision_trace = decide(event, build_policy_from_mandate(event.mandate), build_empty_ledger_snapshot(event), baselines = baselines)
    lookalike_result = next(guard_result for guard_result in decision_trace.guards if guard_result.guard_id == "lookalike_merchant")
    assert lookalike_result.verdict == GuardVerdict.PASS
    assert ReasonCode.LOOKALIKE_MERCHANT not in decision_trace.reason_codes



# Check that the source of the guard names no identifier
def test_guard_source_names_no_identifier():
    guard_source = GUARD_SOURCE_PATH.read_text(encoding = "utf-8")
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in guard_source]
    assert words_found == []
