# Script: test_determinism.py
# Purpose: Check that the engine gives the same answers when the same purchases run again, and that no state leaks from one run into the next
# Author: Andrés Gallardo
# Date: September 2026

import pytest

import replay









#### Step 1: Define the shared helpers and fixtures ####

# Load the published tables once for all tests
@pytest.fixture(scope = "module")
def replay_tables():
    return replay.load_replay_tables()



# Replay all scenarios once in ascending order, with the engine
@pytest.fixture(scope = "module")
def ascending_results(replay_tables):
    return replay.replay_scenarios(replay_tables, replay.list_scenario_ids(replay_tables), replay.choose_decision_function())



# Read the decision, the reason codes and the customer message of one purchase
def read_outcome(purchase_result):
    return {
        "decision": purchase_result["record"]["decision"],
        "reason_codes": list(purchase_result["record"]["reason_codes"]),
        "customer_message": purchase_result["record"]["customer_message"],
    }



# Index the outcomes of a run by the identifier of their purchase, so two runs in a different order can be compared
def index_outcomes_by_purchase(purchase_results):
    return {
        purchase_result["record"]["source_authorization_id"]: read_outcome(purchase_result)
        for purchase_result in purchase_results
    }



# List every purchase whose outcome differs between two runs, in the delivery order of the first run.
# Each entry names the purchase and shows both outcomes.
def list_differing_purchases(first_results, second_results):
    first_outcomes = index_outcomes_by_purchase(first_results)
    second_outcomes = index_outcomes_by_purchase(second_results)
    assert set(first_outcomes) == set(second_outcomes), "The two runs decided different purchases"
    return [
        {
            "purchase": source_authorization_id,
            "first_outcome": first_outcome,
            "second_outcome": second_outcomes[source_authorization_id],
        }
        for source_authorization_id, first_outcome in first_outcomes.items()
        if first_outcome != second_outcomes[source_authorization_id]
    ]









#### Step 2: Check the same run twice ####

# Check that a second run in the same order gives the same decisions, reason codes and customer messages
def test_same_run_twice_gives_the_same_answers(replay_tables, ascending_results):
    second_results = replay.replay_scenarios(replay_tables, replay.list_scenario_ids(replay_tables), replay.choose_decision_function())

    # Expect two separate runs, which share no live purchase identifier
    first_live_ids = {purchase_result["record"]["authorization_id"] for purchase_result in ascending_results}
    second_live_ids = {purchase_result["record"]["authorization_id"] for purchase_result in second_results}
    assert first_live_ids.isdisjoint(second_live_ids)



    # Expect the same outcome on every purchase
    assert list_differing_purchases(ascending_results, second_results) == []









#### Step 3: Check the scenarios in descending order ####

# Check that every scenario gives the same answers when the scenarios run from the last to the first, so no run leaves anything behind for the next
def test_descending_scenario_order_gives_the_same_answers(replay_tables, ascending_results):
    descending_scenario_ids = sorted(replay.list_scenario_ids(replay_tables), reverse = True)
    descending_results = replay.replay_scenarios(replay_tables, descending_scenario_ids, replay.choose_decision_function())

    # Expect that the scenarios really ran in the opposite order
    scenario_order_of_the_run = list(dict.fromkeys(purchase_result["record"]["scenario_id"] for purchase_result in descending_results))
    assert scenario_order_of_the_run == descending_scenario_ids
    assert scenario_order_of_the_run != replay.list_scenario_ids(replay_tables)



    # Expect the same outcome on every purchase
    assert list_differing_purchases(ascending_results, descending_results) == []
