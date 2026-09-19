# Script: platform_helpers.py
# Purpose: Build a small in-process platform from the example message for the tests of the client, the worker and the web API
# Author: Jonas Lüthi
# Date: September 2026

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from app.api_client.fake_platform import FakePlatform
from app.api_client.leash import LeashClient
from app.config import Settings
from app.service import RelayService
from app.state.audit import AuditLog
from app.state.store import DecisionStore
from app.webapi.sse import EventBroadcaster
from app.worker.loop import DecisionWorker









#### Step 1: Define the clock and the example message ####

# Locate the example message
EXAMPLE_MESSAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_fixtures" / "example_authorization_request.json"



# State the instruction the tests use, whose limit the CHF 20 of the example meets exactly
TEST_INSTRUCTION = "Buy one ordinary grocery item for CHF 20 or less. Ask me when uncertain."



# Tell a fixed time that a test moves forward by hand
class FakeClock:

    def __init__(self, start = None):
        self.now = start if start is not None else datetime(2026, 9, 19, 8, 0, 0, tzinfo = timezone.utc)

    def read(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds = seconds)
        return self.now



# Write a moment the way the messages write it
def format_message_time(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")









#### Step 2: Build messages from attempts ####

# Build one live-shaped message from a test attempt, which names the amount, the sale time and whether the message is broken.
# The one cart line gets its own item identifier from the position of the attempt, unless the attempt states one under item_id,
# because the real purchases of a run do not all hold the identical cart.
def build_test_message(attempt, cart_lines, run_state, uncertainty_policy, live_authorization_id, received_at):
    message = json.loads(EXAMPLE_MESSAGE_PATH.read_text(encoding = "utf-8"))
    amount = float(attempt.get("billing_amount_chf", 20.0))
    delivery_fee = message["authorization"]["delivery_fee"]
    authorization = message["authorization"]
    authorization["authorization_id"] = live_authorization_id
    authorization["source_authorization_id"] = attempt["authorization_id"]
    authorization["replay_order"] = int(attempt.get("replay_order", 1))
    authorization["mandate_id"] = run_state.mandate_id
    authorization["profile_id"] = run_state.profile_id
    authorization["timestamp"] = attempt.get("timestamp", authorization["timestamp"])
    authorization["amount"] = amount
    authorization["billing_amount_chf"] = amount
    authorization["items_subtotal"] = float(Decimal(str(amount)) - Decimal(str(delivery_fee)))
    authorization["items"][0]["unit_price"] = authorization["items_subtotal"]
    authorization["items"][0]["item_id"] = attempt.get("item_id", "IT_EXAMPLE_" + str(authorization["replay_order"]).zfill(4))
    authorization["recent_attempt_count_10m"] = len(run_state.earlier_purchases)
    message["request_id"] = "req_" + live_authorization_id
    message["deadline_at"] = format_message_time(received_at + timedelta(seconds = 8))
    message["runtime"]["received_at"] = format_message_time(received_at)
    message["mandate"]["mandate_id"] = run_state.mandate_id
    message["mandate"]["profile_id"] = run_state.profile_id
    message["mandate"]["uncertainty_policy"] = uncertainty_policy
    message["context"]["approved_spend_in_period_chf"] = float(sum(
        Decimal(str(earlier["billing_amount_chf"])) for earlier in run_state.earlier_purchases if earlier["status"] == "approved"
    ))
    message["context"]["recent_authorizations"] = [dict(earlier) for earlier in run_state.earlier_purchases]



    # Break the message on request, with an amount written as text, which the published schema and the reader both refuse
    if attempt.get("invalid"):
        authorization["billing_amount_chf"] = str(amount)
    return message



# Build the attempts of one test scenario
def build_test_scenario(amounts, invalid_positions = ()):
    return [
        {
            "attempt": {
                "authorization_id": "AU_TEST_" + str(position).zfill(4),
                "replay_order": position,
                "billing_amount_chf": amount,
                "invalid": position in invalid_positions,
            },
            "cart_lines": [],
        }
        for position, amount in enumerate(amounts, start = 1)
    ]









#### Step 3: Build the platform, the client and the worker ####

# Build a platform with fast long polls and the given scenarios
def build_test_platform(scenarios, clock = None, settings = None, team_api_key = "leash_test_key"):
    platform_settings = {"long_poll_max_wait_seconds": 0.2, **(settings or {})}
    return FakePlatform(
        scenarios = scenarios,
        message_builder = build_test_message,
        team_api_key = team_api_key,
        settings = platform_settings,
        read_clock = clock.read if clock is not None else None,
    )



# Build a client that reaches the platform inside the process
def build_test_client(platform, team_api_key = None):
    transport = httpx.ASGITransport(app = platform.app)
    http_client = httpx.AsyncClient(transport = transport, base_url = "http://test-platform")
    return LeashClient("http://test-platform", team_api_key if team_api_key is not None else platform.team_api_key, http_client = http_client)



# Build the settings of a test, with a short engine budget where asked
def build_test_settings(**overrides):
    values = {
        "team_api_key": "leash_test_key",
        "engine_budget_ms": 6000,
        "post_reserve_ms": 1500,
        "long_poll_wait_seconds": 1,
        "leash_mode": "live",
        "worker_autostart": False,
        "overshoot_tolerance_share": "0.10",
        **overrides,
    }
    return Settings(_env_file = None, **values)



# Build a service around a test platform, with the store in memory and the audit files in a temporary folder
def build_test_service(platform, audit_folder, settings = None, clock = None, decide_function = None, fact_extractor = None):
    if settings is None:
        settings = build_test_settings()
    store = DecisionStore(":memory:")
    audit_log = AuditLog(audit_folder)
    broadcaster = EventBroadcaster()
    client = build_test_client(platform)
    worker = DecisionWorker(
        client, store, audit_log, broadcaster, settings,
        read_clock = clock.read if clock is not None else None,
        decide_function = decide_function,
        source = "offline",
        fact_extractor = fact_extractor,
    )
    return RelayService(settings, store, client, audit_log, broadcaster, worker, platform)



# Confirm a mandate on the platform through the service and start a run of the scenario
async def confirm_and_start(service, scenario_id, instruction = TEST_INSTRUCTION, hard_rules = (), uncertainty_policy = "ask"):
    await service.refresh_bootstrap()
    draft = await service.create_draft(instruction, list(hard_rules), uncertainty_policy)
    mandate = await service.confirm_draft(draft["draft_id"])
    run = await service.start_run(scenario_id, mandate["mandate_id"])
    return mandate, run



# Fetch the next envelope from the platform through the client, or None
async def fetch_envelope(service, wait_seconds = 1):
    return await service.client.next_decision_request(wait_seconds)



# Run a coroutine to completion
def run_async(coroutine):
    return asyncio.run(coroutine)
