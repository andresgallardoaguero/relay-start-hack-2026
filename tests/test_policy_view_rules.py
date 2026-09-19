# Script: test_policy_view_rules.py
# Purpose: Check that the policy is written into the rule format of the platform without changing what the instruction alone gives, and that the check list names the whole policy
# Author: Andrés Gallardo
# Date: September 2026

from decimal import Decimal
from types import SimpleNamespace

import pytest

import replay
from app.models.policy import Expectations
from app.policyc.compiler import build_policy_from_mandate
from app.webapi.policy_view import MandateContent, build_check_list, build_hard_rules_from_policy









#### Step 1: State the instructions and the rules they give ####

# Read the five public instructions from the published catalogue, by their scenario
SCENARIO_CATALOGUE = replay.read_case_table("scenario_catalogue.csv", replay.CASE_DATA_FOLDER)
PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID = dict(zip(SCENARIO_CATALOGUE["scenario_id"], SCENARIO_CATALOGUE["cardholder_instruction"]))
assert len(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID) == 5, "The published catalogue must hold five instructions"



# Name the fields of a rule the way the rule format of the platform names the facts of a purchase message
ORDER_AMOUNT_RULE_FIELD = "authorization.billing_amount_chf"
ITEM_CATEGORY_RULE_FIELD = "authorization.items.item_category"
MERCHANT_CATEGORY_RULE_FIELD = "authorization.merchant.merchant_category"



# State the exact rules each public instruction gives, in the order in which they are written
EXPECTED_PUBLIC_RULES_BY_SCENARIO_ID = {
    "SCEN0000": [
        {"field": ORDER_AMOUNT_RULE_FIELD, "operator": "<=", "value": 20.0, "currency": "CHF", "scope": "purchase"},
        {"field": ITEM_CATEGORY_RULE_FIELD, "operator": "in", "value": ["groceries"]},
    ],
    "SCEN0001": [
        {"field": ORDER_AMOUNT_RULE_FIELD, "operator": "<=", "value": 120.0, "currency": "CHF", "scope": "purchase"},
        {"field": ORDER_AMOUNT_RULE_FIELD, "operator": "<=", "value": 300.0, "currency": "CHF", "scope": "period", "period_days": 7},
        {"field": ITEM_CATEGORY_RULE_FIELD, "operator": "in", "value": ["groceries", "household"]},
    ],
    "SCEN0002": [
        {"field": ORDER_AMOUNT_RULE_FIELD, "operator": "<=", "value": 200.0, "currency": "CHF", "scope": "purchase"},
        {"field": ITEM_CATEGORY_RULE_FIELD, "operator": "in", "value": ["sporting_goods"]},
        {"field": MERCHANT_CATEGORY_RULE_FIELD, "operator": "in", "value": ["sporting_goods"]},
    ],
    "SCEN0003": [
        {"field": ORDER_AMOUNT_RULE_FIELD, "operator": "<=", "value": 250.0, "currency": "CHF", "scope": "purchase"},
        {"field": ITEM_CATEGORY_RULE_FIELD, "operator": "in", "value": ["clothing"]},
    ],
    "SCEN0004": [
        {"field": ORDER_AMOUNT_RULE_FIELD, "operator": "<=", "value": 400.0, "currency": "CHF", "scope": "purchase"},
        {"field": ITEM_CATEGORY_RULE_FIELD, "operator": "in", "value": ["electronics"]},
    ],
}



