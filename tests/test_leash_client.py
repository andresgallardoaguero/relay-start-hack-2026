# Script: test_leash_client.py
# Purpose: Check that the client sends the key, reads every kind of answer and turns a refusal into one typed error
# Author: Jonas Lüthi
# Date: September 2026

import pytest

from app.api_client.leash import LeashApiError, find_value
from platform_helpers import build_test_client, build_test_platform, build_test_scenario, run_async



# The health check needs no key, everything else does, and a wrong key gives a 401 with the platform's message
def test_key_is_required_everywhere_except_the_health_check():
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        client = build_test_client(platform, team_api_key = "wrong")
        assert (await client.healthz())["status"] == "ok"
        with pytest.raises(LeashApiError) as refusal:
            await client.bootstrap()
        assert refusal.value.status_code == 401
        assert "A valid team bearer token is required" in str(refusal.value)
        await client.close()
    run_async(scenario())



# The mandate, run and purchase calls answer as the platform describes them, and an empty poll gives None
def test_the_whole_flow_through_the_client():
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])}, settings = {"redelivery_after_seconds": 0})
        client = build_test_client(platform)
        bootstrap = await client.bootstrap()
        assert bootstrap["decision_timeout_seconds"] == 8 and bootstrap["scenarios"][0]["event_count"] == 1
        assert "fx_rates_to_chf" in await client.reference_data()
        assert await client.next_decision_request(1) is None



        # Create, confirm, read, tighten
        draft = await client.create_mandate("Buy milk for CHF 20 or less.", [], "ask", ["guidance"], ["question"])
        mandate = await client.confirm_mandate(draft["draft_id"])
        assert mandate["status"] == "active" and mandate["instruction"] == "Buy milk for CHF 20 or less."
        assert (await client.get_mandate(mandate["mandate_id"]))["mandate_id"] == mandate["mandate_id"]
        tightened = await client.patch_mandate(mandate["mandate_id"], uncertainty_policy = "decline")
        assert tightened["uncertainty_policy"] == "decline"
        with pytest.raises(LeashApiError) as refusal:
            await client.patch_mandate(mandate["mandate_id"], uncertainty_policy = "ask")
        assert refusal.value.status_code == 409



        # Start, receive, answer, receive again, list, read events
        run = await client.start_scenario_run("SCEN0000", mandate["mandate_id"])
        envelope = await client.next_decision_request(1)
        assert envelope["run_id"] == run["run_id"] and envelope["data"]["type"] == "authorization.request"
        assert envelope["data"]["mandate"]["uncertainty_policy"] == "decline"
        redelivered = await client.next_decision_request(1)
        assert redelivered["authorization_id"] == envelope["authorization_id"] and redelivered["delivery_count"] == 2
        answer = await client.post_decision(envelope["authorization_id"], {"authorization_id": envelope["authorization_id"], "decision": "step_up"})
        assert answer["status"] == "pending"
        resolved = await client.resolve_authorization(envelope["authorization_id"], "approve", "yes")
        assert resolved["status"] == "approved"
        assert await client.next_decision_request(1) is None
        assert (await client.get_scenario_run(run["run_id"]))["status"] == "completed"
        assert len((await client.list_authorizations())["authorizations"]) == 1
        events = await client.read_events(0)
        assert [event["type"] for event in events["events"]][-1] == "run.completed"
        assert (await client.reset_team())["status"] == "reset"
        assert (await client.list_authorizations())["authorizations"] == []
        await client.close()
    run_async(scenario())



# A decision after the deadline or for an unknown purchase is refused with the platform's code
def test_refusals_carry_the_status_code():
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        client = build_test_client(platform)
        with pytest.raises(LeashApiError) as refusal:
            await client.post_decision("LA_unknown", {"authorization_id": "LA_unknown", "decision": "approve"})
        assert refusal.value.status_code == 404 and refusal.value.path.endswith("/decision")
        with pytest.raises(LeashApiError) as refusal:
            await client.start_scenario_run("SCEN9999", "TM_none")
        assert refusal.value.status_code == 404
        await client.close()
    run_async(scenario())



# A field is found at the top level or one level down, and a missing field gives the default
def test_find_value_looks_one_level_down():
    assert find_value({"draft_id": "d1"}, "draft_id") == "d1"
    assert find_value({"data": {"draft_id": "d2"}}, "draft_id") == "d2"
    assert find_value({"data": {"other": 1}}, "draft_id", "none") == "none"
    assert find_value("not a dict", "draft_id") is None
