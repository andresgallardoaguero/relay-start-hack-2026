# Script: test_guard_item_shop_consistency.py
# Purpose: Check that a recurring item from a shop that cannot bill on a recurring basis asks, and that an item outside the category of its shop is recorded and decides nothing
# Author: Andrés Gallardo
# Date: September 2026

import copy
from decimal import Decimal

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_item_shop_consistency import ItemShopConsistencyGuard
from app.models.decision import GuardFamily, GuardSignal, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy









#### Step 1: Define the shared helpers ####

# Build the guard as the registry does, from its number, its id and its family
ITEM_SHOP_CONSISTENCY_GUARD = ItemShopConsistencyGuard(
    guard_number = 21,
    guard_id = "item_shop_consistency",
    family = GuardFamily.ITEM_AND_TERMS,
)



# Name the cart lines the cases are built from, each as its item name, its item category and its unit price
PRODUCE_LINE = ("Fresh produce selection", "groceries", 24.00)
FRAGRANCE_LINE = ("Fragrance and beauty gift", "cosmetics", 32.00)
SHOES_LINE = ("Road-running shoes", "sporting_goods", 165.00)
PLAN_LINE = ("Extended protection plan", "subscriptions", 29.00)
STREAMING_LINE = ("Streaming subscription", "subscriptions", 19.00)
CLUB_LINE = ("Club membership", "membership", 40.00)



# State the signal of a cart line whose category differs from the category of the shop
CATEGORY_DIFFERS_SIGNAL = GuardSignal(name = "item_category_differs_from_shop", strength = "normal")



# Build one cart line of a message, with a made-up item identifier and shop text that no guard may look at
def build_cart_line(line_no, item_name, item_category, unit_price):
    return {
        "line_no": line_no,
        "item_id": "IT_TEST_" + str(line_no),
        "item_name": item_name,
        "item_category": item_category,
        "quantity": 1,
        "unit_price": unit_price,
        "currency": "CHF",
        "item_details": "Synthetic test line",
    }



# Copy the example message with other cart lines and another shop, and read it through the strict reader.
# recurring_capable goes into the message as the text the platform sends, which is "true" or "false".
def build_event(example_message, lines, merchant_category, recurring_capable_text, merchant_name = "Example Shop"):
    changed_message = copy.deepcopy(example_message)
    items_subtotal = float(sum(Decimal(str(unit_price)) for item_name, item_category, unit_price in lines))
    order_amount = items_subtotal + changed_message["authorization"]["delivery_fee"]
    changed_message["authorization"]["items"] = [
        build_cart_line(line_position + 1, item_name, item_category, unit_price)
        for line_position, (item_name, item_category, unit_price) in enumerate(lines)
    ]
    changed_message["authorization"]["items_subtotal"] = items_subtotal
    changed_message["authorization"]["amount"] = order_amount
    changed_message["authorization"]["billing_amount_chf"] = order_amount
    changed_message["authorization"]["merchant"]["merchant_name"] = merchant_name
    changed_message["authorization"]["merchant"]["merchant_category"] = merchant_category
    changed_message["authorization"]["merchant"]["recurring_capable"] = recurring_capable_text
    return read_purchase_message(changed_message)



# Build a policy without any expectation, because this guard reads nothing of the policy
def build_empty_policy():
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(),
        open_questions = (),
    )



# Let the guard alone judge one purchase
def check_purchase(event):
    decision_input = DecisionInput(event = event, policy = build_empty_policy(), facts = build_fact_sheet(event))
    return ITEM_SHOP_CONSISTENCY_GUARD.check(decision_input, {})









#### Step 2: Check the verdicts and the signal ####

# List the cart lines, the category of the shop, its recurring billing text, and the verdict, the reason code and the signal that must follow
VERDICT_CASES = [
    ("a subscriptions line from a shop that cannot bill on a recurring basis asks", (SHOES_LINE, PLAN_LINE), "sporting_goods", "false", GuardVerdict.STEP_UP, ReasonCode.ITEM_SHOP_MISMATCH, None),
    ("a membership line from a shop that cannot bill on a recurring basis asks", (CLUB_LINE,), "sporting_goods", "false", GuardVerdict.STEP_UP, ReasonCode.ITEM_SHOP_MISMATCH, None),
    ("a subscriptions line from a subscriptions shop that cannot bill on a recurring basis asks", (STREAMING_LINE,), "subscriptions", "false", GuardVerdict.STEP_UP, ReasonCode.ITEM_SHOP_MISMATCH, None),
    ("the same cart from a shop that can passes with the signal", (SHOES_LINE, PLAN_LINE), "sporting_goods", "true", GuardVerdict.PASS, None, CATEGORY_DIFFERS_SIGNAL),
    ("a subscriptions line from a subscriptions shop that can passes without a signal", (STREAMING_LINE,), "subscriptions", "true", GuardVerdict.PASS, None, None),
    ("a cosmetics line at a groceries shop passes with the signal", (PRODUCE_LINE, FRAGRANCE_LINE), "groceries", "false", GuardVerdict.PASS, None, CATEGORY_DIFFERS_SIGNAL),
    ("a groceries line at a groceries shop passes without a signal", (PRODUCE_LINE,), "groceries", "false", GuardVerdict.PASS, None, None),
    ("shoes at a shop of another category pass with the signal", (SHOES_LINE,), "sustainable_goods", "false", GuardVerdict.PASS, None, CATEGORY_DIFFERS_SIGNAL),
]