# State instructions outside the public data, each for one corner of the rule writing
LOOSE_SHOP_INSTRUCTION = "Buy a cycling helmet from a sports shop for CHF 150 or less."
BUDGET_WITHOUT_DAYS_INSTRUCTION = "Buy paint and tools for the renovation, at most CHF 900 in total."
RULED_OUT_GOODS_INSTRUCTION = "Order groceries for up to CHF 150 per order, but never gift cards or cosmetics."
EXCLUSIVE_LIMIT_INSTRUCTION = "Buy books for under CHF 60 per order."
STRICT_SHOP_WITH_BUDGET_INSTRUCTION = "Get our weekly groceries only from a supermarket, at most CHF 180 per order and no more than CHF 500 per month."
EXCLUSIVE_BUDGET_INSTRUCTION = "Keep takeaway spending below CHF 200 within 10 days."
PREFERRED_FAMILIARITY_INSTRUCTION = "Buy a rain coat, preferably from a shop I have used before, for CHF 180 or less, returnable within 30 days."
NO_LIMIT_INSTRUCTION = "Renew my music plan."
OUTSIDE_INSTRUCTIONS = (
    LOOSE_SHOP_INSTRUCTION,
    BUDGET_WITHOUT_DAYS_INSTRUCTION,
    RULED_OUT_GOODS_INSTRUCTION,
    EXCLUSIVE_LIMIT_INSTRUCTION,
    STRICT_SHOP_WITH_BUDGET_INSTRUCTION,
    EXCLUSIVE_BUDGET_INSTRUCTION,
    PREFERRED_FAMILIARITY_INSTRUCTION,
    NO_LIMIT_INSTRUCTION,
)
ALL_INSTRUCTIONS = tuple(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID.values()) + OUTSIDE_INSTRUCTIONS



# Name the two checks that appear only when the policy carries the fields of the repeated order and of the thing already bought
LABELS_OF_FIELDS_A_POLICY_MAY_NOT_CARRY = ("Repeated order", "Already bought")
LABELS_OF_THE_CHECKS_THAT_ALWAYS_CLOSE_THE_LIST = ["When uncertain", "Shop text is data", "One purchase, one decision"]









#### Step 2: Define the shared helpers ####

# Compile an instruction together with the given rules, the way the interface and the engine do
def compile_policy(instruction, hard_rules = ()):
    return build_policy_from_mandate(MandateContent(instruction, hard_rules, "ask"))



# Write the rules of an instruction that carries none
def write_rules(instruction):
    return build_hard_rules_from_policy(compile_policy(instruction))



# List every expectation that differs between two policies, each with its field and both values
def list_differing_expectations(policy_from_instruction, policy_with_rules):
    return [
        {
            "field": field_name,
            "from_instruction_alone": getattr(policy_from_instruction.expectations, field_name),
            "with_written_rules": getattr(policy_with_rules.expectations, field_name),
        }
        for field_name in Expectations.model_fields
        if getattr(policy_from_instruction.expectations, field_name) != getattr(policy_with_rules.expectations, field_name)
    ]



# Read the labels of a check list, without the two checks of fields a policy may not carry
def read_stated_labels(checks):
    return [check["label"] for check in checks if check["label"] not in LABELS_OF_FIELDS_A_POLICY_MAY_NOT_CARRY]



# Find the one check with the given label, or None
def find_check(checks, label):
    return next((check for check in checks if check["label"] == label), None)



# Name the check that describes a written rule, by the field, the operator and the scope of the rule
def read_label_of_rule(rule):
    if rule["field"] == ORDER_AMOUNT_RULE_FIELD:
        return "Budget over a period" if rule.get("scope") == "period" else "Limit per order"
    if rule["field"] == ITEM_CATEGORY_RULE_FIELD:
        return "Goods covered" if rule["operator"] == "in" else "Goods ruled out"
    return "Kind of shop"



# Report whether a value is one the platform accepts, which is a number, a text or a list of texts, and never a boolean or nothing
def value_is_accepted_by_the_platform(value):
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float, str)):
        return True
    return isinstance(value, list) and len(value) > 0 and all(isinstance(entry, str) for entry in value)









#### Step 3: Check the rules the public instructions give ####

# Check the exact list of rules of each public instruction
@pytest.mark.parametrize("scenario_id", sorted(EXPECTED_PUBLIC_RULES_BY_SCENARIO_ID))
def test_public_instruction_gives_the_expected_rules(scenario_id):
    assert write_rules(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID[scenario_id]) == EXPECTED_PUBLIC_RULES_BY_SCENARIO_ID[scenario_id]



# Check that every published scenario has its expected rules stated here
def test_every_public_instruction_is_covered():
    assert sorted(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID) == sorted(EXPECTED_PUBLIC_RULES_BY_SCENARIO_ID)









#### Step 4: Check the round trip ####

