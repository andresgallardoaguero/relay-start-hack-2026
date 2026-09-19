# Script: test_budget.py
# Purpose: Check that the engine gets the earlier of the platform deadline minus the reserve and its own budget, and never a negative time
# Author: Jonas Lüthi
# Date: September 2026

from datetime import datetime, timedelta, timezone

from app.worker.budget import DeadlineBudget



# State a fixed arrival and the usual settings
RECEIVED_AT = datetime(2026, 9, 19, 8, 0, 0, tzinfo = timezone.utc)
ENGINE_BUDGET_MS = 6000
POST_RESERVE_MS = 1500



# Build a budget with a deadline the given seconds after arrival
def build_budget(deadline_seconds_after_arrival):
    return DeadlineBudget(
        received_at = RECEIVED_AT,
        deadline_at = RECEIVED_AT + timedelta(seconds = deadline_seconds_after_arrival),
        engine_budget_ms = ENGINE_BUDGET_MS,
        post_reserve_ms = POST_RESERVE_MS,
    )



# A fresh purchase with 8 seconds gives the engine its own 6 second budget, since the platform bound of 6.5 is later
def test_fresh_purchase_gets_the_engine_budget():
    budget = build_budget(8)
    assert budget.seconds_left_for_engine(RECEIVED_AT) == 6.0
    assert budget.engine_deadline_at == RECEIVED_AT + timedelta(seconds = 6)



# A purchase with 5 seconds left gets 3.5 seconds, which is the deadline minus the reserve
def test_platform_bound_wins_when_it_is_earlier():
    budget = build_budget(5)
    assert budget.seconds_left_for_engine(RECEIVED_AT) == 3.5



# A purchase with only 1 second left gives the engine nothing, because the reserve is larger than what is left
def test_late_purchase_gives_the_engine_nothing_but_never_a_negative_time():
    budget = build_budget(1)
    assert budget.seconds_left_for_engine(RECEIVED_AT) == 0.0
    assert budget.seconds_until_deadline(RECEIVED_AT) == 1.0
    assert not budget.is_past_deadline(RECEIVED_AT)



# Time passing after arrival reduces what is left, down to zero and not below
def test_time_left_shrinks_as_the_clock_moves():
    budget = build_budget(8)
    assert budget.seconds_left_for_engine(RECEIVED_AT + timedelta(seconds = 4)) == 2.0
    assert budget.seconds_left_for_engine(RECEIVED_AT + timedelta(seconds = 7)) == 0.0
    assert budget.is_past_deadline(RECEIVED_AT + timedelta(seconds = 8))
    assert budget.seconds_until_deadline(RECEIVED_AT + timedelta(seconds = 9)) == -1.0
