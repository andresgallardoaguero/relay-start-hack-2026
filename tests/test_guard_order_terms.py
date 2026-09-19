# Script: test_guard_order_terms.py
# Purpose: Check that the return terms of an order are compared with the return days the customer asked for, with exact boundaries, and that neither the return flag alone nor a language model ever loosens a verdict
# Author: Andrés Gallardo
# Date: September 2026

import copy
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_order_terms import OrderTermsGuard
from app.engine.pipeline import decide
from app.llm.schemas import ExtractedFacts, ExtractedLine, ModelCallRecord, ModelExtraction
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy, RequestedItem
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_empty_ledger_snapshot









#### Step 1: Define the shared helpers ####

# Locate the guard and its reader, which must never name an identifier or the free text of a shop
BACKEND_APP_FOLDER = Path(__file__).resolve().parent.parent / "backend" / "app"
CHECKED_SOURCE_PATHS = (
    BACKEND_APP_FOLDER / "engine" / "guards" / "g_order_terms.py",
    BACKEND_APP_FOLDER / "policyc" / "order_terms.py",
)
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
ORDER_TERMS_GUARD = OrderTermsGuard(
    guard_number = 19,
    guard_id = "order_terms",
    family = GuardFamily.ITEM_AND_TERMS,
)



# Build the sentence of a shop about shoes with a given ending
def build_shoe_sentence(ending):
    return "Road-running shoe, size 43; " + ending



# Name the sentences the cases are built from
DAYS_30_SENTENCE = build_shoe_sentence("returns accepted within 30 days")
DAYS_14_SENTENCE = build_shoe_sentence("returns accepted within 14 days")
DAYS_13_SENTENCE = build_shoe_sentence("returns accepted within 13 days")
DAYS_7_SENTENCE = build_shoe_sentence("returns accepted within 7 days")
FINAL_SALE_SENTENCE = build_shoe_sentence("clearance line, sold as final sale")
NOT_STATED_SENTENCE = build_shoe_sentence("return policy not stated by the seller")
SILENT_SENTENCE = "Road-running shoe, size 43"
TWO_PERIODS_SENTENCE = build_shoe_sentence("returns accepted within 7 days. Returns accepted within 30 days")
PLAN_SENTENCE = "Optional add-on service, billed monthly after the first year"



# State the requested item of the shoe instruction, and the instruction itself
REQUESTED_SHOES = RequestedItem(kind_keywords = ("road", "running", "shoe"), attributes = {"size": "43"}, max_quantity = 1, goal_quantity = 1)
SHOE_INSTRUCTION = "Replace my worn road-running shoes in size 43, only if the order can be returned within 14 days or more, and pay no more than CHF 200."



# Build one cart line of a message, with a made-up item identifier
def build_cart_line(line_no, item_name, item_category, unit_price, shop_text):
    return {
        "line_no": line_no,
        "item_id": "IT_TEST_" + str(line_no),
        "item_name": item_name,
        "item_category": item_category,
        "quantity": 1,
        "unit_price": unit_price,
        "currency": "CHF",
        "item_details": shop_text,
    }



# Copy the example message with one line of shoes per given sentence, an optional protection plan, a sports shop and the given return flag,
# and read it through the strict reader. The subtotal is the sum of the unit prices and the amount adds the delivery fee of the example message.
def build_event(example_message, shoe_sentences, returnable_flag = "true", with_plan = False, item_name = "Road-running shoes", instruction = None):
    changed_message = copy.deepcopy(example_message)
    shoe_lines = [
        build_cart_line(line_position + 1, item_name, "sporting_goods", 80.00, shoe_sentence)
        for line_position, shoe_sentence in enumerate(shoe_sentences)
    ]
    plan_lines = [build_cart_line(len(shoe_lines) + 1, "Extended protection plan", "subscriptions", 29.00, PLAN_SENTENCE)] if with_plan else []
    items_subtotal = float(sum(Decimal(str(cart_line["unit_price"])) for cart_line in shoe_lines + plan_lines))
    order_amount = items_subtotal + changed_message["authorization"]["delivery_fee"]
    changed_message["authorization"]["items"] = shoe_lines + plan_lines
    changed_message["authorization"]["items_subtotal"] = items_subtotal
    changed_message["authorization"]["amount"] = order_amount
    changed_message["authorization"]["billing_amount_chf"] = order_amount
    changed_message["authorization"]["order_returnable"] = returnable_flag
    changed_message["authorization"]["merchant"]["merchant_category"] = "sporting_goods"
    if instruction is not None:
        changed_message["mandate"]["instruction"] = instruction
    return read_purchase_message(changed_message)