# Check that the policy compiled from the instruction and the written rules expects the same as the policy from the instruction alone
@pytest.mark.parametrize("instruction", ALL_INSTRUCTIONS)
def test_written_rules_leave_every_expectation_as_it_was(instruction):
    policy_from_instruction = compile_policy(instruction)
    policy_with_rules = compile_policy(instruction, build_hard_rules_from_policy(policy_from_instruction))
    assert list_differing_expectations(policy_from_instruction, policy_with_rules) == []



# Check that the written rules add no question for the customer
@pytest.mark.parametrize("instruction", ALL_INSTRUCTIONS)
def test_written_rules_add_no_open_question(instruction):
    policy_from_instruction = compile_policy(instruction)
    policy_with_rules = compile_policy(instruction, build_hard_rules_from_policy(policy_from_instruction))
    assert policy_with_rules.open_questions == policy_from_instruction.open_questions



# Check that the outside instructions really reach the corners they were written for, so the round trip is no empty proof
def test_outside_instructions_reach_their_corners():
    loose_shop_expectations = compile_policy(LOOSE_SHOP_INSTRUCTION).expectations
    assert len(loose_shop_expectations.required_merchant_categories) > 0 and not loose_shop_expectations.merchant_category_is_strict
    budget_without_days_expectations = compile_policy(BUDGET_WITHOUT_DAYS_INSTRUCTION).expectations
    assert budget_without_days_expectations.period_limit_reading == "read" and budget_without_days_expectations.period_days is None
    assert len(compile_policy(RULED_OUT_GOODS_INSTRUCTION).expectations.prohibited_item_categories) > 0
    exclusive_limit_expectations = compile_policy(EXCLUSIVE_LIMIT_INSTRUCTION).expectations
    assert exclusive_limit_expectations.per_order_limit_reading == "read" and not exclusive_limit_expectations.per_order_limit_inclusive
    exclusive_budget_expectations = compile_policy(EXCLUSIVE_BUDGET_INSTRUCTION).expectations
    assert exclusive_budget_expectations.period_limit_reading == "read" and not exclusive_budget_expectations.period_limit_inclusive
    assert compile_policy(NO_LIMIT_INSTRUCTION).expectations.per_order_limit_reading == "not_stated"









#### Step 5: Check the decisions with the written rules in the mandate ####

# Load the published tables once for all tests
@pytest.fixture(scope = "module")
def replay_tables():
    return replay.load_replay_tables()



# Replay all scenarios once as published, where every mandate carries the instruction and no rules
@pytest.fixture(scope = "module")
def results_without_rules(replay_tables):
    return replay.replay_scenarios(replay_tables, replay.list_scenario_ids(replay_tables), replay.choose_decision_function())



# Read the decision and the reason codes of one purchase
def read_outcome(purchase_result):
    return {
        "decision": purchase_result["record"]["decision"],
        "reason_codes": list(purchase_result["record"]["reason_codes"]),
    }



# List the policies the engine built for the mandates of one run, in the order in which the mandates first appear
def list_policies_of_run(purchase_results):
    mandate_ids_of_the_run = list(dict.fromkeys(purchase_result["message"]["mandate"]["mandate_id"] for purchase_result in purchase_results))
    return [replay.POLICIES_BY_MANDATE_ID[mandate_id] for mandate_id in mandate_ids_of_the_run]



# Replace the mandate of the event with one that carries the whole policy written as rules, then let the engine decide.
# The event cannot be changed, so a changed copy is built, and the earlier purchases of the run are handed on as they are.
def decide_with_policy_written_as_rules(event, earlier_purchases):
    compiled_policy = build_policy_from_mandate(event.mandate)
    written_rules = MandateContent(event.mandate.instruction, build_hard_rules_from_policy(compiled_policy), event.mandate.uncertainty_policy).hard_rules
    mandate_with_rules = event.mandate.model_copy(update = {"hard_rules": written_rules})
    event_with_rules = event.model_copy(update = {"mandate": mandate_with_rules})
    return replay.decide_with_engine(event_with_rules, earlier_purchases)



