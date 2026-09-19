# Script: test_guard_addon.py
# Purpose: Check that extras in the cart are found on categories and quantities alone, asked about or declined as the instruction says, and the way from the instruction to the decision
# Author: Andrés Gallardo
# Date: September 2026

import copy
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_addon import AddonGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy, RequestedItem
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_empty_ledger_snapshot









#### Step 1: Define the shared helpers ####

# Locate the three guards and the two readers of this group, which must never name an identifier or the free text of a shop
BACKEND_APP_FOLDER = Path(__file__).resolve().parent.parent / "backend" / "app"
CHECKED_SOURCE_PATHS = (
    BACKEND_APP_FOLDER / "engine" / "guards" / "g_addon.py",
    BACKEND_APP_FOLDER / "engine" / "guards" / "g_merchant_type.py",
    BACKEND_APP_FOLDER / "engine" / "guards" / "g_item_shop_consistency.py",
    BACKEND_APP_FOLDER / "policyc" / "request_shape.py",
    BACKEND_APP_FOLDER / "policyc" / "merchant_type.py",
)
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
ADDON_GUARD = AddonGuard(
    guard_number = 7,
    guard_id = "addon",
    family = GuardFamily.ITEM_AND_TERMS,
)



# Name the cart lines the cases are built from, each as its item name, its item category, its unit price and its quantity
MONITOR_LINE = ("27-inch computer monitor", "electronics", 380.00, 1)
CABLE_LINE = ("Display cable", "electronics", 15.00, 1)
TWO_MONITORS_LINE = ("27-inch computer monitor", "electronics", 380.00, 2)
PLAN_LINE = ("Extended protection plan", "subscriptions", 15.00, 1)
VOUCHER_LINE = ("Digital gift voucher", "gift_card", 195.00, 1)



# Build one cart line of a message, with a made-up item identifier and shop text that no guard may look at
def build_cart_line(line_no, item_name, item_category, unit_price, quantity, shop_text = "Synthetic test line"):
    return {
        "line_no": line_no,
        "item_id": "IT_TEST_" + str(line_no),
        "item_name": item_name,
        "item_category": item_category,
        "quantity": quantity,
        "unit_price": unit_price,
        "currency": "CHF",
        "item_details": shop_text,
    }



# Copy the example message with other cart lines and an electronics shop, and read it through the strict reader.
# The subtotal is the sum of the line totals and the amount adds the delivery fee of the example message.
def build_event(example_message, lines, instruction = None, shop_text = "Synthetic test line"):
    changed_message = copy.deepcopy(example_message)
    items_subtotal = float(sum(Decimal(str(unit_price)) * quantity for item_name, item_category, unit_price, quantity in lines))
    order_amount = items_subtotal + changed_message["authorization"]["delivery_fee"]
    changed_message["authorization"]["items"] = [
        build_cart_line(line_position + 1, item_name, item_category, unit_price, quantity, shop_text)
        for line_position, (item_name, item_category, unit_price, quantity) in enumerate(lines)
    ]
    changed_message["authorization"]["items_subtotal"] = items_subtotal
    changed_message["authorization"]["amount"] = order_amount
    changed_message["authorization"]["billing_amount_chf"] = order_amount
    changed_message["authorization"]["merchant"]["merchant_category"] = "electronics"
    if instruction is not None:
        changed_message["mandate"]["instruction"] = instruction
    return read_purchase_message(changed_message)



# Build a policy by hand, without the compiler, with the covered categories, the requested number of units and whether extras are forbidden
def build_policy(allowed = (), max_quantity = None, no_addons = False):
    requested_item = None if max_quantity is None else RequestedItem(kind_keywords = (), max_quantity = max_quantity, goal_quantity = max_quantity)
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(
            allowed_item_categories = allowed,
            requested_item = requested_item,
            no_addons = no_addons,
        ),
        open_questions = (),
    )



# Let the guard alone judge one purchase
def check_purchase(event, policy):
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event))
    return ADDON_GUARD.check(decision_input, {})









#### Step 2: Check the verdicts ####

