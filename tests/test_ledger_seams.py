# Script: test_ledger_seams.py
# Purpose: Check that the worker hands the engine the memory of its own run, that an approval is checked against the budget again and warns, and that a second run starts with an empty period
# Author: Andrés Gallardo
# Date: September 2026

from datetime import datetime, timedelta, timezone

from platform_helpers import (
    build_test_platform,
    build_test_service,
    confirm_and_start,
    fetch_envelope,
    format_message_time,
    run_async,
)









#### Step 1: Define the shared helpers ####

# State an instruction with a limit per order and a budget over seven days, and the four amounts of the run.
# CHF 20.00 and CHF 18.00 fit. CHF 15.00 brings the period to 53.00 and CHF 14.00 brings it to 52.00, so both ask while the other is open.
BUDGET_INSTRUCTION = "Buy groceries, at most CHF 20 per order and at most CHF 50 across any seven days. Ask me when uncertain."
RUN_AMOUNTS = [20.0, 18.0, 15.0, 14.0]
SCENARIO_NAME = "SCEN0000"



# State the simulated time of the first purchase and the hours between two purchases, which keeps them apart for the split order check
FIRST_PURCHASE_TIME = datetime(2026, 8, 12, 6, 0, 0, tzinfo = timezone.utc)
HOURS_BETWEEN_PURCHASES = 3



# Build the attempts of the test run, each with its own simulated time
def build_timed_scenario(amounts):
    return [
        {
            "attempt": {
                "authorization_id": "AU_SEAM_" + str(position).zfill(4),
                "replay_order": position,
                "billing_amount_chf": amount,
                "timestamp": format_message_time(FIRST_PURCHASE_TIME + timedelta(hours = HOURS_BETWEEN_PURCHASES * (position - 1))),
                "invalid": False,
            },
            "cart_lines": [],
        }
        for position, amount in enumerate(amounts, start = 1)
    ]



# Let the worker decide the next purchases of the platform, one after the other, and return their records
async def decide_next_purchases(service, purchase_count):
    return [
        await service.worker.handle_envelope(await fetch_envelope(service))
        for position in range(purchase_count)
    ]



# Find one evidence value of the budget guard in a stored record
def read_budget_evidence(record, fact_name):
    budget_entry = next(guard_entry for guard_entry in record["trace"]["guards"] if guard_entry["guard_id"] == "period_budget")
    return next(evidence_item["value"] for evidence_item in budget_entry["evidence"] if evidence_item["fact"] == fact_name)









#### Step 2: Check the worker ####

# Check that the worker counts the approved purchases of its run, so the third and the fourth purchase ask, each at its own total
def test_worker_hands_the_engine_the_memory_of_the_run(tmp_path):
    async def scenario():
        platform = build_test_platform({SCENARIO_NAME: build_timed_scenario(RUN_AMOUNTS)})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, SCENARIO_NAME, instruction = BUDGET_INSTRUCTION)
        records = await decide_next_purchases(service, 4)
        assert [record["decision"] for record in records] == ["approve", "approve", "step_up", "step_up"]
        assert [record["status"] for record in records] == ["approved", "approved", "pending", "pending"]
        assert [record["reason_codes"] for record in records] == [[], [], ["SMALL_OVERSHOOT"], ["SMALL_OVERSHOOT"]]
        assert [read_budget_evidence(record, "period_total_chf") for record in records] == [20.0, 38.0, 53.0, 52.0]
        assert [read_budget_evidence(record, "period_spent_before_chf") for record in records] == [0.0, 20.0, 38.0, 38.0]
        assert "CHF 53.00" in records[2]["customer_message"]
        assert "CHF 52.00" in records[3]["customer_message"]
        await service.client.close()
    run_async(scenario())



# State an instruction with a limit per order and no budget, under which CHF 22.01 declines as more than 10 percent over the limit
LIMIT_INSTRUCTION = "Buy groceries, at most CHF 20 per order. Ask me when uncertain."



# Build the attempts of a run whose purchases all hold the same cart, which each attempt says by stating the same item
def build_scenario_with_the_same_cart(amounts):
    return [
        {**scenario_entry, "attempt": {**scenario_entry["attempt"], "item_id": "IT_SEAM_SAME_ITEM"}}
        for scenario_entry in build_timed_scenario(amounts)
    ]



# Let the worker decide a run of two purchases with the same cart at the same shop, three hours apart, and return their records
async def decide_two_purchases(tmp_path, amounts):
    platform = build_test_platform({SCENARIO_NAME: build_scenario_with_the_same_cart(amounts)})
    service = build_test_service(platform, tmp_path)
    await confirm_and_start(service, SCENARIO_NAME, instruction = LIMIT_INSTRUCTION)
    records = await decide_next_purchases(service, 2)
    await service.client.close()
    return records



# Check that the worker hands the engine the cart of the purchase being decided, so the same cart for the same amount at the same shop,
# three hours after an approved order, asks as a repeated order. The same order after a declined one does not,
# because only an order the customer already has can be bought twice.
def test_worker_asks_about_a_repeated_order_and_not_after_a_declined_one(tmp_path):
    async def scenario():
        records_after_an_approval = await decide_two_purchases(tmp_path / "after_an_approval", [18.0, 18.0])
        assert [record["decision"] for record in records_after_an_approval] == ["approve", "step_up"]
        assert [record["reason_codes"] for record in records_after_an_approval] == [[], ["DUPLICATE_SUSPECTED"]]
        assert "3 hours ago for CHF 18.00" in records_after_an_approval[1]["customer_message"]

        records_after_a_decline = await decide_two_purchases(tmp_path / "after_a_decline", [22.01, 20.0])
        assert [record["decision"] for record in records_after_a_decline] == ["decline", "approve"]
        assert [record["status"] for record in records_after_a_decline] == ["declined", "approved"]
        assert records_after_a_decline[1]["reason_codes"] == []
    run_async(scenario())









