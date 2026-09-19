# Script: test_webapi.py
# Purpose: Check that the web API carries the three screens through the whole flow, from compiling an instruction to answering a question, and streams events
# Author: Jonas Lüthi
# Date: September 2026

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.webapi.sse import EventBroadcaster
from platform_helpers import build_test_platform, build_test_scenario, build_test_service, build_test_settings



# State the instruction of the tests and the rule it compiles to
INSTRUCTION = "Buy one ordinary grocery item for CHF 20 or less. Ask me when uncertain."
EXPECTED_RULE = {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 20.0, "currency": "CHF", "scope": "purchase"}
EXPECTED_GOODS_RULE = {"field": "authorization.items.item_category", "operator": "in", "value": ["groceries"]}



# Build the app around a small platform and open a client that runs the app's start and stop
@pytest.fixture
def api(tmp_path):
    platform = build_test_platform({"SCEN0001": build_test_scenario([20.0, 21.5])})
    settings = build_test_settings()
    service = build_test_service(platform, tmp_path, settings = settings)
    app = create_app(service = service, settings = settings)
    with TestClient(app) as client:
        yield client, service, platform



# Wait until the store holds the given number of decisions, or fail
def wait_for_decisions(client, count, timeout_seconds = 5.0):
    started = time.perf_counter()
    while time.perf_counter() - started < timeout_seconds:
        decisions = client.get("/api/decisions").json()["decisions"]
        if len(decisions) >= count:
            return decisions
        time.sleep(0.05)
    raise AssertionError("Expected " + str(count) + " decisions in time")



# Compiling shows the limit rule and the check list without touching the platform
def test_compile_shows_rules_and_checks(api):
    client, service, platform = api
    answer = client.post("/api/policy/compile", json = {"instruction": INSTRUCTION}).json()
    assert answer["hard_rules"] == [EXPECTED_RULE, EXPECTED_GOODS_RULE]
    check_labels = [check["label"] for check in answer["checks"]]
    assert check_labels[:3] == ["Limit per order", "Goods covered", "Requested thing"]
    assert check_labels[-3:] == ["When uncertain", "Shop text is data", "One purchase, one decision"]
    assert answer["open_questions"] == [] and answer["expectations"]["per_order_limit_chf"] == "20"
    assert platform.drafts == {}
    assert client.post("/api/policy/compile", json = {"instruction": ""}).status_code == 422



# The whole flow through the screens, from draft to the customer's answer
def test_draft_confirm_run_and_resolve(api):
    client, service, platform = api
    assert client.get("/api/policy").status_code == 404
    draft = client.post("/api/policy/draft", json = {"instruction": INSTRUCTION, "hard_rules": [EXPECTED_RULE], "uncertainty_policy": "ask"}).json()
    confirmed = client.post("/api/policy/confirm", json = {"draft_id": draft["draft_id"]}).json()
    assert confirmed["status"] == "active" and confirmed["hard_rules"] == [EXPECTED_RULE]
    policy = client.get("/api/policy").json()
    assert policy["mandate"]["mandate_id"] == confirmed["mandate_id"]
    assert policy["policy"]["hard_rules"] == [EXPECTED_RULE, EXPECTED_GOODS_RULE]



    # Start the run, and the worker decides both purchases in the background
    run = client.post("/api/run/start", json = {"scenario_id": "SCEN0001"}).json()
    assert run["status"] == "active" and run["mandate_id"] == confirmed["mandate_id"]
    decisions = wait_for_decisions(client, 2)
    assert [record["decision"] for record in decisions] == ["approve", "step_up"]
    pending = client.get("/api/pending").json()["pending"]
    assert len(pending) == 1 and pending[0]["reason_codes"] == ["SMALL_OVERSHOOT", "GOAL_ALREADY_FULFILLED"]
    assert pending[0]["approval_warning"] is None
    assert client.get("/api/decisions/" + pending[0]["live_authorization_id"]).json()["trace"]["decision"] == "step_up"
    assert client.get("/api/decisions/LA_none").status_code == 404



    # The customer approves, and the platform and the store agree
    resolved = client.post("/api/resolve/" + pending[0]["live_authorization_id"], json = {"decision": "approve"}).json()
    assert resolved["status"] == "approved"
    assert resolved["resolution"]["budget_warning"] is None
    assert platform.authorizations[pending[0]["live_authorization_id"]].status == "approved"
    assert client.get("/api/pending").json()["pending"] == []
    refused = client.post("/api/resolve/" + pending[0]["live_authorization_id"], json = {"decision": "approve"})
    assert refused.status_code == 409 and "does not wait" in refused.json()["error"]["message"]



    # The run closes and the status shows the counts
    started = time.perf_counter()
    while time.perf_counter() - started < 5.0 and client.get("/api/runs/" + run["run_id"]).json()["status"] == "active":
        time.sleep(0.05)
    assert client.get("/api/runs/" + run["run_id"]).json()["status"] == "completed"
    status = client.get("/api/status").json()
    assert status["counts"]["approved"] == 2 and status["worker"]["counters"]["decisions"] == 2
    assert status["current_mandate_id"] == confirmed["mandate_id"] and status["llm_mode"] == "off"
    assert client.get("/api/runs").json()["runs"][0]["run_id"] == run["run_id"]