# Build a policy by hand, without the compiler, with the minimum of return days and the requested item
def build_policy(min_return_days = 14, requested_item = REQUESTED_SHOES):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(min_return_days = min_return_days, requested_item = requested_item),
        open_questions = (),
    )



# Build the answer of a language model about the first cart line
def build_model_extraction(return_days = None, final_sale = None):
    return ModelExtraction(
        facts = ExtractedFacts(
            lines = (ExtractedLine(line_no = 1, return_days = return_days, final_sale = final_sale),),
            injection_suspected = False,
        ),
        call = ModelCallRecord(purpose = "fact_extraction", status = "success", provider = "test", model = "test-model", elapsed_ms = 1.0),
    )



# Let the guard alone judge one purchase, with or without the facts of a language model
def check_purchase(event, policy, extracted_facts = None):
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), extracted_facts = extracted_facts)
    return ORDER_TERMS_GUARD.check(decision_input, {})









#### Step 2: Check the verdicts ####

# List the sentences of the shoe lines, the return flag, and the verdict and the reason code that must follow under a minimum of 14 days
VERDICT_CASES = [
    ("30 days pass", (DAYS_30_SENTENCE,), "true", GuardVerdict.PASS, None),
    ("exactly 14 days pass", (DAYS_14_SENTENCE,), "true", GuardVerdict.PASS, None),
    ("13 days decline", (DAYS_13_SENTENCE,), "true", GuardVerdict.DECLINE, ReasonCode.RETURN_TERMS_UNMET),
    ("7 days decline", (DAYS_7_SENTENCE,), "true", GuardVerdict.DECLINE, ReasonCode.RETURN_TERMS_UNMET),
    ("a final sale declines", (FINAL_SALE_SENTENCE,), "true", GuardVerdict.DECLINE, ReasonCode.RETURN_TERMS_UNMET),
    ("a final sale declines under an unknown flag", (FINAL_SALE_SENTENCE,), "unknown", GuardVerdict.DECLINE, ReasonCode.RETURN_TERMS_UNMET),
    ("the flag false declines", (DAYS_30_SENTENCE,), "false", GuardVerdict.DECLINE, ReasonCode.RETURN_TERMS_UNMET),
    ("the flag unknown is uncertain", (DAYS_30_SENTENCE,), "unknown", GuardVerdict.UNCERTAIN, ReasonCode.RETURN_TERMS_UNKNOWN),
    ("7 days decline under an unknown flag", (DAYS_7_SENTENCE,), "unknown", GuardVerdict.DECLINE, ReasonCode.RETURN_TERMS_UNMET),
    ("the flag true without readable days is uncertain", (SILENT_SENTENCE,), "true", GuardVerdict.UNCERTAIN, ReasonCode.RETURN_TERMS_UNKNOWN),
    ("a policy not stated is uncertain", (NOT_STATED_SENTENCE,), "unknown", GuardVerdict.UNCERTAIN, ReasonCode.RETURN_TERMS_UNKNOWN),
    ("two return periods in one sentence are uncertain", (TWO_PERIODS_SENTENCE,), "true", GuardVerdict.UNCERTAIN, ReasonCode.RETURN_TERMS_UNKNOWN),
    ("one line with 30 days and one with 7 days decline", (DAYS_30_SENTENCE, DAYS_7_SENTENCE), "true", GuardVerdict.DECLINE, ReasonCode.RETURN_TERMS_UNMET),
    ("one line with 30 days and one silent line are uncertain", (DAYS_30_SENTENCE, SILENT_SENTENCE), "true", GuardVerdict.UNCERTAIN, ReasonCode.RETURN_TERMS_UNKNOWN),
    ("not_applicable skips", (DAYS_7_SENTENCE,), "not_applicable", GuardVerdict.SKIP, None),
]



