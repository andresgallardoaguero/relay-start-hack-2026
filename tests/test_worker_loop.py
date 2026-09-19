# Script: test_worker_loop.py
# Purpose: Check that the worker decides every delivered purchase once inside its deadline, never approves when the engine is late, and keeps a question open for the customer
# Author: Jonas Lüthi
# Date: September 2026

import asyncio
import time

import httpx
import pytest

from app.api_client.leash import LeashApiError
from app.engine.pipeline import decide
from app.llm.schemas import ExtractedFacts, ExtractedLine, ModelCallRecord, ModelExtraction
from app.service import ServiceError
from platform_helpers import (
    FakeClock,
    build_test_platform,
    build_test_scenario,
    build_test_service,
    build_test_settings,
    confirm_and_start,
    fetch_envelope,
    run_async,
)



# State a rule that stores the limit of the instruction with the platform
LIMIT_RULE = {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 20, "currency": "CHF", "scope": "purchase"}



class StubTransport:
    provider = "test-provider"
    model = "test-model"



class SuccessfulExtractor:
    transport = StubTransport()

    def __init__(self, clock = None):
        self.clock = clock

    async def extract(self, event):
        if self.clock is not None:
            self.clock.advance(2)
        return ModelExtraction(
            facts = ExtractedFacts(
                lines = tuple(ExtractedLine(line_no = item.line_no, product_kind = "grocery") for item in event.authorization.items),
                injection_suspected = False,
            ),
            call = ModelCallRecord(
                purpose = "fact_extraction", status = "success", provider = "test-provider", model = "test-model", elapsed_ms = 1,
            ),
        )



class BrokenExtractor:
    transport = StubTransport()

    async def extract(self, event):
        raise RuntimeError("model unavailable")



# The optional extraction is passed to the engine, and its elapsed time comes out of the engine budget rather than the post reserve
def test_model_extraction_is_passed_inside_the_engine_budget(tmp_path, monkeypatch):
    async def scenario():
        clock = FakeClock()
        seen = {}

        def recording_decide(event, policy, state, extracted_facts = None):
            seen["extraction"] = extracted_facts
            return decide(event, policy, state)

        original_wait_for = asyncio.wait_for

        async def recording_wait_for(awaitable, timeout):
            seen["timeout"] = timeout
            return await original_wait_for(awaitable, timeout)

        monkeypatch.setattr(asyncio, "wait_for", recording_wait_for)
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])}, clock = clock)
        service = build_test_service(
            platform, tmp_path, clock = clock, decide_function = recording_decide,
            fact_extractor = SuccessfulExtractor(clock),
        )
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["decision"] == "approve"
        assert seen["extraction"].call.status == "success"
        assert seen["extraction"].facts.lines[0].product_kind == "grocery"
        assert seen["timeout"] == pytest.approx(4.0)
        await service.client.close()

    run_async(scenario())



# An unexpected extractor failure becomes degraded model evidence and never becomes the engine's GUARD_ERROR fallback
def test_extractor_error_is_contained_before_the_engine(tmp_path):
    async def scenario():
        seen = {}

        def recording_decide(event, policy, state, extracted_facts = None):
            seen["extraction"] = extracted_facts
            return decide(event, policy, state)

        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(
            platform, tmp_path, decide_function = recording_decide, fact_extractor = BrokenExtractor(),
        )
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["decision"] == "approve" and "GUARD_ERROR" not in record["reason_codes"]
        assert seen["extraction"].facts is None
        assert seen["extraction"].call.status == "provider_error"
        assert seen["extraction"].call.error_type == "RuntimeError"
        await service.client.close()

    run_async(scenario())









#### Step 1: Ordinary purchases ####

# One purchase inside the limit is approved, answered, recorded once, written to the audit file and published
def test_ordinary_purchase_is_decided_posted_recorded_and_published(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        queue = service.broadcaster.subscribe()
        mandate, run = await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)
        record = await service.worker.handle_envelope(envelope)



        # The record is complete, accepted by the platform and carries every guard
        assert record["decision"] == "approve" and record["status"] == "approved"
        assert record["posted"] is True and record["post_status_code"] == 200
        assert record["amount_chf"] == "20.00" and record["delivery_count"] == 1
        assert record["margin_ms"] > 0 and len(record["trace"]["guards"]) == 23
        assert platform.authorizations[record["live_authorization_id"]].status == "approved"



        # The audit file and the event stream carry the decision
        audit_lines = service.audit_log.read_run(run["run_id"])
        assert [line["kind"] for line in audit_lines] == ["run_started", "decision"]
        event_names = []
        while not queue.empty():
            event_names.append(queue.get_nowait()[0])
        assert "decision" in event_names and "run" in event_names
        assert service.worker.counters["decisions"] == 1
        await service.client.close()
    run_async(scenario())



