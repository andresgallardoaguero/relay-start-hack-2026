# Script: preferences.py
# Purpose: Model the monthly confirmation that keeps a shopping mandate active
# Author: Viktor Vantsev
# Date: September 2026

from datetime import datetime, timedelta, timezone



CHECK_INTERVAL_DAYS = 30
RESPONSE_WINDOW_DAYS = 30
PREFERENCE_CHECK_KEY_PREFIX = "preference_check:"

SCHEDULED = "scheduled"
PENDING = "pending"
STOPPED = "stopped"



def format_moment(moment):
    return moment.astimezone(timezone.utc).isoformat()



def read_moment(moment_text):
    return datetime.fromisoformat(moment_text.replace("Z", "+00:00"))



def preference_check_key(mandate_id):
    return PREFERENCE_CHECK_KEY_PREFIX + mandate_id



# Begin one cycle when the customer confirms a mandate or confirms that it still fits.
def start_preference_cycle(mandate_id, confirmed_at):
    check_due_at = confirmed_at + timedelta(days = CHECK_INTERVAL_DAYS)
    response_deadline_at = check_due_at + timedelta(days = RESPONSE_WINDOW_DAYS)
    return {
        "version": "1",
        "mandate_id": mandate_id,
        "status": SCHEDULED,
        "agent_enabled": True,
        "needs_response": False,
        "last_confirmed_at": format_moment(confirmed_at),
        "check_due_at": format_moment(check_due_at),
        "response_deadline_at": format_moment(response_deadline_at),
        "stopped_at": None,
        "stop_reason": None,
    }



# Move a scheduled check to the inbox after 30 days, and stop the agent after
# another 30 days without an answer. Boundary instants belong to the new state.
def advance_preference_check(preference_check, now):
    state = dict(preference_check)
    if state["status"] == STOPPED:
        return state
    if now >= read_moment(state["response_deadline_at"]):
        return {
            **state,
            "status": STOPPED,
            "agent_enabled": False,
            "needs_response": False,
            "stopped_at": format_moment(now),
            "stop_reason": "confirmation_expired",
        }
    if now >= read_moment(state["check_due_at"]):
        return {**state, "status": PENDING, "agent_enabled": True, "needs_response": True}
    return state



# Accept an answer only while the monthly question is open. A positive answer
# starts a new cycle; a negative answer stops the agent immediately.
def answer_preference_check(preference_check, preferences_still_hold, answered_at):
    state = advance_preference_check(preference_check, answered_at)
    if state["status"] != PENDING:
        raise ValueError("The monthly preferences question is not open")
    if preferences_still_hold:
        return start_preference_cycle(state["mandate_id"], answered_at)
    return {
        **state,
        "status": STOPPED,
        "agent_enabled": False,
        "needs_response": False,
        "stopped_at": format_moment(answered_at),
        "stop_reason": "preferences_changed",
    }



# Stop a cycle when the mandate is withdrawn for another reason.
def stop_preference_check(preference_check, stopped_at, reason = "mandate_revoked"):
    return {
        **preference_check,
        "status": STOPPED,
        "agent_enabled": False,
        "needs_response": False,
        "stopped_at": format_moment(stopped_at),
        "stop_reason": reason,
    }
