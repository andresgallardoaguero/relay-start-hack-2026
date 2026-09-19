# Script: test_no_hardcoding.py
# Purpose: Check that the engine gives the same decisions when every purchase identifier and every scenario identifier is replaced
# Author: Andrés Gallardo
# Date: September 2026

import random
import re

import pytest

import replay









#### Step 1: Define the shared helpers and fixtures ####

# State the shape the message format requires of a scenario identifier
SCENARIO_IDENTIFIER_PATTERN = re.compile(r"^SCEN[0-9]{4}$")



# State the two seeds of the random identifiers, so a failing run can be repeated exactly
SCRAMBLE_SEEDS = (20260918, 7)



# Load the published tables once for all tests
@pytest.fixture(scope = "module")
def replay_tables():
    return replay.load_replay_tables()



# Replay all scenarios once as published, with the engine
@pytest.fixture(scope = "module")
def published_results(replay_tables):
    return replay.replay_scenarios(replay_tables, replay.list_scenario_ids(replay_tables), replay.choose_decision_function())



# Read the decision and the reason codes of one purchase
def read_outcome(purchase_result):
    return {
        "decision": purchase_result["record"]["decision"],
        "reason_codes": list(purchase_result["record"]["reason_codes"]),
    }



# List every purchase whose outcome differs between two runs, purchase by purchase in delivery order.
# Each entry names the purchase by its published identifier and shows both outcomes.
def list_differing_purchases(published_results, changed_results):
    assert len(published_results) == len(changed_results), "The two runs decided a different number of purchases"
    return [
        {
            "published_purchase": published_result["record"]["source_authorization_id"],
            "published_outcome": read_outcome(published_result),
            "changed_outcome": read_outcome(changed_result),
        }
        for published_result, changed_result in zip(published_results, changed_results)
        if read_outcome(published_result) != read_outcome(changed_result)
    ]









#### Step 2: Build tables with replaced identifiers ####

# Draw one new purchase identifier per published purchase, all different from each other and from every published one
def draw_purchase_identifiers(published_purchase_ids, random_generator):
    drawn_numbers = random_generator.sample(range(10 ** 8), len(published_purchase_ids))
    new_id_by_published_id = {
        published_purchase_id: "PURCHASE-" + format(drawn_number, "08d")
        for published_purchase_id, drawn_number in zip(published_purchase_ids, drawn_numbers)
    }
    assert set(new_id_by_published_id.values()).isdisjoint(published_purchase_ids), "A drawn purchase identifier repeats a published one"
    return new_id_by_published_id



# Draw one new scenario identifier per published scenario, in the required shape and never one of the published ones
def draw_scenario_identifiers(published_scenario_ids, random_generator):
    free_identifiers = [
        "SCEN" + format(scenario_number, "04d")
        for scenario_number in range(10 ** 4)
        if "SCEN" + format(scenario_number, "04d") not in published_scenario_ids
    ]
    drawn_identifiers = random_generator.sample(free_identifiers, len(published_scenario_ids))
    return dict(zip(published_scenario_ids, drawn_identifiers))



# Replace the purchase identifiers and the scenario identifiers consistently, in the purchases, in the pointer to a related purchase,
# in the keys of the cart lines and inside every cart line.
# The identifiers of shops, cards, customers and items stay, because they are facts of the purchase, and so does the delivery position.
def replace_identifiers(replay_tables, new_purchase_id_by_published_id, new_scenario_id_by_published_id):

    # Keep an empty pointer to a related purchase empty
    new_related_id_by_published_id = {**new_purchase_id_by_published_id, "": ""}



    # Replace the identifiers in the purchases
    changed_attempts = (
        replay_tables.joined_attempts
        .assign(authorization_id = lambda table: table["authorization_id"].map(new_purchase_id_by_published_id))
        .assign(related_authorization_id = lambda table: table["related_authorization_id"].map(new_related_id_by_published_id))
        .assign(scenario_id = lambda table: table["scenario_id"].map(new_scenario_id_by_published_id))
    )
    assert changed_attempts["authorization_id"].notna().all(), "A purchase received no new identifier"
    assert changed_attempts["related_authorization_id"].notna().all(), "A related purchase received no new identifier"
    assert changed_attempts["scenario_id"].notna().all(), "A scenario received no new identifier"



    # Replace the identifiers in the keys of the cart lines and inside every cart line
    changed_cart_lines = {
        new_purchase_id_by_published_id[published_purchase_id]: [
            {**cart_line, "authorization_id": new_purchase_id_by_published_id[published_purchase_id]}
            for cart_line in cart_lines
        ]
        for published_purchase_id, cart_lines in replay_tables.cart_lines_by_source_id.items()
    }
    return replay.ReplayTables(
        joined_attempts = changed_attempts,
        cart_lines_by_source_id = changed_cart_lines,
    )









