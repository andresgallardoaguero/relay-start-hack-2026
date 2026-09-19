# Script: loop.py
# Purpose: Receive every proposed purchase from the platform, decide it once inside its deadline, answer it, record it and tell the interface
# Author: Jonas Lüthi
# Date: September 2026

import asyncio
import hashlib
import json
import time
import traceback
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal

import httpx

from app.api_client.leash import LeashApiError, find_value
from app.engine.pipeline import decide
from app.llm.schemas import ModelCallRecord, ModelExtraction
from app.models.decision import (
    ENGINE_VERSION,
    AggregationRecord,
    Decision,
    DecisionTrace,
    ReasonCode,
    TraceAmounts,
    TraceFacts,
    TraceIdentifiers,
    TraceLanguageModel,
    TraceMerchant,
    TraceTimings,
    build_decision_request_body,
)
from app.models.events import InvalidEventError, read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_ledger_snapshot_from_records, read_event_cart_lines
from app.trust.score import build_invalid_event_score, build_trust_evidence, score_trace
from app.worker.budget import DeadlineBudget, read_utc_clock









#### Step 1: Define the fixed values ####

# State the answer given when the message cannot be read, the pause between two attempts to send an answer, and the pauses after a failed poll
INVALID_EVENT_MESSAGE = "Please review this purchase. The purchase message could not be read, so nothing was approved."
POST_RETRY_PAUSE_SECONDS = 0.25
POLL_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 10.0)



# State the human answer window used until the platform reports its own
DEFAULT_HUMAN_TIMEOUT_SECONDS = 120



# State the words that mean a run is over in a progress answer
FINISHED_RUN_STATUSES = ("completed", "finished", "done", "cancelled", "failed", "expired")



# Write a moment as ISO 8601 text in UTC
def format_moment(moment):
    return moment.astimezone(timezone.utc).isoformat()



# Choose the answer of the engine when it could not decide in time or at all, which is a question and never an approval,
# and a refusal when the customer asked for refusals in doubt
def choose_fallback_decision(uncertainty_policy):
    if uncertainty_policy == "decline":
        return Decision.DECLINE
    return Decision.STEP_UP









#### Step 2: Build the records ####

# Compute the trust score of a trace, or none when the computation fails, because the score takes no part in the decision
# and a failing score must never cost an answer. The note names the type of the exception only, so no shop text reaches it.
def score_trace_safely(trace, uncertainty_policy, note_error = None):
    try:
        return score_trace(trace, uncertainty_policy)
    except Exception as score_error:
        if note_error is not None:
            note_error("The trust score could not be computed - " + type(score_error).__name__)
        return None



# Build the trust score of a message that could not be read, or none when that fails
def build_invalid_event_score_safely(uncertainty_policy, note_error = None):
    try:
        return build_invalid_event_score(uncertainty_policy)
    except Exception as score_error:
        if note_error is not None:
            note_error("The trust score of an unreadable message could not be built - " + type(score_error).__name__)
        return None



# Build the evidence items of a trust score, or none when there is no score or the items cannot be built
def build_trust_evidence_safely(trust_score, note_error = None):
    if trust_score is None:
        return []
    try:
        return build_trust_evidence(trust_score)
    except Exception as evidence_error:
        if note_error is not None:
            note_error("The trust score evidence could not be built - " + type(evidence_error).__name__)
        return []



