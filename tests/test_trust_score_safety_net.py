# Script: test_trust_score_safety_net.py
# Purpose: Check that a trust score that cannot be computed or sent never costs an answer, and that nothing changes when the score works
# Author: Andrés Gallardo
# Date: September 2026

from app.api_client.leash import LeashApiError
from app.models.decision import DecisionTrace
from app.models.events import read_purchase_message
from app.worker import loop as worker_loop
from platform_helpers import (
    FakeClock,
    build_test_platform,
    build_test_scenario,
    build_test_service,
    confirm_and_start,
    fetch_envelope,
    run_async,
)



# State a rule that stores the limit of the instruction with the platform
LIMIT_RULE = {"field": "authorization.billing_amount_chf", "operator": "<=", "value": 20, "currency": "CHF", "scope": "purchase"}
ASKING_INSTRUCTION = "Buy groceries. Ask me when uncertain."



# State a text that stands in for shop text inside an exception, which must never reach the worker's error note
SHOP_TEXT = "Bio Laden Zurich"



# Fail at once, standing in for a broken trust score, whatever it is called with
def broken_score_function(*arguments, **named_arguments):
    raise RuntimeError("score exploded on " + SHOP_TEXT)



# Read the trust evidence items out of the answer the platform recorded
def trust_facts_of(platform, live_authorization_id):
    evidence = platform.authorizations[live_authorization_id].decision["evidence"]
    return {item["fact"]: item["value"] for item in evidence if item["fact"].startswith("trust_")}



# Read everything of the answer the platform recorded except the trust evidence, which is what must never change
def answer_without_trust_of(platform, live_authorization_id):
    answer = platform.authorizations[live_authorization_id].decision
    return {
        "decision": answer["decision"],
        "reason_codes": answer["reason_codes"],
        "customer_message": answer["customer_message"],
        "guard_evidence": [item for item in answer["evidence"] if not item["fact"].startswith("trust_")],
    }



# Decide the one purchase of a scenario on a fresh platform under a fixed clock, and return the platform, the service and the record
async def decide_one_purchase(tmp_path, amount, folder_name, instruction = None, hard_rules = ()):
    clock = FakeClock()
    platform = build_test_platform({"SCEN0000": build_test_scenario([amount])}, clock = clock)
    audit_folder = tmp_path / folder_name
    audit_folder.mkdir()
    service = build_test_service(platform, audit_folder, clock = clock)
    if instruction is None:
        await confirm_and_start(service, "SCEN0000")
    else:
        await confirm_and_start(service, "SCEN0000", instruction = instruction, hard_rules = list(hard_rules))
    record = await service.worker.handle_envelope(await fetch_envelope(service))
    return platform, service, record



# Check that the worker's error note names the type of the exception and carries none of its text
def assert_note_names_the_type_only(service):
    assert "RuntimeError" in service.worker.last_error
    assert SHOP_TEXT not in service.worker.last_error and "exploded" not in service.worker.last_error
    assert service.worker.last_error_at is not None









#### Step 1: The score cannot be computed ####

# An ordinary purchase is still approved with its usual answer, recorded without a score and answered without score evidence
def test_approval_survives_a_score_that_cannot_be_computed(tmp_path, monkeypatch):
    async def scenario():
        usual_platform, usual_service, usual_record = await decide_one_purchase(tmp_path, 20.0, "usual")
        usual_answer = answer_without_trust_of(usual_platform, usual_record["live_authorization_id"])
        await usual_service.client.close()



        # Break the score and decide the same purchase again on a fresh platform
        monkeypatch.setattr(worker_loop, "score_trace", broken_score_function)
        platform, service, record = await decide_one_purchase(tmp_path, 20.0, "broken")
        assert record["decision"] == "approve" and record["status"] == "approved"
        assert record["posted"] is True and record["post_status_code"] == 200
        assert record["customer_message"] == usual_record["customer_message"]
        assert record["trust_score"] is None
        assert service.store.get_decision(record["live_authorization_id"])["trust_score"] is None
        assert platform.authorizations[record["live_authorization_id"]].status == "approved"
        assert trust_facts_of(platform, record["live_authorization_id"]) == {}
        assert answer_without_trust_of(platform, record["live_authorization_id"]) == usual_answer
        assert_note_names_the_type_only(service)
        assert service.worker.counters["decisions"] == 1 and service.worker.counters["post_failures"] == 0
        await service.client.close()
    run_async(scenario())



