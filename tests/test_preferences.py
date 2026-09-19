# Script: test_preferences.py
# Purpose: Prove the isolated monthly preference state machine
# Author: Viktor Vantsev
# Date: September 2026

from datetime import datetime, timedelta, timezone

import pytest

from app.preferences import (
    advance_preference_check,
    answer_preference_check,
    start_preference_cycle,
    stop_preference_check,
)



NOW = datetime(2026, 9, 19, 8, 0, 0, tzinfo = timezone.utc)



def test_cycle_asks_after_30_days_and_stops_after_another_30():
    state = start_preference_cycle("TM_1", NOW)
    assert state["status"] == "scheduled" and state["agent_enabled"] is True
    assert advance_preference_check(state, NOW + timedelta(days = 30) - timedelta(seconds = 1))["status"] == "scheduled"
    pending = advance_preference_check(state, NOW + timedelta(days = 30))
    assert pending["status"] == "pending" and pending["needs_response"] is True
    assert advance_preference_check(pending, NOW + timedelta(days = 60) - timedelta(seconds = 1))["status"] == "pending"
    stopped = advance_preference_check(pending, NOW + timedelta(days = 60))
    assert stopped["status"] == "stopped" and stopped["agent_enabled"] is False
    assert stopped["stop_reason"] == "confirmation_expired"



def test_positive_answer_starts_a_new_cycle_and_negative_answer_stops():
    pending = advance_preference_check(start_preference_cycle("TM_1", NOW), NOW + timedelta(days = 30))
    confirmed = answer_preference_check(pending, True, NOW + timedelta(days = 31))
    assert confirmed["status"] == "scheduled"
    assert confirmed["check_due_at"] == (NOW + timedelta(days = 61)).isoformat()
    changed = answer_preference_check(pending, False, NOW + timedelta(days = 31))
    assert changed["status"] == "stopped" and changed["stop_reason"] == "preferences_changed"



def test_answer_is_refused_before_the_question_and_after_expiry():
    scheduled = start_preference_cycle("TM_1", NOW)
    with pytest.raises(ValueError, match = "not open"):
        answer_preference_check(scheduled, True, NOW + timedelta(days = 1))
    with pytest.raises(ValueError, match = "not open"):
        answer_preference_check(scheduled, True, NOW + timedelta(days = 60))



def test_external_revocation_stops_the_cycle_without_mutating_the_input():
    scheduled = start_preference_cycle("TM_1", NOW)
    stopped = stop_preference_check(scheduled, NOW + timedelta(days = 2))
    assert scheduled["status"] == "scheduled"
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "mandate_revoked"
