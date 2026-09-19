# Script: test_fake_platform.py
# Purpose: Check that the in-process platform delivers purchases in order, repeats an unanswered one, feeds the context from the decisions and cancels on revoke
# Author: Jonas Lüthi
# Date: September 2026

from app.api_client.offline import build_offline_platform
from app.models.events import read_purchase_message
from platform_helpers import FakeClock, build_test_client, build_test_platform, build_test_scenario, run_async



# The context of a later purchase carries the earlier decisions of the run, and the approved spend counts final approvals only
def test_context_is_built_from_the_decisions_of_the_run():
    async def scenario():
        platform = build_test_platform({"SCEN0001": build_test_scenario([20.0, 15.0, 10.0])})
        client = build_test_client(platform)
        draft = await client.create_mandate("Buy milk.", [], "ask")
        mandate = await client.confirm_mandate(draft["draft_id"])
        await client.start_scenario_run("SCEN0001", mandate["mandate_id"])
        first = await client.next_decision_request(1)
        await client.post_decision(first["authorization_id"], {"authorization_id": first["authorization_id"], "decision": "approve"})
        second = await client.next_decision_request(1)
        await client.post_decision(second["authorization_id"], {"authorization_id": second["authorization_id"], "decision": "step_up"})
        third = await client.next_decision_request(1)
        assert third["data"]["context"]["approved_spend_in_period_chf"] == 20.0
        statuses = [earlier["status"] for earlier in third["data"]["context"]["recent_authorizations"]]
        assert statuses == ["approved", "pending"]
        assert third["data"]["authorization"]["replay_order"] == 3
        await client.close()
    run_async(scenario())



# An unanswered purchase is delivered again only after the redelivery pause
def test_unanswered_purchase_is_delivered_again_after_the_pause():
    async def scenario():
        clock = FakeClock()
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])}, clock = clock, settings = {"redelivery_after_seconds": 3})
        client = build_test_client(platform)
        draft = await client.create_mandate("Buy milk.", [], "ask")
        mandate = await client.confirm_mandate(draft["draft_id"])
        await client.start_scenario_run("SCEN0000", mandate["mandate_id"])
        first = await client.next_decision_request(1)
        assert await client.next_decision_request(0) is None
        clock.advance(3)
        again = await client.next_decision_request(0)
        assert again["authorization_id"] == first["authorization_id"] and again["delivery_count"] == 2
        await client.close()
    run_async(scenario())



# Revoking the mandate cancels the run and the purchases that are still to come
def test_revoke_cancels_the_remaining_purchases():
    async def scenario():
        platform = build_test_platform({"SCEN0001": build_test_scenario([20.0, 15.0])})
        client = build_test_client(platform)
        draft = await client.create_mandate("Buy milk.", [], "ask")
        mandate = await client.confirm_mandate(draft["draft_id"])
        run = await client.start_scenario_run("SCEN0001", mandate["mandate_id"])
        first = await client.next_decision_request(1)
        revoked = await client.revoke_mandate(mandate["mandate_id"])
        assert revoked["status"] == "revoked"
        assert (await client.get_scenario_run(run["run_id"]))["status"] == "cancelled"
        assert platform.authorizations[first["authorization_id"]].status == "cancelled"
        assert await client.next_decision_request(0) is None
        await client.close()
    run_async(scenario())



# The offline platform built from the public purchases serves the five scenarios with 45 valid messages
def test_offline_platform_serves_the_public_purchases():
    async def scenario():
        platform = build_offline_platform("leash_offline", settings = {"long_poll_max_wait_seconds": 0.2})
        client = build_test_client(platform)
        bootstrap = await client.bootstrap()
        assert {scenario["scenario_id"]: scenario["event_count"] for scenario in bootstrap["scenarios"]} == {"SCEN0000": 1, "SCEN0001": 10, "SCEN0002": 12, "SCEN0003": 11, "SCEN0004": 11}
        assert bootstrap["scenarios"][0]["name"] == "Connection check" and bootstrap["scenarios"][1]["name"] == "Household budget"
        draft = await client.create_mandate("Buy clothing for at most CHF 250 per order.", [], "ask")
        mandate = await client.confirm_mandate(draft["draft_id"])
        await client.start_scenario_run("SCEN0003", mandate["mandate_id"])
        source_ids = []
        for position in range(11):
            envelope = await client.next_decision_request(1)
            event = read_purchase_message(envelope["data"])
            assert event.mandate.mandate_id == mandate["mandate_id"]
            source_ids.append(event.authorization.source_authorization_id)
            await client.post_decision(envelope["authorization_id"], {"authorization_id": envelope["authorization_id"], "decision": "approve"})
        assert len(set(source_ids)) == 11 and all(source_id.startswith("AU") for source_id in source_ids)
        assert await client.next_decision_request(1) is None
        await client.close()
    run_async(scenario())