# Build a decision record for a valid message that the engine could not decide, with an empty guard list and the engine named as the raiser
def build_fallback_trace(event, received_at, decided_at, reason_code, customer_message):
    fallback_decision = choose_fallback_decision(event.mandate.uncertainty_policy)
    return DecisionTrace(
        ids = TraceIdentifiers(
            authorization_id = event.authorization.authorization_id,
            source_authorization_id = event.authorization.source_authorization_id,
            request_id = event.request_id,
            mandate_id = event.authorization.mandate_id,
            scenario_id = event.authorization.scenario_id,
        ),
        received_at = received_at,
        decided_at = decided_at,
        deadline_at = event.deadline_at,
        margin_ms = (event.deadline_at - decided_at) / timedelta(milliseconds = 1),
        decision = fallback_decision,
        reason_codes = [reason_code],
        customer_message = customer_message,
        notes = [],
        facts = TraceFacts(
            amounts = TraceAmounts(
                amount = event.authorization.amount,
                currency = event.authorization.currency,
                billing_amount_chf = event.authorization.billing_amount_chf,
            ),
            merchant = TraceMerchant(
                merchant_id = event.authorization.merchant.merchant_id,
                merchant_name = event.authorization.merchant.merchant_name,
                merchant_category = event.authorization.merchant.merchant_category,
                merchant_country = event.authorization.merchant.merchant_country,
            ),
        ),
        guards = [],
        aggregation = AggregationRecord(
            initial = Decision.APPROVE,
            final = fallback_decision,
            raised_by = ["engine"],
            uncertainty_policy_applied = False,
        ),
        llm = TraceLanguageModel(),
        timings = TraceTimings(total_ms = (decided_at - received_at) / timedelta(milliseconds = 1)),
    )



# Read one text field of a raw message without trusting its shape
def read_raw_text(raw_message, *path, default = None):
    value = raw_message
    for path_part in path:
        if not isinstance(value, dict) or path_part not in value:
            return default
        value = value[path_part]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    return default



# Turn the status of a decision into the status of the purchase in the store
def status_for_decision(decision_value):
    return {"approve": "approved", "decline": "declined", "step_up": "pending"}[decision_value]



# Build the store record of a decided purchase from its trace, where the answer window of a question starts at the moment given.
# The trust score is derived from the trace under the uncertainty policy of the mandate, and computed here when the caller has none.
def build_record_from_trace(trace, run_id, event, source, human_timeout_seconds, window_starts_at, trust_score = None):
    decision_value = trace.decision.value
    human_deadline_at = None
    if decision_value == "step_up":
        human_deadline_at = format_moment(window_starts_at + timedelta(seconds = human_timeout_seconds))
    if trust_score is None:
        trust_score = score_trace_safely(trace, event.mandate.uncertainty_policy)
    return {
        "live_authorization_id": trace.ids.authorization_id,
        "run_id": run_id,
        "source_authorization_id": trace.ids.source_authorization_id,
        "scenario_id": trace.ids.scenario_id,
        "replay_order": event.authorization.replay_order,
        "mandate_id": trace.ids.mandate_id,
        "sim_timestamp": format_moment(event.authorization.timestamp),
        "amount_chf": format_money_text(event.authorization.billing_amount_chf),
        "currency": event.authorization.currency,
        "merchant_id": trace.facts.merchant.merchant_id,
        "merchant_name": trace.facts.merchant.merchant_name,
        "decision": decision_value,
        "status": status_for_decision(decision_value),
        "reason_codes": [reason_code.value for reason_code in trace.reason_codes],
        "customer_message": trace.customer_message,
        "notes": list(trace.notes),
        "received_at": format_moment(trace.received_at),
        "decided_at": format_moment(trace.decided_at),
        "deadline_at": format_moment(trace.deadline_at),
        "margin_ms": trace.margin_ms,
        "total_ms": trace.timings.total_ms,
        "posted": False,
        "delivery_count": 1,
        "human_deadline_at": human_deadline_at,
        "source": source,
        "trace": trace.model_dump(mode = "json"),
        "problems": None,
        "resolution": None,
        "trust_score": trust_score.model_dump(mode = "json") if trust_score is not None else None,
    }



# Write a message amount as money text with two decimals, built from the text of the number
def format_money_text(value):
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding = ROUND_HALF_EVEN))