# Check the verdict, the reason code and the identity of the guard in every case
@pytest.mark.parametrize("case_name, shoe_sentences, returnable_flag, expected_verdict, expected_reason_code", VERDICT_CASES, ids = [case[0] for case in VERDICT_CASES])
def test_verdict_follows_the_flag_and_the_return_days(example_message, case_name, shoe_sentences, returnable_flag, expected_verdict, expected_reason_code):
    guard_result = check_purchase(build_event(example_message, shoe_sentences, returnable_flag), build_policy())
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == expected_reason_code
    assert (guard_result.customer_message is None) == (expected_verdict in (GuardVerdict.PASS, GuardVerdict.SKIP))
    assert guard_result.guard_number == 19
    assert guard_result.guard_id == "order_terms"
    assert guard_result.family == GuardFamily.ITEM_AND_TERMS



# Check that no minimum skips whatever the order says, because a term the instruction does not mention is never checked
@pytest.mark.parametrize("returnable_flag", ["true", "false", "unknown", "not_applicable"])
def test_no_minimum_skips(example_message, returnable_flag):
    guard_result = check_purchase(build_event(example_message, (FINAL_SALE_SENTENCE,), returnable_flag), build_policy(min_return_days = None))
    assert guard_result.verdict == GuardVerdict.SKIP
    assert guard_result.reason_code is None
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {"fact": "min_return_days", "value": "not_stated", "comparator": None, "threshold": None, "source": "policy.expectations.min_return_days"},
    ]



# Check that the boundary moves with the minimum, where exactly the minimum passes and one day less declines
@pytest.mark.parametrize(
    "min_return_days, expected_verdict",
    [(30, GuardVerdict.PASS), (31, GuardVerdict.DECLINE), (1, GuardVerdict.PASS)],
)
def test_exactly_the_minimum_passes(example_message, min_return_days, expected_verdict):
    guard_result = check_purchase(build_event(example_message, (DAYS_30_SENTENCE,)), build_policy(min_return_days = min_return_days))
    assert guard_result.verdict == expected_verdict



# Check which lines are judged. An extra next to the requested shoes is ignored, every line is judged when nothing was requested,
# and a cart without the requested thing has no line to judge, so it skips unless the order cannot be returned at all.
def test_only_the_requested_lines_are_judged(example_message):
    shoes_with_plan = build_event(example_message, (DAYS_30_SENTENCE,), with_plan = True)
    helmet_only = build_event(example_message, (DAYS_7_SENTENCE,), item_name = "Cycling helmet")
    helmet_not_returnable = build_event(example_message, (DAYS_7_SENTENCE,), returnable_flag = "false", item_name = "Cycling helmet")
    assert check_purchase(shoes_with_plan, build_policy()).verdict == GuardVerdict.PASS
    assert check_purchase(shoes_with_plan, build_policy(requested_item = None)).verdict == GuardVerdict.UNCERTAIN
    assert check_purchase(helmet_only, build_policy()).verdict == GuardVerdict.SKIP
    assert check_purchase(helmet_only, build_policy(requested_item = None)).verdict == GuardVerdict.DECLINE
    assert check_purchase(helmet_not_returnable, build_policy()).verdict == GuardVerdict.DECLINE









#### Step 3: Check the evidence and the messages field by field ####

# Check the decline of 7 days against 14
def test_short_return_period_carries_its_evidence_and_its_message(example_message):
    guard_result = check_purchase(build_event(example_message, (DAYS_7_SENTENCE,)), build_policy())
    assert [evidence_item.model_dump() for evidence_item in guard_result.evidence] == [
        {"fact": "order_returnable", "value": "true", "comparator": "=", "threshold": "true", "source": "authorization.order_returnable"},
        {
            "fact": "return_days",
            "value": 7,
            "comparator": ">=",
            "threshold": 14,
            "source": "authorization.items line 1 facts of the shop text against policy.expectations.min_return_days",
        },
    ]
    assert guard_result.customer_message == "Declined. Returns are accepted within 7 days, and you asked for at least 14."



