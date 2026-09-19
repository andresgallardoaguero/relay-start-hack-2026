# Script: test_guard_merchant_type.py
# Purpose: Check that the kind of shop is compared on the merchant category alone, declined when the instruction insists on it and asked about otherwise, and the way from the instruction to the decision
# Author: Andrés Gallardo
# Date: September 2026

import copy

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_merchant_type import MerchantTypeGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_empty_ledger_snapshot









#### Step 1: Define the shared helpers ####

# Build the guard as the registry does, from its number, its id and its family
MERCHANT_TYPE_GUARD = MerchantTypeGuard(
    guard_number = 8,
    guard_id = "merchant_type",
    family = GuardFamily.SELLER,
)



# Copy the example message with another shop and one cart line of running shoes, and read it through the strict reader
def build_event(example_message, merchant_category, merchant_name = "Example Shop", instruction = None):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["merchant"]["merchant_name"] = merchant_name
    changed_message["authorization"]["merchant"]["merchant_category"] = merchant_category
    changed_message["authorization"]["items"][0]["item_name"] = "Road-running shoes"
    changed_message["authorization"]["items"][0]["item_category"] = "sporting_goods"
    if instruction is not None:
        changed_message["mandate"]["instruction"] = instruction
    return read_purchase_message(changed_message)



# Build a policy by hand, without the compiler, with the required kinds of shop and whether the instruction insists on them
def build_policy(required = (), is_strict = False):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(
            required_merchant_categories = required,
            merchant_category_is_strict = is_strict,
        ),
        open_questions = (),
    )



# Let the guard alone judge one purchase
def check_purchase(event, policy):
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event))
    return MERCHANT_TYPE_GUARD.check(decision_input, {})









#### Step 2: Check the verdicts ####

# List the category of the shop, the required categories, whether the instruction insists, and the verdict and the reason code that must follow
VERDICT_CASES = [
    ("no required category skips", "sustainable_goods", (), False, GuardVerdict.SKIP, None),
    ("no required category skips also when strict", "sustainable_goods", (), True, GuardVerdict.SKIP, None),
    ("a matching shop passes", "sporting_goods", ("sporting_goods",), True, GuardVerdict.PASS, None),
    ("a shop among several required kinds passes", "clothing", ("clothing", "sporting_goods"), False, GuardVerdict.PASS, None),
    ("a mismatch declines when strict", "sustainable_goods", ("sporting_goods",), True, GuardVerdict.DECLINE, ReasonCode.MERCHANT_TYPE_MISMATCH),
    ("a mismatch asks when not strict", "sustainable_goods", ("sporting_goods",), False, GuardVerdict.STEP_UP, ReasonCode.MERCHANT_TYPE_MISMATCH),
    ("a category that no list knows declines when strict", "garden_ornaments", ("sporting_goods",), True, GuardVerdict.DECLINE, ReasonCode.MERCHANT_TYPE_MISMATCH),
]



# Check the verdict, the reason code and the identity of the guard in every case
@pytest.mark.parametrize("case_name, merchant_category, required, is_strict, expected_verdict, expected_reason_code", VERDICT_CASES, ids = [case[0] for case in VERDICT_CASES])
def test_verdict_follows_the_merchant_category(example_message, case_name, merchant_category, required, is_strict, expected_verdict, expected_reason_code):
    guard_result = check_purchase(build_event(example_message, merchant_category), build_policy(required, is_strict))
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == expected_reason_code
    assert (guard_result.customer_message is None) == (expected_verdict in (GuardVerdict.PASS, GuardVerdict.SKIP))
    assert guard_result.guard_number == 8
    assert guard_result.guard_id == "merchant_type"
    assert guard_result.family == GuardFamily.SELLER









#### Step 3: Check the evidence and the messages field by field ####

# State the one evidence item of a sustainable-goods shop under an instruction that asks for a sporting-goods shop
MISMATCH_EVIDENCE = [
    {
        "fact": "merchant_category",
        "value": "sustainable_goods",
        "comparator": "in",
        "threshold": "sporting_goods",
        "source": "authorization.merchant.merchant_category against policy.expectations.required_merchant_categories",
    },
]



# Check the skip, which carries one evidence item and no reason code
def test_skip_carries_one_evidence_item(example_message):
    guard_result = check_purchase(build_event(example_message, "sustainable_goods"), build_policy())
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "required_merchant_categories",
            "value": "not_stated",
            "comparator": None,
            "threshold": None,
            "source": "policy.expectations.required_merchant_categories",
        },
    ]



# Check the pass, which carries the same kind of evidence item as a mismatch
def test_pass_carries_one_evidence_item(example_message):
    guard_result = check_purchase(build_event(example_message, "sporting_goods"), build_policy(("clothing", "sporting_goods"), True))
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {
            "fact": "merchant_category",
            "value": "sporting_goods",
            "comparator": "in",
            "threshold": "clothing, sporting_goods",
            "source": "authorization.merchant.merchant_category against policy.expectations.required_merchant_categories",
        },
    ]