# Tightening adds rules and moves uncertainty to decline, loosening is refused, and revoking withdraws the permission
def test_tighten_and_revoke(api):
    client, service, platform = api
    draft = client.post("/api/policy/draft", json = {"instruction": INSTRUCTION, "hard_rules": [EXPECTED_RULE]}).json()
    client.post("/api/policy/confirm", json = {"draft_id": draft["draft_id"]})
    size_rule = {"field": "item.size", "operator": "=", "value": "43", "scope": "purchase"}
    tightened = client.patch("/api/policy", json = {"add_rules": [size_rule], "uncertainty_policy": "decline"}).json()
    assert tightened["hard_rules"] == [EXPECTED_RULE, size_rule] and tightened["uncertainty_policy"] == "decline"
    assert client.patch("/api/policy", json = {"uncertainty_policy": "ask"}).status_code == 422
    revoked = client.delete("/api/policy").json()
    assert revoked["status"] == "revoked"
    assert client.post("/api/run/start", json = {"scenario_id": "SCEN0001"}).status_code == 409



# The screen may name the mandate it holds in the path, and a mandate that is not the current one is refused
def test_tighten_and_revoke_with_the_mandate_in_the_path(api):
    client, service, platform = api
    draft = client.post("/api/policy/draft", json = {"instruction": INSTRUCTION, "hard_rules": [EXPECTED_RULE]}).json()
    mandate_id = client.post("/api/policy/confirm", json = {"draft_id": draft["draft_id"]}).json()["mandate_id"]
    refused = client.patch("/api/policy/TM_other", json = {"uncertainty_policy": "decline"})
    assert refused.status_code == 409 and "not the current mandate" in refused.json()["error"]["message"]
    assert client.delete("/api/policy/TM_other").status_code == 409
    tightened = client.patch("/api/policy/" + mandate_id, json = {"uncertainty_policy": "decline"}).json()
    assert tightened["uncertainty_policy"] == "decline" and tightened["mandate_id"] == mandate_id
    assert client.get("/api/policy").json()["policy"]["uncertainty_policy"] == "decline"
    assert client.delete("/api/policy/" + mandate_id).json()["status"] == "revoked"



# The worker can be started for a run someone else starts, and stopped, and the stream carries the whole status each time
def test_worker_start_and_stop(api):
    client, service, platform = api
    assert client.get("/api/status").json()["worker"]["state"] == "idle"
    queue = service.broadcaster.subscribe()
    started = client.post("/api/worker/start", json = {"continuous": True}).json()
    assert started["state"] == "polling" and started["continuous"] is True
    stopped = client.post("/api/worker/stop").json()
    assert stopped["state"] == "idle"
    event_name, payload = queue.get_nowait()
    assert event_name == "status" and payload["worker"]["state"] == "polling" and payload["engine_mode"] == "deterministic"
    service.broadcaster.unsubscribe(queue)



# Every published event reaches an open stream in order, formatted as a server-sent event
def test_broadcaster_streams_published_events_in_order():
    async def scenario():
        broadcaster = EventBroadcaster()
        queue = broadcaster.subscribe()
        broadcaster.publish("decision", {"live_authorization_id": "LA_1"})
        broadcaster.publish("resolution", {"live_authorization_id": "LA_1", "status": "approved"})
        stream = broadcaster.stream(queue, heartbeat_seconds = 0.05)
        first = await anext(stream)
        second = await anext(stream)
        heartbeat = await anext(stream)
        await stream.aclose()
        assert first == 'event: decision\ndata: {"live_authorization_id": "LA_1"}\n\n'
        assert second.startswith("event: resolution\ndata: ") and '"status": "approved"' in second
        assert heartbeat == ": heartbeat\n\n"
        assert queue not in broadcaster.queues
    asyncio.run(scenario())



# A team reset clears the platform and the store
def test_team_reset_clears_everything(api):
    client, service, platform = api
    draft = client.post("/api/policy/draft", json = {"instruction": INSTRUCTION, "hard_rules": [EXPECTED_RULE]}).json()
    client.post("/api/policy/confirm", json = {"draft_id": draft["draft_id"]})
    client.post("/api/run/start", json = {"scenario_id": "SCEN0001"})
    wait_for_decisions(client, 2)
    answer = client.post("/api/team/reset").json()
    assert answer["platform"]["status"] == "reset"
    assert client.get("/api/decisions").json()["decisions"] == []
    assert client.get("/api/policy").status_code == 404
    assert platform.runs == {}