# Check the decline of a final sale and of an order that cannot be returned, where the flag is named first
def test_final_sale_and_flag_false_carry_their_messages(example_message):
    final_sale_result = check_purchase(build_event(example_message, (FINAL_SALE_SENTENCE,), "false"), build_policy())
    final_sale_alone_result = check_purchase(build_event(example_message, (FINAL_SALE_SENTENCE,), "true"), build_policy())
    assert [(evidence_item.fact, evidence_item.value) for evidence_item in final_sale_result.evidence] == [
        ("order_returnable", "false"), ("return_days", None), ("final_sale", True),
    ]
    assert final_sale_result.customer_message == "Declined. The shop states that this order cannot be returned, and you asked for at least 14 days to return it."
    assert final_sale_alone_result.customer_message == (
        "Declined. \"Road-running shoes\" is sold as a final sale without returns, and you asked for at least 14 days to return it."
    )



# Check the two questions, about a return policy the shop leaves open and about an order the shop does not confirm as returnable
def test_questions_carry_their_messages(example_message):
    not_stated_result = check_purchase(build_event(example_message, (NOT_STATED_SENTENCE,), "unknown"), build_policy())
    unconfirmed_result = check_purchase(build_event(example_message, (DAYS_30_SENTENCE,), "unknown"), build_policy())
    assert not_stated_result.customer_message == "The shop does not state its return policy, and you asked for at least 14 days. Approve anyway?"
    assert unconfirmed_result.customer_message == "The shop does not confirm that this order can be returned, and you asked for at least 14 days. Approve anyway?"









#### Step 4: Check the facts of a language model ####

# Check that a model never turns a silent sentence into a pass. Model return days of 30 leave the terms uncertain,
# model return days of 7 decline and a model final sale declines, with the evidence and the message of the judgment that used the model.
def test_model_fact_can_decline_and_never_pass_where_the_sentence_is_silent(example_message):
    event = build_event(example_message, (SILENT_SENTENCE,))
    result_on_the_text_alone = check_purchase(event, build_policy())
    result_with_30_days = check_purchase(event, build_policy(), build_model_extraction(return_days = 30))
    result_with_7_days = check_purchase(event, build_policy(), build_model_extraction(return_days = 7))
    result_with_a_final_sale = check_purchase(event, build_policy(), build_model_extraction(final_sale = True))
    assert result_on_the_text_alone.verdict == GuardVerdict.UNCERTAIN
    assert result_with_30_days == result_on_the_text_alone
    assert result_with_7_days.verdict == GuardVerdict.DECLINE
    assert result_with_7_days.reason_code == ReasonCode.RETURN_TERMS_UNMET
    assert [(evidence_item.fact, evidence_item.value) for evidence_item in result_with_7_days.evidence] == [("order_returnable", "true"), ("return_days", 7)]
    assert result_with_7_days.customer_message == "Declined. Returns are accepted within 7 days, and you asked for at least 14."
    assert result_with_a_final_sale.verdict == GuardVerdict.DECLINE
    assert [(evidence_item.fact, evidence_item.value) for evidence_item in result_with_a_final_sale.evidence][-1] == ("final_sale", True)



# Check that a sentence which passes on its own stays a pass under a model that agrees, says nothing, gives no facts or denies a final sale,
# and that a model final sale is the one model fact that declines it
def test_model_changes_a_pass_only_towards_a_decline(example_message):
    event = build_event(example_message, (DAYS_30_SENTENCE,))
    result_on_the_text_alone = check_purchase(event, build_policy())
    model_without_facts = ModelExtraction(
        facts = None,
        call = ModelCallRecord(purpose = "fact_extraction", status = "timeout", provider = "test", model = "test-model", elapsed_ms = 2000.0),
    )
    assert result_on_the_text_alone.verdict == GuardVerdict.PASS
    assert check_purchase(event, build_policy(), build_model_extraction(return_days = 30)) == result_on_the_text_alone
    assert check_purchase(event, build_policy(), build_model_extraction()) == result_on_the_text_alone
    assert check_purchase(event, build_policy(), build_model_extraction(final_sale = False)) == result_on_the_text_alone
    assert check_purchase(event, build_policy(), model_without_facts) == result_on_the_text_alone
    assert check_purchase(event, build_policy(), build_model_extraction(return_days = 30, final_sale = True)).verdict == GuardVerdict.DECLINE