# Build the store record of a message that could not be read, which is a question with the reason INVALID_EVENT
def build_invalid_event_record(live_authorization_id, run_id, raw_message, problems, received_at, decided_at, source, human_timeout_seconds, note_error = None):
    deadline_text = read_raw_text(raw_message, "deadline_at")
    trust_score = build_invalid_event_score_safely(read_raw_text(raw_message, "mandate", "uncertainty_policy", default = "unknown"), note_error)
    return {
        "live_authorization_id": live_authorization_id,
        "run_id": run_id,
        "source_authorization_id": read_raw_text(raw_message, "authorization", "source_authorization_id"),
        "scenario_id": read_raw_text(raw_message, "authorization", "scenario_id"),
        "replay_order": read_raw_text(raw_message, "authorization", "replay_order"),
        "mandate_id": read_raw_text(raw_message, "authorization", "mandate_id"),
        "sim_timestamp": read_raw_text(raw_message, "authorization", "timestamp"),
        "amount_chf": None,
        "currency": None,
        "merchant_id": read_raw_text(raw_message, "authorization", "merchant", "merchant_id"),
        "merchant_name": read_raw_text(raw_message, "authorization", "merchant", "merchant_name"),
        "decision": "step_up",
        "status": "pending",
        "reason_codes": [ReasonCode.INVALID_EVENT.value],
        "customer_message": INVALID_EVENT_MESSAGE,
        "notes": [],
        "received_at": format_moment(received_at),
        "decided_at": format_moment(decided_at),
        "deadline_at": str(deadline_text) if deadline_text is not None else None,
        "margin_ms": None,
        "total_ms": (decided_at - received_at) / timedelta(milliseconds = 1),
        "posted": False,
        "delivery_count": 1,
        "human_deadline_at": format_moment(decided_at + timedelta(seconds = human_timeout_seconds)),
        "source": source,
        "trace": None,
        "problems": list(problems),
        "resolution": None,
        "trust_score": trust_score.model_dump(mode = "json") if trust_score is not None else None,
    }



# Build the evidence items that carry the trust score of a record, or none when the record has no score or the score is left out
def trust_evidence_of_record(record, include_trust_score, note_error = None):
    if not include_trust_score or record.get("trust_score") is None:
        return []
    return build_trust_evidence_safely(record["trust_score"], note_error)



# Build the body sent to the platform for a purchase whose message could not be read
def build_invalid_event_request_body(live_authorization_id, engine_version, record = None, include_trust_score = True, note_error = None):
    return {
        "authorization_id": live_authorization_id,
        "decision": "step_up",
        "reason_codes": [ReasonCode.INVALID_EVENT.value],
        "customer_message": INVALID_EVENT_MESSAGE,
        "evidence": trust_evidence_of_record(record or {}, include_trust_score, note_error),
        "engine_version": engine_version,
    }



# Build the body sent to the platform from a stored record, for a delivery that repeats a purchase already decided
def build_request_body_from_record(record, engine_version, include_trust_score = True, note_error = None):
    return {
        "authorization_id": record["live_authorization_id"],
        "decision": record["decision"],
        "reason_codes": list(record.get("reason_codes", [])),
        "customer_message": record.get("customer_message", ""),
        "evidence": trust_evidence_of_record(record, include_trust_score, note_error),
        "engine_version": engine_version,
    }









#### Step 3: Define the worker ####