# Check that the written rules leave the decision and the reason codes of every public purchase as they were
def test_written_rules_change_no_public_decision(replay_tables, results_without_rules):
    results_with_rules = replay.replay_scenarios(replay_tables, replay.list_scenario_ids(replay_tables), decide_with_policy_written_as_rules)
    assert len(results_without_rules) == replay.EXPECTED_ATTEMPT_COUNT
    assert len(results_with_rules) == len(results_without_rules)



    # Expect that the published run decided without any rule
    rule_counts_without_rules = [len(policy.hard_rules) for policy in list_policies_of_run(results_without_rules)]
    assert rule_counts_without_rules == [0] * len(rule_counts_without_rules)



    # Expect that the rules really reached the engine, at least two in every mandate and among them every kind of rule the public instructions give
    policies_with_rules = list_policies_of_run(results_with_rules)
    assert all(len(policy.hard_rules) >= 2 for policy in policies_with_rules)
    rule_kinds_of_the_run = {(rule.field, rule.operator, rule.scope) for policy in policies_with_rules for rule in policy.hard_rules}
    assert rule_kinds_of_the_run == {
        (ORDER_AMOUNT_RULE_FIELD, "<=", "purchase"),
        (ORDER_AMOUNT_RULE_FIELD, "<=", "period"),
        (ITEM_CATEGORY_RULE_FIELD, "in", None),
        (MERCHANT_CATEGORY_RULE_FIELD, "in", None),
    }



    # Expect the same outcome on every purchase, and show both outcomes of each purchase that differs
    differing_purchases = [
        {
            "purchase": result_without_rules["record"]["source_authorization_id"],
            "outcome_without_rules": read_outcome(result_without_rules),
            "outcome_with_rules": read_outcome(result_with_rules),
        }
        for result_without_rules, result_with_rules in zip(results_without_rules, results_with_rules)
        if read_outcome(result_without_rules) != read_outcome(result_with_rules)
    ]
    assert differing_purchases == []









#### Step 6: Check the form of the written rules ####

# Check that a kind of shop the instruction merely names writes no rule, and that a kind of shop that is a condition writes one
def test_only_a_strict_kind_of_shop_is_written_as_a_rule():
    loose_rule_fields = [rule["field"] for rule in write_rules(LOOSE_SHOP_INSTRUCTION)]
    strict_rule_fields = [rule["field"] for rule in write_rules(STRICT_SHOP_WITH_BUDGET_INSTRUCTION)]
    assert MERCHANT_CATEGORY_RULE_FIELD not in loose_rule_fields
    assert strict_rule_fields.count(MERCHANT_CATEGORY_RULE_FIELD) == 1



# Check that every value is a number, a text or a list of texts
@pytest.mark.parametrize("instruction", ALL_INSTRUCTIONS)
def test_every_rule_value_is_accepted_by_the_platform(instruction):
    assert all(value_is_accepted_by_the_platform(rule["value"]) for rule in write_rules(instruction))



# Check that no rule carries an optional field it does not use, neither as an empty value nor on a rule the field does not belong to
@pytest.mark.parametrize("instruction", ALL_INSTRUCTIONS)
def test_no_rule_carries_an_unused_optional_field(instruction):
    written_rules = write_rules(instruction)
    assert all(rule_value is not None for rule in written_rules for rule_value in rule.values())
    category_rules = [rule for rule in written_rules if rule["field"] != ORDER_AMOUNT_RULE_FIELD]
    order_limit_rules = [rule for rule in written_rules if rule.get("scope") == "purchase"]
    assert all(sorted(rule) == ["field", "operator", "value"] for rule in category_rules)
    assert all(sorted(rule) == ["currency", "field", "operator", "scope", "value"] for rule in order_limit_rules)



# Check that a budget over a number of days names the days, and that a budget over the whole instruction names none
def test_budget_rule_names_the_days_only_when_the_budget_has_them():
    budget_with_days = [rule for rule in write_rules(STRICT_SHOP_WITH_BUDGET_INSTRUCTION) if rule.get("scope") == "period"]
    budget_without_days = [rule for rule in write_rules(BUDGET_WITHOUT_DAYS_INSTRUCTION) if rule.get("scope") == "period"]
    assert [sorted(rule) for rule in budget_with_days] == [["currency", "field", "operator", "period_days", "scope", "value"]]
    assert [sorted(rule) for rule in budget_without_days] == [["currency", "field", "operator", "scope", "value"]]