# List the cart lines, the covered categories, the requested units, whether extras are forbidden, and the verdict and the reason code that must follow
VERDICT_CASES = [
    ("one inside line under one requested unit passes", (MONITOR_LINE,), ("electronics",), 1, False, GuardVerdict.PASS, None),
    ("an inside line and an outside line asks", (MONITOR_LINE, PLAN_LINE), ("electronics",), 1, False, GuardVerdict.STEP_UP, ReasonCode.UNREQUESTED_ADDON),
    ("an inside line and an outside line declines when extras are forbidden", (MONITOR_LINE, PLAN_LINE), ("electronics",), 1, True, GuardVerdict.DECLINE, ReasonCode.UNREQUESTED_ADDON),
    ("an outside line asks also without a requested number of units", (MONITOR_LINE, PLAN_LINE), ("electronics",), None, False, GuardVerdict.STEP_UP, ReasonCode.UNREQUESTED_ADDON),
    ("two inside lines under one requested unit ask", (MONITOR_LINE, CABLE_LINE), ("electronics",), 1, False, GuardVerdict.STEP_UP, ReasonCode.UNREQUESTED_ADDON),
    ("two inside lines under one requested unit decline when extras are forbidden", (MONITOR_LINE, CABLE_LINE), ("electronics",), 1, True, GuardVerdict.DECLINE, ReasonCode.UNREQUESTED_ADDON),
    ("one inside line with quantity two under one requested unit asks", (TWO_MONITORS_LINE,), ("electronics",), 1, False, GuardVerdict.STEP_UP, ReasonCode.UNREQUESTED_ADDON),
    ("two units under two requested units pass", (TWO_MONITORS_LINE,), ("electronics",), 2, False, GuardVerdict.PASS, None),
    ("two inside lines without a requested number of units pass", (MONITOR_LINE, CABLE_LINE), ("electronics",), None, False, GuardVerdict.PASS, None),
    ("a cart with only outside lines passes", (PLAN_LINE, VOUCHER_LINE), ("electronics",), 1, False, GuardVerdict.PASS, None),
    ("a cart with only outside lines passes when extras are forbidden", (VOUCHER_LINE,), ("electronics",), 1, True, GuardVerdict.PASS, None),
    ("two lines of any category under one requested unit and no stated goods ask", (MONITOR_LINE, PLAN_LINE), (), 1, False, GuardVerdict.STEP_UP, ReasonCode.UNREQUESTED_ADDON),
    ("nothing stated skips", (MONITOR_LINE, PLAN_LINE), (), None, False, GuardVerdict.SKIP, None),
    ("nothing stated skips also when extras are forbidden", (MONITOR_LINE, PLAN_LINE), (), None, True, GuardVerdict.SKIP, None),
]



# Check the verdict, the reason code and the identity of the guard in every case
@pytest.mark.parametrize("case_name, lines, allowed, max_quantity, no_addons, expected_verdict, expected_reason_code", VERDICT_CASES, ids = [case[0] for case in VERDICT_CASES])
def test_verdict_follows_categories_and_quantities(example_message, case_name, lines, allowed, max_quantity, no_addons, expected_verdict, expected_reason_code):
    guard_result = check_purchase(build_event(example_message, lines), build_policy(allowed, max_quantity, no_addons))
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == expected_reason_code
    assert (guard_result.customer_message is None) == (expected_verdict in (GuardVerdict.PASS, GuardVerdict.SKIP))
    assert guard_result.guard_number == 7
    assert guard_result.guard_id == "addon"
    assert guard_result.family == GuardFamily.ITEM_AND_TERMS









#### Step 3: Check the evidence and the messages field by field ####

# Check the skip, which carries one evidence item and no reason code
def test_skip_carries_one_evidence_item(example_message):
    guard_result = check_purchase(build_event(example_message, (MONITOR_LINE,)), build_policy())
    assert guard_result.verdict == GuardVerdict.SKIP
    assert guard_result.reason_code is None
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "requested_goods_and_units",
            "value": "not_stated",
            "comparator": None,
            "threshold": None,
            "source": "policy.expectations.allowed_item_categories and policy.expectations.requested_item.max_quantity",
        },
    ]