#### Step 3: Check the approval ####

# Check that approving the third purchase succeeds, and that approving the fourth then goes through as well, with the new total
# shown on the open question before and kept with the answer, because the customer always decides and is never refused
def test_second_approval_goes_through_with_a_warning_once_the_first_used_the_room(tmp_path):
    async def scenario():
        platform = build_test_platform({SCENARIO_NAME: build_timed_scenario(RUN_AMOUNTS)})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, SCENARIO_NAME, instruction = BUDGET_INSTRUCTION)
        records = await decide_next_purchases(service, 4)
        third_identifier = records[2]["live_authorization_id"]
        fourth_identifier = records[3]["live_authorization_id"]



        # Expect no warning on either open question while nothing was approved since they were asked
        assert [record["approval_warning"] for record in service.list_pending()] == [None, None]



        # Approve the stated overshoot of the third purchase, which the customer was told about
        resolved_record = await service.resolve(third_identifier, "approve")
        assert resolved_record["status"] == "approved"
        assert resolved_record["resolution"]["budget_warning"] is None
        assert platform.authorizations[third_identifier].status == "approved"



        # Expect the open fourth question to warn about the total of both now
        [fourth_pending] = service.list_pending()
        assert fourth_pending["live_authorization_id"] == fourth_identifier
        assert "CHF 67.00" in fourth_pending["approval_warning"]
        assert "CHF 50.00" in fourth_pending["approval_warning"]
        assert "Nothing was approved" not in fourth_pending["approval_warning"]



        # Expect the approval of the fourth to go through, sent to the platform, with the warning kept with the answer
        approved_record = await service.resolve(fourth_identifier, "approve")
        assert approved_record["status"] == "approved"
        assert "CHF 67.00" in approved_record["resolution"]["budget_warning"]
        assert service.store.get_resolution(fourth_identifier)["decision"] == "approve"
        assert platform.authorizations[fourth_identifier].status == "approved"
        assert service.list_pending() == []
        await service.client.close()
    run_async(scenario())



# Check the reverse order, where the later question is approved first and the earlier one then carries the warning,
# because the two share a period although the later purchase lies after the earlier one in simulated time. A decline carries no warning.
def test_earlier_question_carries_the_warning_once_the_later_one_used_the_room(tmp_path):
    async def scenario():
        platform = build_test_platform({SCENARIO_NAME: build_timed_scenario(RUN_AMOUNTS)})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, SCENARIO_NAME, instruction = BUDGET_INSTRUCTION)
        records = await decide_next_purchases(service, 4)
        third_identifier = records[2]["live_authorization_id"]
        fourth_identifier = records[3]["live_authorization_id"]



        # Approve the fourth purchase first, whose stated total of CHF 52.00 still holds
        resolved_record = await service.resolve(fourth_identifier, "approve")
        assert resolved_record["status"] == "approved"
        assert resolved_record["resolution"]["budget_warning"] is None



        # Expect the open third question to warn about the total of both, and its decline to go through without a warning
        [third_pending] = service.list_pending()
        assert third_pending["live_authorization_id"] == third_identifier
        assert "CHF 67.00" in third_pending["approval_warning"]
        declined_record = await service.resolve(third_identifier, "decline")
        assert declined_record["status"] == "declined"
        assert declined_record["resolution"]["budget_warning"] is None
        assert platform.authorizations[third_identifier].status == "declined"
        await service.client.close()
    run_async(scenario())









#### Step 4: Check the second run ####

# Check that a second run of the same scenario under the same mandate starts with an empty period, although it replays the same simulated dates
def test_second_run_starts_with_an_empty_period(tmp_path):
    async def scenario():
        platform = build_test_platform({SCENARIO_NAME: build_timed_scenario(RUN_AMOUNTS)})
        service = build_test_service(platform, tmp_path)
        mandate, first_run = await confirm_and_start(service, SCENARIO_NAME, instruction = BUDGET_INSTRUCTION)
        first_records = await decide_next_purchases(service, 4)
        await service.resolve(first_records[2]["live_authorization_id"], "approve")



        # Start the second run and expect its first two purchases to see only each other
        second_run = await service.start_run(SCENARIO_NAME, mandate["mandate_id"])
        second_records = await decide_next_purchases(service, 2)
        assert second_run["run_id"] != first_run["run_id"]
        assert {record["run_id"] for record in second_records} == {second_run["run_id"]}
        assert {record["mandate_id"] for record in first_records + second_records} == {mandate["mandate_id"]}
        assert [record["decision"] for record in second_records] == ["approve", "approve"]
        assert [read_budget_evidence(record, "period_spent_before_chf") for record in second_records] == [0.0, 20.0]
        assert [read_budget_evidence(record, "period_total_chf") for record in second_records] == [20.0, 38.0]
        await service.client.close()
    run_async(scenario())