# Receive purchases from the platform in one task, decide each one once and answer it before its deadline.
# The worker never sends a human answer, and it never approves when the engine could not decide.
class DecisionWorker:

    def __init__(self, client, store, audit_log, broadcaster, settings, read_clock = None, decide_function = None, policy_builder = None, source = "live", fact_extractor = None):
        self.client = client
        self.store = store
        self.audit_log = audit_log
        self.broadcaster = broadcaster
        self.settings = settings
        self.read_clock = read_clock if read_clock is not None else read_utc_clock
        self.decide_function = decide_function if decide_function is not None else decide
        self.policy_builder = policy_builder if policy_builder is not None else build_policy_from_mandate
        self.source = source
        self.fact_extractor = fact_extractor



        # Keep the policies built in this process, the platform settings and the counters the status endpoint shows
        self.policies_by_key = {}
        self.platform_settings = {}
        self.polling = False
        self.continuous = False
        self.wake_up = asyncio.Event()
        self.stop_requested = False
        self.task = None
        self.counters = {"polls": 0, "deliveries": 0, "redeliveries": 0, "decisions": 0, "invalid_events": 0, "fallbacks": 0, "post_failures": 0, "poll_errors": 0}
        self.last_poll_at = None
        self.last_delivery_at = None
        self.last_error = None
        self.last_error_at = None
        self.last_margin_ms = None
        self.last_total_ms = None
        self.consecutive_poll_errors = 0



        # Let the service replace what the status event carries, so the stream sends the same object as the status endpoint
        self.status_describer = None



    # Read the human answer window, from the platform when known
    @property
    def human_timeout_seconds(self):
        return int(self.platform_settings.get("human_timeout_seconds", DEFAULT_HUMAN_TIMEOUT_SECONDS))



    # Describe the worker for the status endpoint
    def describe(self):
        return {
            "state": "polling" if self.polling else "idle",
            "continuous": self.continuous,
            "source": self.source,
            "last_poll_at": self.last_poll_at,
            "last_delivery_at": self.last_delivery_at,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "last_margin_ms": self.last_margin_ms,
            "last_total_ms": self.last_total_ms,
            "counters": dict(self.counters),
            "platform_settings": dict(self.platform_settings),
            "active_run_ids": [run["run_id"] for run in self.store.list_runs(active_only = True)],
        }



    # Tell every interface how the worker is doing, with the whole service status when the service asked for that
    def publish_status(self):
        describe = self.status_describer if self.status_describer is not None else self.describe
        self.broadcaster.publish("status", describe())



    # Note a failure for the status endpoint
    def note_error(self, error_text):
        self.last_error = error_text
        self.last_error_at = format_moment(self.read_clock())









    #### Step 4: Start and stop polling ####

    # Read the settings of the platform, which is the first live call and tells the worker the answer windows
    async def load_platform_settings(self):
        bootstrap_payload = await self.client.bootstrap()
        for setting_name in ("decision_timeout_seconds", "human_timeout_seconds", "long_poll_max_wait_seconds", "redelivery_after_seconds", "judging_mode", "max_active_runs", "max_runs_per_team"):
            value = find_value(bootstrap_payload, setting_name)
            if value is not None:
                self.platform_settings[setting_name] = value
        scenarios = find_value(bootstrap_payload, "scenarios")
        if isinstance(scenarios, list):
            self.platform_settings["scenarios"] = scenarios
        return bootstrap_payload



    # Start polling, either while a run is open or without end
    def start_polling(self, continuous = False):
        self.continuous = self.continuous or continuous
        self.polling = True
        self.wake_up.set()
        self.publish_status()



    # Stop polling after the current poll returns
    def stop_polling(self):
        self.polling = False
        self.continuous = False
        self.publish_status()



    # Start the task that runs the loop
    def start_task(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run_forever())
        return self.task



    # End the task
    async def stop_task(self):
        self.stop_requested = True
        self.polling = False
        self.wake_up.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None



    # Run the loop until the task ends, waiting while nobody asks for polling
    async def run_forever(self):
        while not self.stop_requested:
            if not self.polling:
                self.wake_up.clear()
                await self.wake_up.wait()
                continue
            await self.poll_once()









    #### Step 5: Poll once ####

    # Ask the platform for one purchase, handle it, and on an empty answer check the runs and expire old questions
    async def poll_once(self, wait_seconds = None):
        if wait_seconds is None:
            wait_seconds = self.settings.long_poll_wait_seconds
        self.counters["polls"] = self.counters["polls"] + 1
        self.last_poll_at = format_moment(self.read_clock())
        try:
            envelope = await self.client.next_decision_request(wait_seconds)
        except (LeashApiError, httpx.HTTPError) as poll_error:
            await self.handle_poll_error(poll_error)
            return
        self.consecutive_poll_errors = 0



        # Handle the purchase, where any failure inside is recorded and never ends the loop
        if envelope is not None:
            try:
                await self.handle_envelope(envelope)
            except Exception as handling_error:
                self.counters["post_failures"] = self.counters["post_failures"] + 1
                self.note_error("Handling a purchase failed - " + type(handling_error).__name__ + " - " + str(handling_error))
                self.publish_status()
            return



        # Nothing came, so expire old questions, refresh the runs and go idle when no run is open
        self.expire_old_questions()
        await self.refresh_runs()
        if not self.continuous and not self.store.list_runs(active_only = True):
            self.polling = False
            self.publish_status()



    # Record a failed poll and pause, longer after each failure in a row
    async def handle_poll_error(self, poll_error):
        self.counters["poll_errors"] = self.counters["poll_errors"] + 1
        self.consecutive_poll_errors = self.consecutive_poll_errors + 1
        self.note_error("Polling failed - " + type(poll_error).__name__ + " - " + str(poll_error))
        self.publish_status()
        pause_index = min(self.consecutive_poll_errors, len(POLL_BACKOFF_SECONDS)) - 1
        await asyncio.sleep(POLL_BACKOFF_SECONDS[pause_index])



    # Mark every question whose answer window has passed as expired and tell the interface
    def expire_old_questions(self):
        for record in self.store.expire_pending(self.read_clock()):
            self.audit_log.append(record.get("run_id"), "expired", {"live_authorization_id": record["live_authorization_id"], "status": "expired"})
            self.broadcaster.publish("resolution", record)



    # Read the progress of every open run and close the runs the platform reports as over
    async def refresh_runs(self):
        for run in self.store.list_runs(active_only = True):
            try:
                progress = await self.client.get_scenario_run(run["run_id"])
            except (LeashApiError, httpx.HTTPError) as progress_error:
                self.note_error("Reading run " + run["run_id"] + " failed - " + str(progress_error))
                continue
            status = find_value(progress, "status")
            if isinstance(status, str) and status.lower() in FINISHED_RUN_STATUSES:
                self.store.save_run({**run, "status": status.lower(), "raw": progress})
                self.broadcaster.publish("run", self.store.get_run(run["run_id"]))









    #### Step 6: Handle one delivered purchase ####

    # Decide one delivered purchase once, answer it and record it, or answer a repeated delivery from the store
    async def handle_envelope(self, envelope):
        received_at = self.read_clock()
        self.counters["deliveries"] = self.counters["deliveries"] + 1
        self.last_delivery_at = format_moment(received_at)
        run_id = envelope.get("run_id") if isinstance(envelope, dict) else None
        raw_message = envelope.get("data") if isinstance(envelope, dict) else None
        live_authorization_id = read_raw_text(raw_message, "authorization", "authorization_id")
        if live_authorization_id is None:
            live_authorization_id = envelope.get("authorization_id") if isinstance(envelope, dict) else None
        if not isinstance(live_authorization_id, str) or live_authorization_id == "":
            self.note_error("A delivery carried no live authorization identifier and could not be answered")
            self.publish_status()
            return None



        # Answer a purchase that was already decided from the store, so it is decided once and counted once
        existing_record = self.store.get_decision(live_authorization_id)
        if existing_record is not None:
            return await self.handle_redelivery(existing_record, raw_message)



        # Read the message strictly, and answer a message that cannot be read with a question
        try:
            event = read_purchase_message(raw_message)
        except InvalidEventError as invalid_event_error:
            return await self.handle_invalid_event(live_authorization_id, run_id, raw_message, invalid_event_error.problems, received_at)



        # Decide inside the budget, and fall back to a question or a refusal when the engine is late or fails.
        # The trust score is derived from the trace once and travels with the record and with the answer to the platform.
        trace = await self.decide_within_budget(event, received_at, run_id)
        trust_score = score_trace_safely(trace, event.mandate.uncertainty_policy, self.note_error)
        record = build_record_from_trace(trace, run_id, event, self.source, self.human_timeout_seconds, self.read_clock(), trust_score = trust_score)
        self.store.save_decision(record)
        self.counters["decisions"] = self.counters["decisions"] + 1
        self.last_margin_ms = trace.margin_ms
        self.last_total_ms = trace.timings.total_ms



        # Answer the platform, retrying until the deadline, then record and publish what happened
        request_body = build_decision_request_body(trace)
        if self.include_trust_score:
            request_body["evidence"] = list(request_body["evidence"]) + build_trust_evidence_safely(trust_score, self.note_error)
        await self.post_until_deadline(live_authorization_id, request_body, event.deadline_at)
        record = self.store.get_decision(live_authorization_id)
        self.audit_log.append(run_id, "decision", record)
        self.broadcaster.publish("decision", record)
        self.publish_status()
        return record



    # Count a repeated delivery and send the stored answer again when the platform never accepted it
    async def handle_redelivery(self, existing_record, raw_message):
        live_authorization_id = existing_record["live_authorization_id"]
        self.counters["redeliveries"] = self.counters["redeliveries"] + 1
        self.store.note_redelivery(live_authorization_id)
        if not existing_record["posted"]:
            deadline_text = existing_record.get("deadline_at") or read_raw_text(raw_message, "deadline_at")
            deadline_at = self.read_deadline(deadline_text)
            await self.post_until_deadline(live_authorization_id, build_request_body_from_record(existing_record, self.engine_version(), self.include_trust_score, self.note_error), deadline_at)
        record = self.store.get_decision(live_authorization_id)
        self.broadcaster.publish("decision", record)
        self.publish_status()
        return record



    # Answer a message that cannot be read with a question, and record the problems for the interface
    async def handle_invalid_event(self, live_authorization_id, run_id, raw_message, problems, received_at):
        decided_at = self.read_clock()
        self.counters["invalid_events"] = self.counters["invalid_events"] + 1
        record = build_invalid_event_record(live_authorization_id, run_id, raw_message, problems, received_at, decided_at, self.source, self.human_timeout_seconds, self.note_error)
        self.store.save_decision(record)
        deadline_at = self.read_deadline(record["deadline_at"])
        await self.post_until_deadline(live_authorization_id, build_invalid_event_request_body(live_authorization_id, self.engine_version(), record, self.include_trust_score, self.note_error), deadline_at)
        record = self.store.get_decision(live_authorization_id)
        self.audit_log.append(run_id, "decision", record)
        self.broadcaster.publish("decision", record)
        self.publish_status()
        return record



    # Read a deadline from its text, and take a short window from now when there is none
    def read_deadline(self, deadline_text):
        if isinstance(deadline_text, str):
            try:
                return datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
            except ValueError:
                pass
        return self.read_clock() + timedelta(seconds = 2)



    # Name the engine version written into every answer
    def engine_version(self):
        return ENGINE_VERSION



    # Say whether the trust score travels in the evidence of every answer, which the settings decide
    @property
    def include_trust_score(self):
        return bool(getattr(self.settings, "trust_score_in_evidence", True))









    #### Step 7: Decide within the budget ####

    # Look the policy up by the mandate, building it from the message once per distinct mandate content
    def policy_for(self, mandate):
        mandate_content = mandate.model_dump(mode = "json")
        policy_key = mandate.mandate_id + ":" + hashlib.sha256(json.dumps(mandate_content, sort_keys = True).encode("utf-8")).hexdigest()[:16]
        if policy_key not in self.policies_by_key:
            self.policies_by_key[policy_key] = self.policy_builder(mandate)
        return self.policies_by_key[policy_key]



    # Build the state handed to the engine, which is the frozen memory of the earlier purchases of the same run and the same mandate
    def build_decision_state(self, event, run_id):
        return build_ledger_snapshot_from_records(
            records = self.store.list_decisions(run_id = run_id),
            run_id = run_id,
            mandate_id = event.authorization.mandate_id,
            purchase_time = event.authorization.timestamp,
            shop_identifier = event.authorization.merchant.merchant_id,
            own_identifier = event.authorization.authorization_id,
            own_cart_lines = read_event_cart_lines(event),
        )



    # Ask the optional model for facts, containing every failure in its call record so it can never become a guard error
    async def extract_facts(self, event):
        if self.fact_extractor is None:
            return None
        started_at = time.perf_counter()
        try:
            return await self.fact_extractor.extract(event)
        except Exception as extraction_error:
            transport = getattr(self.fact_extractor, "transport", None)
            return ModelExtraction(
                facts = None,
                call = ModelCallRecord(
                    purpose = "fact_extraction",
                    status = "provider_error",
                    provider = str(getattr(transport, "provider", "unknown")),
                    model = str(getattr(transport, "model", "unknown")),
                    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3),
                    error_type = type(extraction_error).__name__,
                ),
            )



    # Run the optional extraction first and the engine in a thread under the time left, and build the fallback record when the engine is late or fails
    async def decide_within_budget(self, event, received_at, run_id):
        policy = self.policy_for(event.mandate)
        budget = DeadlineBudget(
            received_at = received_at,
            deadline_at = event.deadline_at,
            engine_budget_ms = self.settings.engine_budget_ms,
            post_reserve_ms = self.settings.post_reserve_ms,
        )
        extracted_facts = await self.extract_facts(event)
        seconds_left = budget.seconds_left_for_engine(self.read_clock())
        try:
            decision_arguments = (event, policy, self.build_decision_state(event, run_id))
            if extracted_facts is None:
                decision_call = asyncio.to_thread(self.decide_function, *decision_arguments)
            else:
                decision_call = asyncio.to_thread(self.decide_function, *decision_arguments, extracted_facts = extracted_facts)
            return await asyncio.wait_for(
                decision_call,
                timeout = seconds_left,
            )
        except asyncio.TimeoutError:
            self.counters["fallbacks"] = self.counters["fallbacks"] + 1
            self.note_error("The engine did not decide " + event.authorization.authorization_id + " within " + f"{seconds_left:.2f}" + " seconds")
            return build_fallback_trace(
                event, received_at, self.read_clock(), ReasonCode.ENGINE_TIMEOUT_FALLBACK,
                "Please review this purchase. The checks did not finish in time, so nothing was approved.",
            )
        except Exception as engine_error:
            self.counters["fallbacks"] = self.counters["fallbacks"] + 1
            self.note_error("The engine failed on " + event.authorization.authorization_id + " - " + type(engine_error).__name__ + " - " + str(engine_error))
            traceback.print_exc()
            return build_fallback_trace(
                event, received_at, self.read_clock(), ReasonCode.GUARD_ERROR,
                "Please review this purchase. The checks could not run, so nothing was approved.",
            )









    #### Step 8: Send the answer ####

    # Send the answer, retrying on no answer or a server failure until the deadline, and stop on a refusal
    async def post_until_deadline(self, live_authorization_id, request_body, deadline_at):
        attempts = 0
        while True:
            attempts = attempts + 1
            try:
                response = await self.client.post_decision(live_authorization_id, request_body)
                self.store.mark_posted(live_authorization_id, True, 200, None)
                return response
            except LeashApiError as api_error:
                if api_error.status_code < 500:
                    self.counters["post_failures"] = self.counters["post_failures"] + 1
                    self.store.mark_posted(live_authorization_id, False, api_error.status_code, str(api_error))
                    self.note_error(str(api_error))
                    return None
                last_error = api_error
                status_code = api_error.status_code
            except httpx.HTTPError as network_error:
                last_error = network_error
                status_code = None



            # Give up once the deadline has passed, and pause briefly before the next attempt otherwise
            if self.read_clock() >= deadline_at:
                self.counters["post_failures"] = self.counters["post_failures"] + 1
                self.store.mark_posted(live_authorization_id, False, status_code, "Not accepted before the deadline after " + str(attempts) + " attempts - " + str(last_error))
                self.note_error("The answer for " + live_authorization_id + " was not accepted before the deadline - " + str(last_error))
                return None
            await asyncio.sleep(POST_RETRY_PAUSE_SECONDS)
