# Script: test_trust_in_records.py
# Purpose: Check that every decision record, every answer to the platform and the web API carry the trust score, and that the store keeps it
# Author: Jonas Lüthi
# Date: September 2026

import sqlite3
import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.state.store import TABLE_DEFINITIONS, DecisionStore
from platform_helpers import (
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



# Read the trust evidence items out of the answer the platform recorded
def trust_facts_of(platform, live_authorization_id):
    evidence = platform.authorizations[live_authorization_id].decision["evidence"]
    return {item["fact"]: item["value"] for item in evidence if item["fact"].startswith("trust_")}









#### Step 1: The record and the answer ####

# An ordinary purchase is recorded with full trust, and the answer to the platform carries the score as evidence
def test_record_and_answer_carry_full_trust_for_an_ordinary_purchase(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        trust = record["trust_score"]
        assert trust["score"] == 100 and trust["band"] == "trusted" and trust["deductions"] == []
        assert trust["coverage"]["checks_total"] == 23 and trust["basis"]["uncertainty_policy"] == "ask"
        facts = trust_facts_of(platform, record["live_authorization_id"])
        assert facts == {"trust_score": 100, "trust_band": "trusted", "trust_deductions": "none"}
        await service.client.close()
    run_async(scenario())



# A question scores 60 and names the finding that decided it, in the record and in the answer
def test_question_scores_sixty_and_names_the_finding(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([21.5])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000", instruction = "Buy groceries. Ask me when uncertain.", hard_rules = [LIMIT_RULE])
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["decision"] == "step_up"
        trust = record["trust_score"]
        assert trust["score"] == 60 and trust["band"] == "review"
        assert trust["deductions"][0]["guard_id"] == "per_order_limit" and trust["deductions"][0]["reason_code"] == "SMALL_OVERSHOOT"
        facts = trust_facts_of(platform, record["live_authorization_id"])
        assert facts["trust_score"] == 60 and facts["trust_band"] == "review"
        assert facts["trust_deductions"] == "per_order_limit SMALL_OVERSHOOT -40"
        await service.client.close()
    run_async(scenario())



# A message that could not be read is not assessed, and the answer says so
def test_invalid_message_is_not_assessed(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0], invalid_positions = (1,))})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["reason_codes"] == ["INVALID_EVENT"] and record["trace"] is None
        trust = record["trust_score"]
        assert trust["score"] is None and trust["band"] == "not_assessed"
        assert trust["summary"] == "The purchase message could not be read, so no check ran."
        facts = trust_facts_of(platform, record["live_authorization_id"])
        assert facts["trust_score"] is None and facts["trust_band"] == "not_assessed"
        await service.client.close()
    run_async(scenario())



# A purchase the engine could not decide in time is not assessed
def test_timeout_fallback_is_not_assessed(tmp_path):
    def slow_decide(event, policy, state):
        time.sleep(1.5)
        raise AssertionError("The slow engine must not be waited for")

    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        settings = build_test_settings(engine_budget_ms = 200, post_reserve_ms = 100)
        service = build_test_service(platform, tmp_path, settings = settings, decide_function = slow_decide)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["reason_codes"] == ["ENGINE_TIMEOUT_FALLBACK"] and record["decision"] == "step_up"
        trust = record["trust_score"]
        assert trust["score"] is None and trust["band"] == "not_assessed"
        assert trust["summary"] == "The checks did not finish in time, so nothing was assessed."
        await service.client.close()
    run_async(scenario())



# With the setting off, the score is still recorded and the answer to the platform leaves it out
def test_setting_leaves_the_score_out_of_the_answer(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        settings = build_test_settings(trust_score_in_evidence = False)
        service = build_test_service(platform, tmp_path, settings = settings)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        assert record["trust_score"]["score"] == 100
        assert trust_facts_of(platform, record["live_authorization_id"]) == {}
        await service.client.close()
    run_async(scenario())









#### Step 2: The store ####

# The store keeps the score with the record, and a store file from before the score gains the column on opening
def test_store_keeps_the_score_and_upgrades_an_older_file(tmp_path):
    async def scenario():
        platform = build_test_platform({"SCEN0000": build_test_scenario([20.0])})
        service = build_test_service(platform, tmp_path)
        await confirm_and_start(service, "SCEN0000")
        record = await service.worker.handle_envelope(await fetch_envelope(service))
        stored = service.store.get_decision(record["live_authorization_id"])
        assert stored["trust_score"] == record["trust_score"]
        assert service.store.list_decisions()[0]["trust_score"]["score"] == 100
        await service.client.close()
        return record
    record = run_async(scenario())



    # Build a file with the decisions table as it was before the score, without the trust column
    older_path = tmp_path / "older.sqlite3"
    older_definition = TABLE_DEFINITIONS[0].replace(",\n        trust_score_json TEXT", "")
    assert "trust_score_json" not in older_definition
    connection = sqlite3.connect(str(older_path))
    connection.execute(older_definition)
    connection.commit()
    connection.close()



    # Opening the older file adds the column, and a record with a score is saved and read back
    store = DecisionStore(older_path)
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(decisions)").fetchall()}
    assert "trust_score_json" in columns
    saved = store.save_decision({**record, "sequence": None})
    assert saved["trust_score"]["score"] == 100
    store.close()









#### Step 3: The web API ####

# The log, the inbox and the status carry the score to the screens
def test_web_api_serves_the_score(tmp_path):
    platform = build_test_platform({"SCEN0001": build_test_scenario([20.0, 21.5])})
    settings = build_test_settings()
    service = build_test_service(platform, tmp_path, settings = settings)
    app = create_app(service = service, settings = settings)
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["trust_score_version"] == "1" and status["trust_score_in_evidence"] is True
        draft = client.post("/api/policy/draft", json = {"instruction": "Buy groceries. Ask me when uncertain.", "hard_rules": [LIMIT_RULE], "uncertainty_policy": "ask"}).json()
        client.post("/api/policy/confirm", json = {"draft_id": draft["draft_id"]})
        client.post("/api/run/start", json = {"scenario_id": "SCEN0001"})
        started = time.perf_counter()
        decisions = []
        while time.perf_counter() - started < 5.0 and len(decisions) < 2:
            decisions = client.get("/api/decisions").json()["decisions"]
            time.sleep(0.05)
        assert [record["trust_score"]["score"] for record in decisions] == [100, 60]
        pending = client.get("/api/pending").json()["pending"]
        assert pending[0]["trust_score"]["band"] == "review"
        assert client.get("/api/decisions/" + pending[0]["live_authorization_id"]).json()["trust_score"]["label"] == "Needs your decision"