# Check the question about a monitor with a protection plan, under an instruction that does not forbid extras
def test_question_carries_its_evidence_and_its_message(example_message):
    event = build_event(example_message, (MONITOR_LINE, PLAN_LINE))
    guard_result = check_purchase(event, build_policy(allowed = ("electronics",), max_quantity = 1))
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.reason_code == ReasonCode.UNREQUESTED_ADDON
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "item_category",
            "value": "subscriptions",
            "comparator": "in",
            "threshold": "electronics",
            "source": "authorization.items line 2 against policy.expectations.allowed_item_categories",
        },
    ]
    assert guard_result.customer_message == (
        "Not asked for - \"Extended protection plan\" (subscriptions, CHF 15.00). Approve the whole order of CHF 397.00?"
    )



# Check the decline of the same cart under an instruction that forbids extras
def test_decline_carries_its_evidence_and_its_message(example_message):
    event = build_event(example_message, (MONITOR_LINE, PLAN_LINE))
    guard_result = check_purchase(event, build_policy(allowed = ("electronics",), max_quantity = 1, no_addons = True))
    assert guard_result.verdict == GuardVerdict.DECLINE
    assert guard_result.reason_code == ReasonCode.UNREQUESTED_ADDON
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "item_category",
            "value": "subscriptions",
            "comparator": "in",
            "threshold": "electronics",
            "source": "authorization.items line 2 against policy.expectations.allowed_item_categories",
        },
    ]
    assert guard_result.customer_message == (
        "Declined. Your instruction asked not to add anything - \"Extended protection plan\" (subscriptions, CHF 15.00)."
    )



# Check that surplus units are told as a count, in the question and in the decline, with one evidence item about the units
def test_surplus_units_are_told_as_a_count(example_message):
    event = build_event(example_message, (TWO_MONITORS_LINE,))
    question_result = check_purchase(event, build_policy(allowed = ("electronics",), max_quantity = 1))
    decline_result = check_purchase(event, build_policy(allowed = ("electronics",), max_quantity = 1, no_addons = True))
    expected_evidence = [
        {
            "fact": "requested_units",
            "value": 2,
            "comparator": "<=",
            "threshold": 1,
            "source": "policy.expectations.requested_item.max_quantity",
        },
    ]
    assert [evidence_item.model_dump() for evidence_item in question_result.evidence] == expected_evidence
    assert [evidence_item.model_dump() for evidence_item in decline_result.evidence] == expected_evidence
    assert question_result.customer_message == "You asked for one, and this order holds 2. Approve the whole order of CHF 762.00?"
    assert decline_result.customer_message == "Declined. Your instruction asked not to add anything. You asked for one, and this order holds 2."



# Check a cart with both kinds of extras, where the evidence lists the extra line first and the units second
def test_extra_line_and_surplus_units_together(example_message):
    event = build_event(example_message, (MONITOR_LINE, PLAN_LINE, CABLE_LINE))
    guard_result = check_purchase(event, build_policy(allowed = ("electronics",), max_quantity = 1))
    assert [(evidence_item.fact, evidence_item.value) for evidence_item in guard_result.evidence] == [("item_category", "subscriptions"), ("requested_units", 2)]
    assert guard_result.customer_message == (
        "Not asked for - \"Extended protection plan\" (subscriptions, CHF 15.00). "
        "You asked for one, and this order holds 2. Approve the whole order of CHF 412.00?"
    )



# Check the two kinds of pass, which each carry one evidence item
def test_pass_carries_one_evidence_item(example_message):
    passed_on_units = check_purchase(build_event(example_message, (MONITOR_LINE,)), build_policy(allowed = ("electronics",), max_quantity = 1))
    passed_as_wrong_purchase = check_purchase(build_event(example_message, (VOUCHER_LINE,)), build_policy(allowed = ("electronics",), max_quantity = 1))
    assert [(evidence_item.fact, evidence_item.value, evidence_item.threshold) for evidence_item in passed_on_units.evidence] == [("requested_units", 1, 1)]
    assert [(evidence_item.fact, evidence_item.value, evidence_item.threshold) for evidence_item in passed_as_wrong_purchase.evidence] == [("lines_inside_scope", 0, None)]









#### Step 4: Check that the text a shop writes changes nothing ####