# The policy comes from the mandate inside the message, so a rule stored with the platform limits the purchase
def test_policy_is_built_from_the_rules_in_the_message(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([21.5])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000", instruction = "Buy groceries. Ask me when uncertain.", hard_rules = [LIMIT_RULE])
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["decision"] == "step_up" and record["reason_codes"] == ["SMALL_OVERSHOOT"]
        assert record["status"] == "pending" and record["human_deadline_at"] is not None
        await service.client.close()
    run_async(scenario())









#### Step 2: Repeated deliveries ####

# The same purchase delivered again is decided once, counted once and not answered a second time
def test_repeated_delivery_is_decided_once_and_counted_once(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)
        first = await service.worker.handle_envelope(envelope)
        second = await service.worker.handle_envelope(envelope)
        assert first["live_authorization_id"] == second["live_authorization_id"]
        assert second["delivery_count"] == 2
        assert len(service.store.list_decisions()) == 1
        assert service.worker.counters["decisions"] == 1 and service.worker.counters["redeliveries"] == 1
        assert service.worker.counters["post_failures"] == 0
        await service.client.close()
    run_async(scenario())



# An answer the platform fails to take is sent again until it is accepted, inside the same delivery
def test_answer_is_retried_until_the_platform_accepts_it(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)



        # The first two attempts fail with a server error, the third goes through
        real_post = service.client.post_decision
        attempts = []
        async def flaky_post(authorization_id, body):
            attempts.append(body["decision"])
            if len(attempts) < 3:
                raise LeashApiError(503, "/v1/authorizations/" + authorization_id + "/decision", {"error": {"code": "unavailable", "message": "try later"}})
            return await real_post(authorization_id, body)
        service.client.post_decision = flaky_post
        record = await service.worker.handle_envelope(envelope)
        assert record["posted"] is True and attempts == ["approve", "approve", "approve"]
        assert service.worker.counters["decisions"] == 1 and service.worker.counters["post_failures"] == 0
        await service.client.close()
    run_async(scenario())



# When the platform refused the answer, the repeated delivery sends the stored answer again without deciding again
def test_redelivery_resends_an_answer_the_platform_refused(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)



        # The platform refuses the first answer outright, which is not retried
        real_post = service.client.post_decision
        async def refusing_post(authorization_id, body):
            raise LeashApiError(429, "/v1/authorizations/" + authorization_id + "/decision", {"error": {"code": "rate_limited", "message": "slow down"}})
        service.client.post_decision = refusing_post
        first = await service.worker.handle_envelope(envelope)
        assert first["decision"] == "approve" and first["posted"] is False and first["post_status_code"] == 429
        assert service.worker.counters["post_failures"] == 1



        # The platform accepts the second delivery's answer, and the decision was still made only once
        service.client.post_decision = real_post
        second = await service.worker.handle_envelope(envelope)
        assert second["posted"] is True and second["delivery_count"] == 2
        assert service.worker.counters["decisions"] == 1
        assert platform.authorizations[second["live_authorization_id"]].decision["decision"] == "approve"
        await service.client.close()
    run_async(scenario())



# An answer that no attempt gets through before the deadline is recorded as not accepted, and the amount is counted once
def test_answer_not_accepted_before_the_deadline_is_recorded(tmp_path):
    async def scenario():
        clock = FakeClock()
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])}, clock = clock)
        service = build_test_service(platform, tmp_path, clock = clock)
        await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)
        async def unreachable_post(authorization_id, body):
            clock.advance(3)
            raise httpx.ConnectError("no route")
        service.client.post_decision = unreachable_post
        record = await service.worker.handle_envelope(envelope)
        assert record["decision"] == "approve" and record["posted"] is False
        assert "deadline" in record["post_error"] and record["post_status_code"] is None
        assert len(service.store.list_decisions()) == 1 and service.worker.counters["post_failures"] == 1
        await service.client.close()
    run_async(scenario())









#### Step 3: Messages that cannot be read ####