# Check that an exclusive limit and an exclusive budget keep their operator
def test_exclusive_amounts_are_written_with_the_strict_operator():
    limit_operators = [rule["operator"] for rule in write_rules(EXCLUSIVE_LIMIT_INSTRUCTION) if rule.get("scope") == "purchase"]
    budget_operators = [rule["operator"] for rule in write_rules(EXCLUSIVE_BUDGET_INSTRUCTION) if rule.get("scope") == "period"]
    assert limit_operators == ["<"]
    assert budget_operators == ["<"]



# Check that the written rules fit the published schema of a purchase message
@pytest.mark.parametrize("instruction", ALL_INSTRUCTIONS)
def test_written_rules_fit_the_published_schema(instruction, example_message, event_schema_validator):
    example_message["mandate"]["hard_rules"] = write_rules(instruction)
    assert [error.message for error in event_schema_validator.iter_errors(example_message)] == []



# Check that writing the rules of a policy that already carries them gives the same list, with no rule twice
@pytest.mark.parametrize("instruction", ALL_INSTRUCTIONS)
def test_writing_the_rules_twice_gives_the_same_list(instruction):
    rules_written_once = write_rules(instruction)
    rules_written_twice = build_hard_rules_from_policy(compile_policy(instruction, rules_written_once))
    assert rules_written_twice == rules_written_once
    assert all(rules_written_once.count(rule) == 1 for rule in rules_written_once)



# Check that the rules the mandate already carries stay first and unchanged, and that a carried limit is not written a second time
def test_carried_rules_stay_first_and_unchanged():
    carried_rules = [
        {"field": "item.size", "operator": "=", "value": "43", "scope": "purchase"},
        {"field": ORDER_AMOUNT_RULE_FIELD, "operator": "<=", "value": 100.0, "currency": "CHF", "scope": "purchase"},
    ]
    written_rules = build_hard_rules_from_policy(compile_policy(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID["SCEN0001"], carried_rules))
    assert written_rules[:2] == carried_rules
    assert len([rule for rule in written_rules if rule.get("scope") == "purchase" and rule["field"] == ORDER_AMOUNT_RULE_FIELD]) == 1
    assert written_rules[2:] == EXPECTED_PUBLIC_RULES_BY_SCENARIO_ID["SCEN0001"][1:]









#### Step 7: Check the check list ####

# State the labels each public instruction gives, in order and without the three checks that always close the list
EXPECTED_PUBLIC_LABELS_BY_SCENARIO_ID = {
    "SCEN0000": ["Limit per order", "Goods covered", "Requested thing", "Familiarity of the shop"],
    "SCEN0001": ["Limit per order", "Budget over a period", "Goods covered"],
    "SCEN0002": ["Limit per order", "Goods covered", "Kind of shop", "Requested thing", "Returns"],
    "SCEN0003": ["Limit per order", "Goods covered", "Familiarity of the shop", "Watch over the session"],
    "SCEN0004": ["Limit per order", "Goods covered", "Extras", "Requested thing", "Familiarity of the shop"],
}



# Check that the check list names every stated part of the policy in the fixed order and leaves out what the instruction does not state
@pytest.mark.parametrize("scenario_id", sorted(EXPECTED_PUBLIC_LABELS_BY_SCENARIO_ID))
def test_check_list_names_every_stated_part(scenario_id):
    checks = build_check_list(compile_policy(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID[scenario_id]))
    assert read_stated_labels(checks) == EXPECTED_PUBLIC_LABELS_BY_SCENARIO_ID[scenario_id] + LABELS_OF_THE_CHECKS_THAT_ALWAYS_CLOSE_THE_LIST



# Check that a check is marked as stored exactly when its value is written as a rule, and that the uncertainty policy is marked as well
@pytest.mark.parametrize("instruction", ALL_INSTRUCTIONS)
def test_stored_checks_are_exactly_the_written_rules(instruction):
    policy = compile_policy(instruction)
    stored_labels = sorted(check["label"] for check in build_check_list(policy) if check["stored_with_platform"])
    labels_of_the_rules = sorted(read_label_of_rule(rule) for rule in build_hard_rules_from_policy(policy))
    assert stored_labels == sorted(labels_of_the_rules + ["When uncertain"])



