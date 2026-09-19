# Script: test_policy_from_message.py
# Purpose: Check that the engine builds its policy from the mandate inside each purchase message, and that stored rules that repeat the instruction change no decision
# Author: Andrés Gallardo
# Date: September 2026

import pytest

import replay
from app.models.events import MandateRule
from app.policyc.compiler import build_policy_from_mandate









#### Step 1: Define the shared helpers and fixtures ####

# Name the amount of one purchase the way the rule format of the platform names it
ORDER_AMOUNT_RULE_FIELD = "authorization.billing_amount_chf"



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



# List every purchase whose outcome differs between two runs, purchase by purchase in delivery order.
# Each entry names the purchase and shows both outcomes.
def list_differing_purchases(results_without_rules, results_with_rules):
    assert len(results_without_rules) == len(results_with_rules), "The two runs decided a different number of purchases"
    return [
        {
            "purchase": result_without_rules["record"]["source_authorization_id"],
            "outcome_without_rules": read_outcome(result_without_rules),
            "outcome_with_rules": read_outcome(result_with_rules),
        }
        for result_without_rules, result_with_rules in zip(results_without_rules, results_with_rules)
        if read_outcome(result_without_rules) != read_outcome(result_with_rules)
    ]



# List the policies the engine built for the mandates of one run, in the order in which the mandates first appear
def list_policies_of_run(purchase_results):
    mandate_ids_of_the_run = list(dict.fromkeys(purchase_result["message"]["mandate"]["mandate_id"] for purchase_result in purchase_results))
    return [replay.POLICIES_BY_MANDATE_ID[mandate_id] for mandate_id in mandate_ids_of_the_run]









#### Step 2: Write the compiled limits back as rules ####

# Write the limit per order and the budget over a period of a compiled policy as rules in the rule format of the platform.
# A policy without a limit gives no rule for it.
def build_rules_from_compiled_limits(compiled_policy):
    expectations = compiled_policy.expectations
    order_limit_rules = ()
    if expectations.per_order_limit_chf is not None:
        order_limit_rules = (
            MandateRule(
                field = ORDER_AMOUNT_RULE_FIELD,
                operator = "<=",
                value = float(expectations.per_order_limit_chf),
                currency = "CHF",
                scope = "purchase",
            ),
        )
    period_budget_rules = ()
    if expectations.period_limit_chf is not None:
        period_budget_rules = (
            MandateRule(
                field = ORDER_AMOUNT_RULE_FIELD,
                operator = "<=",
                value = float(expectations.period_limit_chf),
                currency = "CHF",
                scope = "period",
                period_days = expectations.period_days,
            ),
        )
    return order_limit_rules + period_budget_rules



# Replace the mandate of the event with one that carries the compiled limits as rules, then let the engine decide.
# The event cannot be changed, so a changed copy is built, and the earlier purchases of the run are handed on as they are.
def decide_with_limits_written_as_rules(event, earlier_purchases):
    compiled_policy = build_policy_from_mandate(event.mandate)
    mandate_with_rules = event.mandate.model_copy(update = {"hard_rules": build_rules_from_compiled_limits(compiled_policy)})
    event_with_rules = event.model_copy(update = {"mandate": mandate_with_rules})
    return replay.decide_with_engine(event_with_rules, earlier_purchases)









#### Step 3: Check the decisions with the rules in the mandate ####

# Check that rules that repeat the instruction leave every decision and every reason code as it was
def test_rules_that_repeat_the_instruction_change_no_decision(replay_tables, results_without_rules):
    results_with_rules = replay.replay_scenarios(replay_tables, replay.list_scenario_ids(replay_tables), decide_with_limits_written_as_rules)

    # Expect that the published run decided without any rule
    rule_counts_without_rules = [len(policy.hard_rules) for policy in list_policies_of_run(results_without_rules)]
    assert rule_counts_without_rules == [0] * len(rule_counts_without_rules)



    # Expect that the rules really reached the engine, a limit per order in every mandate and a budget over seven days in at least one
    policies_with_rules = list_policies_of_run(results_with_rules)
    rule_scopes_per_policy = [[rule.scope for rule in policy.hard_rules] for policy in policies_with_rules]
    period_days_of_the_budget_rules = {rule.period_days for policy in policies_with_rules for rule in policy.hard_rules if rule.scope == "period"}
    assert all("purchase" in rule_scopes for rule_scopes in rule_scopes_per_policy)
    assert period_days_of_the_budget_rules == {7}



    # Expect that the rules repeat the limits the engine read from the instruction alone
    limits_without_rules = [
        (policy.expectations.per_order_limit_chf, policy.expectations.period_limit_chf, policy.expectations.period_days)
        for policy in list_policies_of_run(results_without_rules)
    ]
    limits_with_rules = [
        (policy.expectations.per_order_limit_chf, policy.expectations.period_limit_chf, policy.expectations.period_days)
        for policy in policies_with_rules
    ]
    assert limits_with_rules == limits_without_rules



    # Expect the same outcome on every purchase
    assert list_differing_purchases(results_without_rules, results_with_rules) == []
