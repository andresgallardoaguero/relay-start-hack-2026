# Script: test_time_shift.py
# Purpose: Check that the engine gives the same decisions when every purchase moves by the same whole number of weeks
# Author: Andrés Gallardo
# Date: September 2026

from datetime import date, datetime, timedelta, timezone

import pytest

import replay









#### Step 1: Define the shared helpers and fixtures ####

# State the shifts in whole weeks, so every purchase keeps its weekday and its hour
SHIFTS_IN_WEEKS = (-4, -1, 1, 4)



# State the Swiss summer time of 2026, which starts and ends at 01.00 UTC.
# Every shifted purchase has to stay inside it, so its hour in Swiss local time stays the same.
SUMMER_TIME_START = datetime(2026, 3, 29, 1, 0, tzinfo = timezone.utc)
SUMMER_TIME_END = datetime(2026, 10, 25, 1, 0, tzinfo = timezone.utc)



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
# Each entry names the purchase and shows both outcomes.
def list_differing_purchases(published_results, changed_results):
    assert len(published_results) == len(changed_results), "The two runs decided a different number of purchases"
    return [
        {
            "purchase": published_result["record"]["source_authorization_id"],
            "published_outcome": read_outcome(published_result),
            "shifted_outcome": read_outcome(changed_result),
        }
        for published_result, changed_result in zip(published_results, changed_results)
        if read_outcome(published_result) != read_outcome(changed_result)
    ]









#### Step 2: Build tables with shifted times ####

# Move one timestamp by a number of weeks and write it the way the published files write it
def shift_timestamp_text(timestamp_text, shift_in_weeks):
    shifted_time = replay.read_simulated_time(timestamp_text) + timedelta(weeks = shift_in_weeks)
    return replay.format_utc_time(shifted_time)



# Move one delivery date by a number of weeks, where an empty cell stays empty
def shift_delivery_date_text(delivery_date_text, shift_in_weeks):
    if delivery_date_text == "":
        return ""
    return (date.fromisoformat(delivery_date_text) + timedelta(weeks = shift_in_weeks)).isoformat()



# Move the timestamp and the delivery date of every purchase by the same number of weeks.
# The cart lines carry no time, so they stay as they are.
def shift_purchase_times(replay_tables, shift_in_weeks):
    shifted_attempts = (
        replay_tables.joined_attempts
        .assign(timestamp = lambda table: table["timestamp"].map(lambda timestamp_text: shift_timestamp_text(timestamp_text, shift_in_weeks)))
        .assign(delivery_by = lambda table: table["delivery_by"].map(lambda delivery_date_text: shift_delivery_date_text(delivery_date_text, shift_in_weeks)))
    )



    # Check that every shifted purchase stays inside Swiss summer time
    shifted_times = shifted_attempts["timestamp"].map(replay.read_simulated_time)
    assert (shifted_times >= SUMMER_TIME_START).all(), "A shifted purchase lies before the start of Swiss summer time"
    assert (shifted_times < SUMMER_TIME_END).all(), "A shifted purchase lies after the end of Swiss summer time"

    return replay.ReplayTables(
        joined_attempts = shifted_attempts,
        cart_lines_by_source_id = replay_tables.cart_lines_by_source_id,
    )









#### Step 3: Check the decisions under shifted times ####

# Check that a shift by whole weeks leaves every decision and every reason code as it was
@pytest.mark.parametrize("shift_in_weeks", SHIFTS_IN_WEEKS)
def test_shifted_weeks_give_the_same_decisions(replay_tables, published_results, shift_in_weeks):

    # Replay the shifted purchases with the same engine
    shifted_tables = shift_purchase_times(replay_tables, shift_in_weeks)
    shifted_results = replay.replay_scenarios(shifted_tables, replay.list_scenario_ids(shifted_tables), replay.choose_decision_function())



    # Expect that the shifted time and the shifted delivery date really reached the engine
    purchases_with_an_unshifted_time = [
        published_result["record"]["source_authorization_id"]
        for published_result, shifted_result in zip(published_results, shifted_results)
        if shifted_result["message"]["authorization"]["timestamp"] != shift_timestamp_text(published_result["message"]["authorization"]["timestamp"], shift_in_weeks)
    ]
    purchases_with_an_unshifted_delivery_date = [
        published_result["record"]["source_authorization_id"]
        for published_result, shifted_result in zip(published_results, shifted_results)
        if (shifted_result["message"]["authorization"]["delivery_by"] or "") != shift_delivery_date_text(published_result["message"]["authorization"]["delivery_by"] or "", shift_in_weeks)
    ]
    assert purchases_with_an_unshifted_time == []
    assert purchases_with_an_unshifted_delivery_date == []



    # Expect the same outcome on every purchase
    assert list_differing_purchases(published_results, shifted_results) == []