# Check the words of the single checks, which are the days of the budget, the size, the return days and the bar of familiarity
def test_checks_state_the_values_of_the_policy():
    household_checks = build_check_list(compile_policy(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID["SCEN0001"]))
    shoe_checks = build_check_list(compile_policy(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID["SCEN0002"]))
    grocery_checks = build_check_list(compile_policy(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID["SCEN0000"]))
    clothing_checks = build_check_list(compile_policy(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID["SCEN0003"]))
    assert "CHF 120.00" in find_check(household_checks, "Limit per order")["detail"]
    assert "CHF 300.00" in find_check(household_checks, "Budget over a period")["detail"] and "7 days" in find_check(household_checks, "Budget over a period")["detail"]
    assert "groceries, household" in find_check(household_checks, "Goods covered")["detail"]
    assert find_check(shoe_checks, "Kind of shop")["detail"].startswith("Only shops for sporting goods")
    assert "size 43" in find_check(shoe_checks, "Requested thing")["detail"]
    assert "at least 14 days" in find_check(shoe_checks, "Returns")["detail"]
    assert "used regularly" in find_check(grocery_checks, "Familiarity of the shop")["detail"] and "condition" in find_check(grocery_checks, "Familiarity of the shop")["detail"]
    assert "used before" in find_check(clothing_checks, "Familiarity of the shop")["detail"]



# Check the checks of the corners, which are a loose kind of shop, a budget without days, ruled-out goods and a familiar shop as a wish
def test_checks_of_the_outside_instructions():
    loose_shop_check = find_check(build_check_list(compile_policy(LOOSE_SHOP_INSTRUCTION)), "Kind of shop")
    assert loose_shop_check["detail"].startswith("Preferably") and "asks you" in loose_shop_check["detail"]
    assert loose_shop_check["stored_with_platform"] is False
    budget_check = find_check(build_check_list(compile_policy(BUDGET_WITHOUT_DAYS_INSTRUCTION)), "Budget over a period")
    assert "under this instruction" in budget_check["detail"] and "days" not in budget_check["detail"]
    ruled_out_check = find_check(build_check_list(compile_policy(RULED_OUT_GOODS_INSTRUCTION)), "Goods ruled out")
    assert "cosmetics, gift card" in ruled_out_check["detail"] and ruled_out_check["stored_with_platform"] is True
    familiarity_check = find_check(build_check_list(compile_policy(PREFERRED_FAMILIARITY_INSTRUCTION)), "Familiarity of the shop")
    assert "used before" in familiarity_check["detail"] and "wish" in familiarity_check["detail"]



# Check that an instruction without a limit still shows the limit per order, as not stored, and nothing it does not state
def test_instruction_without_a_limit_still_shows_the_limit_check():
    checks = build_check_list(compile_policy(NO_LIMIT_INSTRUCTION))
    assert read_stated_labels(checks) == ["Limit per order", "Goods covered"] + LABELS_OF_THE_CHECKS_THAT_ALWAYS_CLOSE_THE_LIST
    assert find_check(checks, "Limit per order")["stored_with_platform"] is False



# Check that a policy that carries the fields of the repeated order and of the thing already bought shows both checks before the uncertainty policy.
# The stand-in carries every expectation of a compiled policy and the three fields on top, so the check works whether or not the policy model has them.
def test_checks_of_fields_a_policy_may_not_carry():
    compiled_policy = compile_policy(PUBLIC_INSTRUCTIONS_BY_SCENARIO_ID["SCEN0004"])
    expectations_with_the_fields = SimpleNamespace(**{
        **{field_name: getattr(compiled_policy.expectations, field_name) for field_name in Expectations.model_fields},
        "duplicate_window_hours": 48,
        "duplicate_amount_share": Decimal("0.10"),
        "goal_fulfilled_action": "note",
    })
    policy_with_the_fields = SimpleNamespace(expectations = expectations_with_the_fields, uncertainty_policy = "ask")
    labels = [check["label"] for check in build_check_list(policy_with_the_fields)]
    assert labels[-5:] == ["Repeated order", "Already bought"] + LABELS_OF_THE_CHECKS_THAT_ALWAYS_CLOSE_THE_LIST
    repeated_order_check = find_check(build_check_list(policy_with_the_fields), "Repeated order")
    assert "48 hours" in repeated_order_check["detail"] and "10 percent" in repeated_order_check["detail"]
    assert repeated_order_check["stored_with_platform"] is False