# Check that a model never loosens a verdict. Seven days in the sentence stay a decline, a final sale stays one, two return periods and a policy
# not stated stay uncertain, and the flag alone still never passes.
def test_model_never_loosens_a_verdict(example_message):
    generous_model = build_model_extraction(return_days = 30, final_sale = False)
    assert check_purchase(build_event(example_message, (DAYS_7_SENTENCE,)), build_policy(), generous_model).verdict == GuardVerdict.DECLINE
    assert check_purchase(build_event(example_message, (FINAL_SALE_SENTENCE,)), build_policy(), generous_model).verdict == GuardVerdict.DECLINE
    assert check_purchase(build_event(example_message, (TWO_PERIODS_SENTENCE,)), build_policy(), generous_model).verdict == GuardVerdict.UNCERTAIN
    assert check_purchase(build_event(example_message, (NOT_STATED_SENTENCE,), "unknown"), build_policy(), generous_model).verdict == GuardVerdict.UNCERTAIN
    assert check_purchase(build_event(example_message, (DAYS_30_SENTENCE,), "false"), build_policy(), generous_model).verdict == GuardVerdict.DECLINE



# Check that a model which contradicts 30 days in the sentence makes the days missing, so the pass becomes uncertain
def test_model_fact_that_contradicts_the_sentence_makes_the_days_missing(example_message):
    guard_result = check_purchase(build_event(example_message, (DAYS_30_SENTENCE,)), build_policy(), build_model_extraction(return_days = 7))
    assert guard_result.verdict == GuardVerdict.UNCERTAIN
    assert guard_result.reason_code == ReasonCode.RETURN_TERMS_UNKNOWN









#### Step 5: Check the way from the instruction to the decision ####

# Check that the unknown case gives step_up, decline and approve under the three uncertainty policies, through the compiler and the whole pipeline
@pytest.mark.parametrize(
    "uncertainty_policy, expected_decision",
    [("ask", Decision.STEP_UP), ("decline", Decision.DECLINE), ("approve", Decision.APPROVE)],
)
def test_unknown_return_terms_follow_the_uncertainty_policy(example_message, uncertainty_policy, expected_decision):
    example_message["mandate"]["uncertainty_policy"] = uncertainty_policy
    event = build_event(example_message, (NOT_STATED_SENTENCE,), "unknown", instruction = SHOE_INSTRUCTION)
    policy = build_policy_from_mandate(event.mandate)
    decision_trace = decide(event, policy, build_empty_ledger_snapshot(event))
    assert policy.expectations.min_return_days == 14
    assert decision_trace.decision == expected_decision
    assert decision_trace.reason_codes == ([] if expected_decision == Decision.APPROVE else [ReasonCode.RETURN_TERMS_UNKNOWN])
    assert decision_trace.aggregation.uncertainty_policy_applied is True



# Check 30 days and 7 days end to end, where the right shoes are approved and the short return period declines
def test_return_days_end_to_end(example_message):
    long_event = build_event(example_message, (DAYS_30_SENTENCE,), instruction = SHOE_INSTRUCTION)
    short_event = build_event(example_message, (DAYS_7_SENTENCE,), instruction = SHOE_INSTRUCTION)
    long_trace = decide(long_event, build_policy_from_mandate(long_event.mandate), build_empty_ledger_snapshot(long_event))
    short_trace = decide(short_event, build_policy_from_mandate(short_event.mandate), build_empty_ledger_snapshot(short_event))
    assert long_trace.decision == Decision.APPROVE
    assert short_trace.decision == Decision.DECLINE
    assert short_trace.reason_codes == [ReasonCode.RETURN_TERMS_UNMET]
    assert short_trace.customer_message == "Declined. Returns are accepted within 7 days, and you asked for at least 14."









#### Step 6: Check that the guard and its reader never name an identifier ####

# Check the source text of the guard and its reader for the words that would tie a decision to a test case, to an identifier or to the free text of a shop
@pytest.mark.parametrize("source_path", CHECKED_SOURCE_PATHS, ids = [source_path.name for source_path in CHECKED_SOURCE_PATHS])
def test_source_names_no_identifier_and_no_shop_text(source_path):
    source_text = source_path.read_text(encoding = "utf-8").lower()
    assert [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text] == []