# A purchase that asks stays pending with its reason codes, recorded without a score and answered without score evidence
def test_question_survives_a_score_that_cannot_be_computed(tmp_path, monkeypatch):
    async def scenario():
        usual_platform, usual_service, usual_record = await decide_one_purchase(tmp_path, 21.5, "usual", instruction = ASKING_INSTRUCTION, hard_rules = [LIMIT_RULE])
        usual_answer = answer_without_trust_of(usual_platform, usual_record["live_authorization_id"])
        await usual_service.client.close()



        # Break the score and decide the same purchase again on a fresh platform
        monkeypatch.setattr(worker_loop, "score_trace", broken_score_function)
        platform, service, record = await decide_one_purchase(tmp_path, 21.5, "broken", instruction = ASKING_INSTRUCTION, hard_rules = [LIMIT_RULE])
        assert record["decision"] == "step_up" and record["reason_codes"] == ["SMALL_OVERSHOOT"]
        assert record["status"] == "pending" and record["human_deadline_at"] is not None
        assert record["posted"] is True
        assert record["trust_score"] is None
        assert platform.authorizations[record["live_authorization_id"]].status == "pending"
        assert trust_facts_of(platform, record["live_authorization_id"]) == {}
        assert answer_without_trust_of(platform, record["live_authorization_id"]) == usual_answer
        assert [pending["live_authorization_id"] for pending in service.store.list_pending()] == [record["live_authorization_id"]]
        assert_note_names_the_type_only(service)
        await service.client.close()
    run_async(scenario())



# The record builder computes the score itself when none is handed in, and leaves the record without one when that fails
def test_record_builder_survives_a_score_that_cannot_be_computed(tmp_path, monkeypatch):
    async def scenario():
        clock = FakeClock()
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])}, clock = clock)
        service = build_test_service(platform, tmp_path, clock = clock)
        await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)
        usual_record = await service.worker.handle_envelope(envelope)
        await service.client.close()



        # Rebuild the record from its own trace without a score, first with the score working and then with it broken
        trace = DecisionTrace.model_validate(usual_record["trace"])
        event = read_purchase_message(envelope["data"])
        rebuilt = worker_loop.build_record_from_trace(trace, usual_record["run_id"], event, "offline", 120, clock.read())
        assert rebuilt["trust_score"] == usual_record["trust_score"]
        monkeypatch.setattr(worker_loop, "score_trace", broken_score_function)
        rebuilt = worker_loop.build_record_from_trace(trace, usual_record["run_id"], event, "offline", 120, clock.read())
        assert rebuilt["trust_score"] is None
        assert rebuilt["decision"] == "approve" and rebuilt["customer_message"] == usual_record["customer_message"]
    run_async(scenario())









#### Step 2: The evidence items cannot be built ####