#### Step 3: Check the decisions under replaced identifiers ####

# Check that random purchase identifiers and random scenario identifiers leave every decision and every reason code as it was
@pytest.mark.parametrize("seed", SCRAMBLE_SEEDS)
def test_random_identifiers_give_the_same_decisions(replay_tables, published_results, seed):

    # Draw the new identifiers from the seed
    random_generator = random.Random(seed)
    published_scenario_ids = replay.list_scenario_ids(replay_tables)
    published_purchase_ids = replay_tables.joined_attempts["authorization_id"].tolist()
    new_purchase_id_by_published_id = draw_purchase_identifiers(published_purchase_ids, random_generator)
    new_scenario_id_by_published_id = draw_scenario_identifiers(published_scenario_ids, random_generator)



    # Replay the scenarios in the published order under their new names
    changed_tables = replace_identifiers(replay_tables, new_purchase_id_by_published_id, new_scenario_id_by_published_id)
    changed_scenario_ids = [new_scenario_id_by_published_id[scenario_id] for scenario_id in published_scenario_ids]
    changed_results = replay.replay_scenarios(changed_tables, changed_scenario_ids, replay.choose_decision_function())



    # Expect that no published identifier reached the engine
    purchase_ids_of_the_changed_run = {changed_result["message"]["authorization"]["source_authorization_id"] for changed_result in changed_results}
    scenario_ids_of_the_changed_run = {changed_result["message"]["authorization"]["scenario_id"] for changed_result in changed_results}
    assert purchase_ids_of_the_changed_run.isdisjoint(published_purchase_ids)
    assert scenario_ids_of_the_changed_run.isdisjoint(published_scenario_ids)
    assert all(SCENARIO_IDENTIFIER_PATTERN.match(scenario_id) for scenario_id in scenario_ids_of_the_changed_run)



    # Expect the same outcome on every purchase
    assert list_differing_purchases(published_results, changed_results) == []



# Check that the scenarios give the same decisions under each other's identifiers, where every scenario runs under the name of the next one
def test_swapped_scenario_identifiers_give_the_same_decisions(replay_tables, published_results):

    # Hand every scenario the identifier of the next one, and the last one the identifier of the first
    published_scenario_ids = replay.list_scenario_ids(replay_tables)
    swapped_scenario_id_by_published_id = dict(zip(published_scenario_ids, published_scenario_ids[1:] + published_scenario_ids[:1]))
    assert all(published_id != swapped_id for published_id, swapped_id in swapped_scenario_id_by_published_id.items()), "A scenario kept its own identifier"



    # Keep the purchase identifiers and replay the scenarios in the published order under their swapped names
    published_purchase_ids = replay_tables.joined_attempts["authorization_id"].tolist()
    same_purchase_id_by_published_id = dict(zip(published_purchase_ids, published_purchase_ids))
    changed_tables = replace_identifiers(replay_tables, same_purchase_id_by_published_id, swapped_scenario_id_by_published_id)
    changed_scenario_ids = [swapped_scenario_id_by_published_id[scenario_id] for scenario_id in published_scenario_ids]
    changed_results = replay.replay_scenarios(changed_tables, changed_scenario_ids, replay.choose_decision_function())



    # Expect that every purchase really ran under another scenario identifier
    purchases_under_their_own_scenario = [
        published_result["record"]["source_authorization_id"]
        for published_result, changed_result in zip(published_results, changed_results)
        if published_result["message"]["authorization"]["scenario_id"] == changed_result["message"]["authorization"]["scenario_id"]
    ]
    assert purchases_under_their_own_scenario == []



    # Expect the same outcome on every purchase
    assert list_differing_purchases(published_results, changed_results) == []