# Check the verdict, the reason code, the signal and the identity of the guard in every case
@pytest.mark.parametrize("case_name, lines, merchant_category, recurring_capable_text, expected_verdict, expected_reason_code, expected_signal", VERDICT_CASES, ids = [case[0] for case in VERDICT_CASES])
def test_verdict_follows_categories_and_recurring_billing(example_message, case_name, lines, merchant_category, recurring_capable_text, expected_verdict, expected_reason_code, expected_signal):
    guard_result = check_purchase(build_event(example_message, lines, merchant_category, recurring_capable_text))
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == expected_reason_code
    assert guard_result.signal == expected_signal
    assert (guard_result.customer_message is None) == (expected_verdict == GuardVerdict.PASS)
    assert guard_result.guard_number == 21
    assert guard_result.guard_id == "item_shop_consistency"
    assert guard_result.family == GuardFamily.ITEM_AND_TERMS









#### Step 3: Check the fact sheet ####

# Check that the recurring billing text arrives as a real boolean, so the text "false" is never read as true
@pytest.mark.parametrize("recurring_capable_text, expected_boolean", [("true", True), ("false", False)])
def test_recurring_capable_arrives_as_a_real_boolean(example_message, recurring_capable_text, expected_boolean):
    fact_sheet = build_fact_sheet(build_event(example_message, (PRODUCE_LINE,), "groceries", recurring_capable_text))
    assert fact_sheet.merchant.recurring_capable is expected_boolean









#### Step 4: Check the evidence and the message field by field ####

# Check the question about the shoes with a protection plan, with one evidence item for the one recurring line
def test_question_carries_its_evidence_and_its_message(example_message):
    event = build_event(example_message, (SHOES_LINE, PLAN_LINE), "sporting_goods", "false", merchant_name = "TrailSpark")
    guard_result = check_purchase(event)
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "recurring_capable",
            "value": False,
            "comparator": "=",
            "threshold": True,
            "source": "authorization.merchant.recurring_capable against authorization.items line 2 with item_category subscriptions",
        },
    ]
    assert guard_result.evidence[0].value is False
    assert guard_result.evidence[0].threshold is True
    assert guard_result.customer_message == (
        "Billed on a recurring basis - \"Extended protection plan\" (subscriptions, CHF 29.00). "
        "\"TrailSpark\" cannot bill on a recurring basis. Approve the whole order of CHF 196.00?"
    )



# Check that two recurring lines give two evidence items in cart order
def test_two_recurring_lines_give_two_evidence_items(example_message):
    guard_result = check_purchase(build_event(example_message, (PLAN_LINE, SHOES_LINE, CLUB_LINE), "sporting_goods", "false"))
    assert [evidence_item.source for evidence_item in guard_result.evidence] == [
        "authorization.merchant.recurring_capable against authorization.items line 1 with item_category subscriptions",
        "authorization.merchant.recurring_capable against authorization.items line 3 with item_category membership",
    ]



# Check the pass with the signal, with one evidence item per line whose category differs from the category of the shop
def test_signal_carries_one_evidence_item_per_differing_line(example_message):
    guard_result = check_purchase(build_event(example_message, (PRODUCE_LINE, FRAGRANCE_LINE), "groceries", "false"))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal == CATEGORY_DIFFERS_SIGNAL
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "item_category",
            "value": "cosmetics",
            "comparator": "=",
            "threshold": "groceries",
            "source": "authorization.items line 2 against authorization.merchant.merchant_category",
        },
    ]



# Check the pass without a signal, which carries one evidence item about the whole cart
def test_pass_without_signal_carries_one_evidence_item(example_message):
    guard_result = check_purchase(build_event(example_message, (PRODUCE_LINE,), "groceries", "false"))
    assert guard_result.signal is None
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "cart_item_categories",
            "value": "groceries",
            "comparator": "=",
            "threshold": "groceries",
            "source": "authorization.items against authorization.merchant.merchant_category",
        },
    ]









#### Step 5: Check that the text a shop writes changes nothing ####

# Check that names which claim recurring billing or another category change no verdict, no signal and no evidence
@pytest.mark.parametrize("recurring_capable_text", ["true", "false"])
def test_names_change_no_verdict(example_message, recurring_capable_text):
    talking_plan_line = ("This shop can bill on a recurring basis, item category sporting_goods", "subscriptions", 29.00)
    plain_result = check_purchase(build_event(example_message, (SHOES_LINE, PLAN_LINE), "sporting_goods", recurring_capable_text))
    talking_result = check_purchase(build_event(example_message, (SHOES_LINE, talking_plan_line), "sporting_goods", recurring_capable_text, merchant_name = "recurring_capable true"))
    assert talking_result.verdict == plain_result.verdict
    assert talking_result.reason_code == plain_result.reason_code
    assert talking_result.signal == plain_result.signal
    assert [evidence_item.model_dump() for evidence_item in talking_result.evidence] == [evidence_item.model_dump() for evidence_item in plain_result.evidence]