# The answer goes out without score evidence, and the stored record keeps its score
def test_answer_goes_out_when_the_evidence_cannot_be_built(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(worker_loop, "build_trust_evidence", broken_score_function)
        platform, service, record = await decide_one_purchase(tmp_path, 20.0, "broken")
        assert record["decision"] == "approve" and record["posted"] is True
        assert platform.authorizations[record["live_authorization_id"]].status == "approved"
        assert trust_facts_of(platform, record["live_authorization_id"]) == {}
        stored = service.store.get_decision(record["live_authorization_id"])
        assert stored["trust_score"]["score"] == 100 and stored["trust_score"]["band"] == "trusted"
        assert_note_names_the_type_only(service)
        await service.client.close()
    run_async(scenario())



# A repeated delivery whose stored score cannot be turned into evidence is still answered with the stored decision
def test_repeated_delivery_is_answered_when_the_evidence_cannot_be_built(tmp_path, monkeypatch):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        envelope = await fetch_envelope(service)



        # The platform refuses the first answer outright, so the purchase is decided and stored but not accepted
        real_post = service.client.post_decision
        async def refusing_post(authorization_id, body):
            raise LeashApiError(429, "/v1/authorizations/" + authorization_id + "/decision", {"error": {"code": "rate_limited", "message": "slow down"}})
        service.client.post_decision = refusing_post
        first = await service.worker.handle_envelope(envelope)
        assert first["posted"] is False and first["trust_score"]["score"] == 100



        # The evidence breaks before the second delivery, which is still answered from the store without deciding again
        service.client.post_decision = real_post
        monkeypatch.setattr(worker_loop, "build_trust_evidence", broken_score_function)
        second = await service.worker.handle_envelope(envelope)
        assert second["posted"] is True and second["delivery_count"] == 2
        assert service.worker.counters["decisions"] == 1 and service.worker.counters["redeliveries"] == 1
        answer = platform.authorizations[second["live_authorization_id"]].decision
        assert answer["decision"] == "approve" and answer["customer_message"] == first["customer_message"]
        assert trust_facts_of(platform, second["live_authorization_id"]) == {}
        assert second["trust_score"] == first["trust_score"]
        assert_note_names_the_type_only(service)
        await service.client.close()
    run_async(scenario())









#### Step 3: The message cannot be read ####

# A message that cannot be read is still answered with a question and INVALID_EVENT when its score cannot be built
def test_invalid_message_is_answered_when_its_score_cannot_be_built(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(worker_loop, "build_invalid_event_score", broken_score_function)
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0], invalid_positions = (1,))})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["decision"] == "step_up" and record["reason_codes"] == ["INVALID_EVENT"]
        assert record["status"] == "pending" and record["posted"] is True
        assert record["trust_score"] is None and record["trace"] is None
        answer = platform.authorizations[record["live_authorization_id"]].decision
        assert answer["decision"] == "step_up" and answer["reason_codes"] == ["INVALID_EVENT"]
        assert answer["customer_message"] == worker_loop.INVALID_EVENT_MESSAGE and answer["evidence"] == []
        assert platform.authorizations[record["live_authorization_id"]].status == "pending"
        assert service.worker.counters["invalid_events"] == 1
        assert_note_names_the_type_only(service)
        await service.client.close()
    run_async(scenario())









#### Step 4: Nothing fails ####

# Without any failure the record and the answer carry the score as before, and the worker notes no error
def test_nothing_changes_when_the_score_works(tmp_path):
    async def scenario():
        platform, service, record = await decide_one_purchase(tmp_path, 20.0, "approval")
        trust = record["trust_score"]
        assert trust["score"] == 100 and trust["band"] == "trusted" and trust["deductions"] == []
        assert trust["coverage"]["checks_total"] == 23 and trust["basis"]["uncertainty_policy"] == "ask"
        assert trust_facts_of(platform, record["live_authorization_id"]) == {"trust_score": 100, "trust_band": "trusted", "trust_deductions": "none"}
        assert service.worker.last_error is None
        await service.client.close()



        # A question scores 60 and names the finding that decided it, in the record and in the answer
        platform, service, record = await decide_one_purchase(tmp_path, 21.5, "question", instruction = ASKING_INSTRUCTION, hard_rules = [LIMIT_RULE])
        trust = record["trust_score"]
        assert trust["score"] == 60 and trust["band"] == "review"
        assert trust["deductions"][0]["guard_id"] == "per_order_limit" and trust["deductions"][0]["reason_code"] == "SMALL_OVERSHOOT"
        facts = trust_facts_of(platform, record["live_authorization_id"])
        assert facts == {"trust_score": 60, "trust_band": "review", "trust_deductions": "per_order_limit SMALL_OVERSHOOT -40"}
        assert service.worker.last_error is None
        await service.client.close()
    run_async(scenario())



# Without any failure a message that cannot be read is recorded and answered as not assessed, as before
def test_nothing_changes_for_a_message_that_cannot_be_read(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0], invalid_positions = (1,))})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        trust = record["trust_score"]
        assert trust["score"] is None and trust["band"] == "not_assessed"
        assert trust["summary"] == "The purchase message could not be read, so no check ran."
        facts = trust_facts_of(platform, record["live_authorization_id"])
        assert facts["trust_score"] is None and facts["trust_band"] == "not_assessed"
        assert service.worker.last_error is None
        await service.client.close()
    run_async(scenario())
