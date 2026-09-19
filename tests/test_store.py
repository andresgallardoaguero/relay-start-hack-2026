# Script: test_store.py
# Purpose: Check that the store records a purchase once, counts repeated deliveries, expires unanswered questions and keeps mandates and runs
# Author: Jonas Lüthi
# Date: September 2026

from datetime import datetime, timedelta, timezone

import pytest

from app.state.store import DecisionStore



# State a fixed moment
NOW = datetime(2026, 9, 19, 8, 0, 0, tzinfo = timezone.utc)



# Build a record of a question with the given identifier and amount
def build_pending_record(live_authorization_id, amount_text = "21.50", run_id = "run_1"):
    return {
        "live_authorization_id": live_authorization_id,
        "run_id": run_id,
        "source_authorization_id": "AU0004",
        "scenario_id": "SCEN0001",
        "replay_order": 4,
        "mandate_id": "TM_1",
        "sim_timestamp": "2026-08-12T09:00:00+00:00",
        "amount_chf": amount_text,
        "currency": "CHF",
        "merchant_id": "ME0001",
        "merchant_name": "Example Market",
        "decision": "step_up",
        "status": "pending",
        "reason_codes": ["SMALL_OVERSHOOT"],
        "customer_message": "CHF 21.50 is above your limit of CHF 20.00. Approve anyway?",
        "notes": [],
        "received_at": NOW.isoformat(),
        "decided_at": NOW.isoformat(),
        "deadline_at": (NOW + timedelta(seconds = 8)).isoformat(),
        "margin_ms": 7000.0,
        "total_ms": 12.5,
        "posted": True,
        "post_status_code": 200,
        "delivery_count": 1,
        "human_deadline_at": (NOW + timedelta(seconds = 120)).isoformat(),
        "source": "live",
        "trace": {"decision": "step_up"},
        "problems": None,
        "resolution": None,
    }



@pytest.fixture
def store():
    store = DecisionStore(":memory:")
    yield store
    store.close()



# A saved record comes back with every field, and an unknown identifier gives None
def test_save_and_read_a_decision(store):
    saved = store.save_decision(build_pending_record("LA_1"))
    assert saved["sequence"] == 1
    assert saved["amount_chf"] == "21.50"
    assert saved["reason_codes"] == ["SMALL_OVERSHOOT"]
    assert saved["trace"] == {"decision": "step_up"}
    assert saved["posted"] is True
    assert store.get_decision("LA_unknown") is None



# Saving the same identifier again replaces the record instead of adding a second one
def test_the_same_purchase_is_recorded_once(store):
    store.save_decision(build_pending_record("LA_1"))
    store.save_decision({**build_pending_record("LA_1"), "customer_message": "changed"})
    assert len(store.list_decisions()) == 1
    assert store.get_decision("LA_1")["customer_message"] == "changed"



# A repeated delivery raises the delivery count and nothing else
def test_redelivery_is_counted(store):
    store.save_decision(build_pending_record("LA_1"))
    assert store.note_redelivery("LA_1") == 2
    assert store.note_redelivery("LA_1") == 3
    assert store.count_by_status()["pending"] == 1



# Decisions come back in order of arrival, and a limit keeps the newest ones
def test_decisions_are_listed_in_order_of_arrival(store):
    for position in range(1, 5):
        store.save_decision(build_pending_record("LA_" + str(position), run_id = "run_1" if position < 4 else "run_2"))
    assert [record["live_authorization_id"] for record in store.list_decisions()] == ["LA_1", "LA_2", "LA_3", "LA_4"]
    assert [record["live_authorization_id"] for record in store.list_decisions(limit = 2)] == ["LA_3", "LA_4"]
    assert [record["live_authorization_id"] for record in store.list_decisions(run_id = "run_2")] == ["LA_4"]



# A question expires once its answer window has passed, and not one second before
def test_pending_questions_expire_after_the_answer_window(store):
    store.save_decision(build_pending_record("LA_1"))
    assert store.expire_pending(NOW + timedelta(seconds = 119)) == []
    expired = store.expire_pending(NOW + timedelta(seconds = 120))
    assert [record["live_authorization_id"] for record in expired] == ["LA_1"]
    assert store.get_decision("LA_1")["status"] == "expired"
    assert store.list_pending() == []



# A resolution changes the status and is kept with the record
def test_resolution_changes_the_status(store):
    store.save_decision(build_pending_record("LA_1"))
    resolution = {"decision": "approve", "customer_message": "Yes", "resolved_at": NOW.isoformat(), "posted": True, "post_status_code": 200}
    store.save_resolution("LA_1", resolution)
    store.set_status("LA_1", "approved", resolution)
    record = store.get_decision("LA_1")
    assert record["status"] == "approved"
    assert record["resolution"]["decision"] == "approve"
    assert store.get_resolution("LA_1")["posted"] is True
    assert store.mark_posted("LA_1", False, 503, "server error") is None
    assert store.get_decision("LA_1")["post_error"] == "server error"



# Mandates, runs and single values are kept and can be wiped together
def test_mandates_runs_and_values(store):
    mandate = store.save_mandate({"mandate_id": "TM_1", "draft_id": "draft_1", "instruction": "Buy milk", "hard_rules": [{"field": "authorization.billing_amount_chf", "operator": "<=", "value": 20}], "uncertainty_policy": "ask"})
    assert mandate["status"] == "active"
    assert mandate["hard_rules"][0]["value"] == 20
    run = store.save_run({"run_id": "run_1", "scenario_id": "SCEN0000", "mandate_id": "TM_1", "raw": {"counters": {"total_events": 1}}})
    assert run["status"] == "active"
    assert store.list_runs(active_only = True)[0]["run_id"] == "run_1"
    store.save_run({**run, "status": "completed"})
    assert store.list_runs(active_only = True) == []
    store.set_value("current_mandate_id", "TM_1")
    assert store.get_value("current_mandate_id") == "TM_1"
    assert store.get_value("missing", "default") == "default"
    store.clear_all()
    assert store.list_mandates() == [] and store.list_runs() == [] and store.get_value("current_mandate_id") is None