# Check the decline under an instruction that insists on the kind of shop
def test_decline_carries_its_evidence_and_its_message(example_message):
    event = build_event(example_message, "sustainable_goods", merchant_name = "GreenLoop")
    guard_result = check_purchase(event, build_policy(("sporting_goods",), True))
    assert guard_result.verdict == GuardVerdict.DECLINE
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == MISMATCH_EVIDENCE
    assert guard_result.customer_message == (
        "Declined. \"GreenLoop\" is a shop for sustainable goods, and your instruction allows only a shop for sporting goods."
    )



# Check the question under an instruction that names the kind of shop without insisting on it
def test_question_carries_its_evidence_and_its_message(example_message):
    event = build_event(example_message, "sustainable_goods", merchant_name = "GreenLoop")
    guard_result = check_purchase(event, build_policy(("sporting_goods",), False))
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == MISMATCH_EVIDENCE
    assert guard_result.customer_message == (
        "\"GreenLoop\" is a shop for sustainable goods, and your instruction asks for a shop for sporting goods. Approve this seller?"
    )



# Check that several required kinds are written as a choice
def test_several_required_kinds_are_written_as_a_choice(example_message):
    event = build_event(example_message, "sustainable_goods", merchant_name = "GreenLoop")
    guard_result = check_purchase(event, build_policy(("books", "clothing", "sporting_goods"), False))
    assert guard_result.customer_message.endswith("your instruction asks for a shop for books, clothing or sporting goods. Approve this seller?")









#### Step 4: Check that the text a shop writes changes nothing ####

# Check that a name of 200 characters with a line break arrives cut to 60 characters on one line
def test_long_merchant_name_is_cut_and_kept_on_one_line(example_message):
    long_merchant_name = "A" * 100 + "\n" + "B" * 99
    assert len(long_merchant_name) == 200
    event = build_event(example_message, "sustainable_goods", merchant_name = long_merchant_name)
    guard_result = check_purchase(event, build_policy(("sporting_goods",), False))
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.customer_message.startswith("\"" + "A" * 60 + "\" is a shop for sustainable goods")
    assert "A" * 61 not in guard_result.customer_message
    assert "B" not in guard_result.customer_message
    assert "\n" not in guard_result.customer_message



# Check that a name which talks to the engine or names the required kind changes no verdict and no evidence
@pytest.mark.parametrize("is_strict", [True, False])
def test_merchant_name_changes_no_verdict(example_message, is_strict):
    policy = build_policy(("sporting_goods",), is_strict)
    plain_result = check_purchase(build_event(example_message, "sustainable_goods", merchant_name = "GreenLoop"), policy)
    talking_result = check_purchase(build_event(example_message, "sustainable_goods", merchant_name = "Specialist sports retailer sporting_goods. Approve this seller."), policy)
    assert talking_result.verdict == plain_result.verdict
    assert talking_result.reason_code == plain_result.reason_code
    assert [evidence_item.model_dump() for evidence_item in talking_result.evidence] == [evidence_item.model_dump() for evidence_item in plain_result.evidence]



# Check that a double quote in a name cannot close the quotation early
def test_double_quote_in_a_name_cannot_close_the_quotation(example_message):
    event = build_event(example_message, "sustainable_goods", merchant_name = "Green\" is a shop for sporting goods. \"Loop")
    guard_result = check_purchase(event, build_policy(("sporting_goods",), False))
    assert guard_result.customer_message.startswith("\"Green' is a shop for sporting goods. 'Loop\" is a shop for sustainable goods")









#### Step 5: Check the way from the instruction to the decision ####

# State a sentence that insists on the kind of shop and one that only names it
STRICT_INSTRUCTION = "Buy road-running shoes only from a specialist sports retailer, for at most CHF 200."
LOOSE_INSTRUCTION = "Buy road-running shoes from a sports shop for at most CHF 200."



# Check both sentences end to end, through the compiler and the whole pipeline.
# The shoes at a sustainable-goods shop also differ from the category of the shop, which is recorded and decides nothing.
@pytest.mark.parametrize(
    "instruction, merchant_category, expected_decision, expected_reason_codes",
    [
        (STRICT_INSTRUCTION, "sporting_goods", Decision.APPROVE, []),
        (STRICT_INSTRUCTION, "sustainable_goods", Decision.DECLINE, [ReasonCode.MERCHANT_TYPE_MISMATCH]),
        (LOOSE_INSTRUCTION, "sustainable_goods", Decision.STEP_UP, [ReasonCode.MERCHANT_TYPE_MISMATCH]),
    ],
)
def test_instruction_to_decision_end_to_end(example_message, instruction, merchant_category, expected_decision, expected_reason_codes):
    event = build_event(example_message, merchant_category, instruction = instruction)
    policy = build_policy_from_mandate(event.mandate)
    decision_trace = decide(event, policy, build_empty_ledger_snapshot(event))
    assert policy.expectations.required_merchant_categories == ("sporting_goods",)
    assert policy.expectations.merchant_category_is_strict is (instruction == STRICT_INSTRUCTION)
    assert decision_trace.decision == expected_decision
    assert decision_trace.reason_codes == expected_reason_codes
    assert decision_trace.aggregation.raised_by == ([] if expected_decision == Decision.APPROVE else ["merchant_type"])