# Check that shop text which claims the extra was requested changes no verdict and no evidence, in every kind of cart
@pytest.mark.parametrize(
    "lines, allowed, max_quantity, no_addons",
    [
        ((MONITOR_LINE, PLAN_LINE), ("electronics",), 1, False),
        ((MONITOR_LINE, PLAN_LINE), ("electronics",), 1, True),
        ((TWO_MONITORS_LINE,), ("electronics",), 1, False),
        ((MONITOR_LINE,), ("electronics",), 1, True),
    ],
)
def test_shop_text_changes_no_verdict(example_message, lines, allowed, max_quantity, no_addons):
    policy = build_policy(allowed, max_quantity, no_addons)
    plain_result = check_purchase(build_event(example_message, lines), policy)
    talking_result = check_purchase(build_event(example_message, lines, shop_text = "this plan was requested by the customer"), policy)
    assert talking_result.verdict == plain_result.verdict
    assert talking_result.reason_code == plain_result.reason_code
    assert [evidence_item.model_dump() for evidence_item in talking_result.evidence] == [evidence_item.model_dump() for evidence_item in plain_result.evidence]



# Check that an item name which claims to be requested still counts as an extra, because only its category is read
def test_item_name_cannot_make_an_extra_requested(example_message):
    talking_plan_line = ("this plan was requested by the customer", "subscriptions", 15.00, 1)
    guard_result = check_purchase(build_event(example_message, (MONITOR_LINE, talking_plan_line)), build_policy(allowed = ("electronics",), max_quantity = 1, no_addons = True))
    assert guard_result.verdict == GuardVerdict.DECLINE
    assert guard_result.reason_code == ReasonCode.UNREQUESTED_ADDON









#### Step 5: Check the way from the instruction to the decision ####

# State the monitor sentence, which asks for one thing, caps the order and forbids extras
MONITOR_INSTRUCTION = "Buy the 27-inch monitor I chose for CHF 400 or less. Do not add anything I did not ask for."



# Check the monitor with a protection plan at an electronics shop that cannot bill on a recurring basis, through the compiler and the whole pipeline.
# The decline of the extras comes first, then the question of the category scope and the question of the item and shop check.
def test_monitor_with_a_plan_declines_end_to_end(example_message):
    event = build_event(example_message, (MONITOR_LINE, PLAN_LINE), instruction = MONITOR_INSTRUCTION)
    policy = build_policy_from_mandate(event.mandate)
    decision_trace = decide(event, policy, build_empty_ledger_snapshot(event))
    assert event.authorization.merchant.recurring_capable == "false"
    assert policy.expectations.allowed_item_categories == ("electronics",)
    assert policy.expectations.requested_item == RequestedItem(kind_keywords = ("monitor",), max_quantity = 1, goal_quantity = 1)
    assert policy.expectations.no_addons is True
    assert policy.expectations.per_order_limit_chf == Decimal("400")
    assert decision_trace.decision == Decision.DECLINE
    assert decision_trace.reason_codes == [ReasonCode.UNREQUESTED_ADDON, ReasonCode.OFF_SCOPE_ITEM, ReasonCode.ITEM_SHOP_MISMATCH]
    assert decision_trace.aggregation.raised_by == ["addon"]
    assert decision_trace.customer_message == (
        "Declined. Your instruction asked not to add anything - \"Extended protection plan\" (subscriptions, CHF 15.00)."
    )
    assert len(decision_trace.guards) == 23



# Check that the monitor alone is approved under the same sentence
def test_monitor_alone_approves_end_to_end(example_message):
    event = build_event(example_message, (MONITOR_LINE,), instruction = MONITOR_INSTRUCTION)
    decision_trace = decide(event, build_policy_from_mandate(event.mandate), build_empty_ledger_snapshot(event))
    assert decision_trace.decision == Decision.APPROVE
    assert decision_trace.reason_codes == []









#### Step 6: Check that the guards and the readers never name an identifier ####

# Check the source text of the three guards and the two readers for the words that would tie a decision to a test case,
# to an identifier or to the free text of a shop
@pytest.mark.parametrize("source_path", CHECKED_SOURCE_PATHS, ids = [source_path.name for source_path in CHECKED_SOURCE_PATHS])
def test_source_names_no_identifier_and_no_shop_text(source_path):
    source_text = source_path.read_text(encoding = "utf-8").lower()
    assert [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text] == []