# A message that breaks the format is answered with a question and the reason INVALID_EVENT, never with silence
def test_invalid_message_is_answered_with_a_question(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0], invalid_positions = (1,))})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["decision"] == "step_up" and record["reason_codes"] == ["INVALID_EVENT"]
        assert record["status"] == "pending" and record["trace"] is None
        assert any("billing_amount_chf" in problem for problem in record["problems"])
        assert record["posted"] is True
        assert platform.authorizations[record["live_authorization_id"]].status == "pending"
        assert service.worker.counters["invalid_events"] == 1
        await service.client.close()
    run_async(scenario())



# A delivery without a live identifier is recorded as an error and does not stop the worker
def test_delivery_without_an_identifier_is_recorded_as_an_error(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        assert await service.worker.handle_envelope({"run_id": "run_x", "data": {"type": "authorization.request"}}) is None
        assert "no live authorization identifier" in service.worker.last_error
        await service.client.close()
    run_async(scenario())









#### Step 4: The engine is late or fails ####

# Sleep longer than any budget, standing in for a slow engine
def slow_decide(event, policy, state):
    time.sleep(3.0)
    raise AssertionError("The slow engine must never finish inside the budget")



# Fail at once, standing in for a broken engine
def broken_decide(event, policy, state):
    raise RuntimeError("guards exploded")



# A slow engine gives a question inside the budget under every uncertainty policy except decline, which gives a refusal, and never an approval
@pytest.mark.parametrize("uncertainty_policy, expected_decision", [("ask", "step_up"), ("approve", "step_up"), ("decline", "decline")])
def test_slow_engine_never_approves(tmp_path, uncertainty_policy, expected_decision):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        settings = build_test_settings(engine_budget_ms = 300)
        service = build_test_service(platform, tmp_path, settings = settings, decide_function = slow_decide)
        await confirm_and_start(service, "SCEN0000", uncertainty_policy = uncertainty_policy)
        started = time.perf_counter()
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert time.perf_counter() - started < 2.0
        assert record["decision"] == expected_decision
        assert record["reason_codes"] == ["ENGINE_TIMEOUT_FALLBACK"]
        assert record["posted"] is True and record["trace"]["guards"] == []
        assert service.worker.counters["fallbacks"] == 1
        await service.client.close()
    run_async(scenario())



# A broken engine gives a question with the reason GUARD_ERROR, and never an approval
def test_broken_engine_falls_back_to_a_question(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path, decide_function = broken_decide)
        await confirm_and_start(service, "SCEN0000", uncertainty_policy = "approve")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["decision"] == "step_up" and record["reason_codes"] == ["GUARD_ERROR"]
        await service.client.close()
    run_async(scenario())



# A purchase that arrives with most of its deadline gone is still answered, with the fallback when the engine cannot fit
def test_late_arrival_is_answered_with_what_is_left(tmp_path):
    async def scenario():
        clock = FakeClock()
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])}, clock = clock)
        def engine_that_takes_a_moment(event, policy, state):
            time.sleep(0.4)
            raise AssertionError("must not finish")
        service = build_test_service(platform, tmp_path, clock = clock, decide_function = engine_that_takes_a_moment)
        await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)



        # Seven seconds of the eight pass before the worker receives the purchase
        clock.advance(7)
        record = await service.worker.handle_envelope(envelope)
        assert record["decision"] == "step_up" and record["reason_codes"] == ["ENGINE_TIMEOUT_FALLBACK"]
        assert record["posted"] is True
        await service.client.close()
    run_async(scenario())









#### Step 5: Questions and the customer's answer ####

# A question stays open, an approval by the customer reaches the platform, and a second answer is refused
def test_step_up_stays_pending_until_the_customer_answers(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([21.5])})
        service = build_test_service(platform, tmp_path)
        queue = service.broadcaster.subscribe()
        mandate, run = await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["status"] == "pending"
        assert [pending["live_authorization_id"] for pending in service.store.list_pending()] == [record["live_authorization_id"]]



        # The customer approves
        resolved = await service.resolve(record["live_authorization_id"], "approve")
        assert resolved["status"] == "approved"
        assert resolved["resolution"]["decision"] == "approve" and resolved["resolution"]["posted"] is True
        assert platform.authorizations[record["live_authorization_id"]].status == "approved"
        assert service.store.list_pending() == []
        assert [line["kind"] for line in service.audit_log.read_run(run["run_id"])] == ["run_started", "decision", "resolution"]
        event_names = []
        while not queue.empty():
            event_names.append(queue.get_nowait()[0])
        assert event_names.count("resolution") == 1



        # A second answer is refused
        with pytest.raises(ServiceError) as refusal:
            await service.resolve(record["live_authorization_id"], "decline")
        assert refusal.value.status_code == 409
        await service.client.close()
    run_async(scenario())



# A refusal by the customer reaches the platform as a decline
def test_customer_can_decline_a_question(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([21.5])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        resolved = await service.resolve(record["live_authorization_id"], "decline", "Too expensive")
        assert resolved["status"] == "declined"
        assert platform.authorizations[record["live_authorization_id"]].resolution["customer_message"] == "Too expensive"
        await service.client.close()
    run_async(scenario())



# A question the platform refuses to resolve stays open, and the refusal is kept
def test_question_stays_open_when_the_platform_refuses_the_answer(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([21.5])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        platform.authorizations[record["live_authorization_id"]].status = "cancelled"
        with pytest.raises(ServiceError) as refusal:
            await service.resolve(record["live_authorization_id"], "approve")
        assert refusal.value.status_code == 409
        assert service.store.get_decision(record["live_authorization_id"])["status"] == "pending"
        assert service.store.get_resolution(record["live_authorization_id"])["posted"] is False
        await service.client.close()
    run_async(scenario())



# A question nobody answers expires after the platform's human window and counts as not approved
def test_question_expires_without_an_answer(tmp_path):
    async def scenario():
        clock = FakeClock()
        platform = build_test_platform({"SCEN0000": build_test_scenario([21.5])}, clock = clock, settings = {"human_timeout_seconds": 60})
        service = build_test_service(platform, tmp_path, clock = clock)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["human_deadline_at"] == "2026-09-19T08:01:00+00:00"
        clock.advance(59)
        service.worker.expire_old_questions()
        assert service.store.get_decision(record["live_authorization_id"])["status"] == "pending"
        clock.advance(1)
        service.worker.expire_old_questions()
        assert service.store.get_decision(record["live_authorization_id"])["status"] == "expired"
        with pytest.raises(ServiceError):
            await service.resolve(record["live_authorization_id"], "approve")
        await service.client.close()
    run_async(scenario())









#### Step 6: The loop ####

# Polling handles the purchases of a run in order and goes idle once the platform reports the run as over
def test_polling_handles_a_run_and_goes_idle_when_it_is_over(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0001": build_test_scenario([20.0, 21.5, 30.0])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0001")
        assert service.worker.polling is True
        for poll_number in range(12):
            if not service.worker.polling:
                break
            await service.worker.poll_once(wait_seconds = 1)
        assert service.worker.polling is False
        decisions = [record["decision"] for record in service.store.list_decisions()]
        assert decisions == ["approve", "step_up", "decline"]
        assert [record["replay_order"] for record in service.store.list_decisions()] == [1, 2, 3]
        assert service.store.list_runs()[0]["status"] == "completed"
        await service.client.close()
    run_async(scenario())



# A failed poll is recorded and the loop carries on
def test_failed_poll_is_recorded_and_the_loop_carries_on(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        service.client.team_api_key = "wrong"
        service.worker.start_polling()
        await service.worker.poll_once(wait_seconds = 1)
        assert service.worker.counters["poll_errors"] == 1
        assert "401" in service.worker.last_error
        assert service.worker.polling is True
        await service.client.close()
    run_async(scenario())



# The worker task waits while nobody asks for polling, wakes up when a run starts and can be stopped
def test_worker_task_wakes_up_and_stops(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        service.worker.start_task()
        await asyncio.sleep(0.05)
        assert service.worker.counters["polls"] == 0
        await confirm_and_start(service, "SCEN0000")
        for wait_number in range(40):
            if service.store.list_decisions():
                break
            await asyncio.sleep(0.1)
        assert len(service.store.list_decisions()) == 1
        await service.worker.stop_task()
        assert service.worker.task is None
        await service.client.close()
    run_async(scenario())



# The platform settings are read from the bootstrap answer
def test_platform_settings_are_read_from_bootstrap(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])}, settings = {"human_timeout_seconds": 45, "judging_mode": True})
        service = build_test_service(platform, tmp_path)
        await service.worker.load_platform_settings()
        assert service.worker.human_timeout_seconds == 45
        assert service.worker.platform_settings["judging_mode"] is True
        assert [scenario["scenario_id"] for scenario in service.worker.platform_settings["scenarios"]] == ["SCEN0000"]
        await service.client.close()
    run_async(scenario())
