# Script: fake_platform.py
# Purpose: Stand in for the authorization service inside the process, with the same endpoints, so the worker can be tested and run without the live server
# Author: Jonas Lüthi
# Date: September 2026

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response









#### Step 1: Define the settings and the identifiers ####

# State the settings the live server reported in its bootstrap answer, which the copy reports the same way
DEFAULT_PLATFORM_SETTINGS = {
    "decision_timeout_seconds": 8,
    "human_timeout_seconds": 120,
    "long_poll_max_wait_seconds": 25,
    "redelivery_after_seconds": 3,
    "max_active_runs": 3,
    "max_runs_per_team": 300,
    "rate_limit_per_minute": 1200,
    "history_window_minutes": 10,
    "judging_mode": False,
    "team_reset_enabled": True,
    "mandate_patch_tighten_only": True,
    "revoked_mandate_cancels_remaining_purchases": True,
}



# State the closed value lists of the platform
ALLOWED_DECISIONS = ("approve", "decline", "step_up")
ALLOWED_HUMAN_DECISIONS = ("approve", "decline")
ALLOWED_UNCERTAINTY_POLICIES = ("ask", "decline", "approve")



# Make a fresh identifier with a readable prefix
def make_identifier(prefix):
    return prefix + uuid.uuid4().hex[:12]



# Write a moment the way the live messages write it
def format_moment(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")



# Read the real clock in UTC
def read_utc_clock():
    return datetime.now(timezone.utc)



# Build the error body of the platform
def error_response(status_code, code, message):
    return JSONResponse(status_code = status_code, content = {"error": {"code": code, "message": message}})









#### Step 2: Define the state of a run ####

# Keep what one run remembers for building its messages, in the shape the offline message builder expects
@dataclass
class FakeRunState:
    mandate_id: str
    profile_id: str
    live_id_by_source_id: dict = field(default_factory = dict)
    earlier_purchases: list = field(default_factory = list)



# Keep one live purchase of a run
@dataclass
class FakeAuthorization:
    authorization_id: str
    run_id: str
    event: dict
    queued_at: datetime
    deadline_at: datetime
    delivered_at: Optional[datetime] = None
    delivery_count: int = 0
    status: str = "queued"
    decision: Optional[dict] = None
    resolution: Optional[dict] = None
    late: bool = False



# Keep one run, its position in the scenario and its purchases
@dataclass
class FakeRun:
    run_id: str
    scenario_id: str
    mandate: dict
    attempts: list
    run_state: FakeRunState
    started_at: datetime
    position: int = 0
    status: str = "active"
    authorization_ids: list = field(default_factory = list)









#### Step 3: Define the platform ####

# Hold the mandates, runs and purchases of one team, and deliver the purchases of a scenario one after the other.
# scenarios maps a scenario identifier to its attempts in delivery order, each a dictionary with the joined purchase row and its cart lines.
# message_builder builds one message from an attempt, its cart lines, the run state, the uncertainty policy, the live identifier and the receipt moment.
class FakePlatform:

    def __init__(self, scenarios, message_builder, team_api_key = "leash_test_key", settings = None, read_clock = None, scenario_names = None):
        self.scenarios = scenarios
        self.message_builder = message_builder
        self.team_api_key = team_api_key
        self.settings = {**DEFAULT_PLATFORM_SETTINGS, **(settings or {})}
        self.read_clock = read_clock if read_clock is not None else read_utc_clock
        self.scenario_names = scenario_names or {}
        self.drafts = {}
        self.mandates = {}
        self.runs = {}
        self.authorizations = {}
        self.events = []
        self.runs_started = 0
        self.app = build_fake_app(self)



    # Forget everything, as the team reset does
    def reset(self):
        self.drafts.clear()
        self.mandates.clear()
        self.runs.clear()
        self.authorizations.clear()
        self.events.clear()
        self.runs_started = 0



    # Record one event of the feed
    def record_event(self, event_type, payload):
        self.events.append({"cursor": len(self.events) + 1, "type": event_type, "occurred_at": format_moment(self.read_clock()), **payload})



    # Describe the scenarios the way the bootstrap answer lists them
    def describe_scenarios(self):
        return [
            {
                "scenario_id": scenario_id,
                "name": self.scenario_names.get(scenario_id, scenario_id),
                "event_count": len(attempts),
            }
            for scenario_id, attempts in sorted(self.scenarios.items())
        ]









    #### Step 4: Manage mandates ####

    # Store a draft
    def create_draft(self, body):
        draft_id = make_identifier("draft_")
        draft = {
            "draft_id": draft_id,
            "instruction": body["instruction"],
            "hard_rules": body.get("hard_rules", []),
            "uncertainty_policy": body["uncertainty_policy"],
            "guidance": body.get("guidance", []),
            "open_questions": body.get("open_questions", []),
            "status": "draft",
            "created_at": format_moment(self.read_clock()),
        }
        self.drafts[draft_id] = draft
        return draft



    # Activate a draft as a mandate
    def confirm_draft(self, draft_id):
        draft = self.drafts[draft_id]
        mandate_id = make_identifier("TM_")
        mandate = {
            **draft,
            "mandate_id": mandate_id,
            "status": "active",
            "confirmed_at": format_moment(self.read_clock()),
        }
        self.mandates[mandate_id] = mandate
        self.record_event("mandate.confirmed", {"mandate_id": mandate_id})
        return mandate



    # Tighten a mandate under the platform's rules, which keep every existing rule and let ask or approve become decline
    def patch_mandate(self, mandate_id, body):
        mandate = self.mandates[mandate_id]
        if mandate["status"] != "active":
            return None, "mandate_not_active", "Only an active mandate can be changed"
        if "hard_rules" in body:
            existing_rules = mandate["hard_rules"]
            new_rules = body["hard_rules"]
            if new_rules[:len(existing_rules)] != existing_rules:
                return None, "rules_not_preserved", "Existing rules must be kept unchanged"
            mandate["hard_rules"] = new_rules
        if "uncertainty_policy" in body:
            if body["uncertainty_policy"] != "decline" and body["uncertainty_policy"] != mandate["uncertainty_policy"]:
                return None, "policy_not_tightened", "The uncertainty policy can only change to decline"
            mandate["uncertainty_policy"] = body["uncertainty_policy"]
        if "guidance" in body:
            mandate["guidance"] = body["guidance"]
        if "open_questions" in body:
            mandate["open_questions"] = body["open_questions"]
        mandate["updated_at"] = format_moment(self.read_clock())
        return mandate, None, None



    # Revoke a mandate and cancel the purchases of its runs that are still to come
    def revoke_mandate(self, mandate_id):
        mandate = self.mandates[mandate_id]
        mandate["status"] = "revoked"
        mandate["revoked_at"] = format_moment(self.read_clock())
        for run in self.runs.values():
            if run.mandate["mandate_id"] == mandate_id and run.status == "active":
                run.status = "cancelled"
                for authorization_id in run.authorization_ids:
                    authorization = self.authorizations[authorization_id]
                    if authorization.status in ("queued", "delivered"):
                        authorization.status = "cancelled"
        self.record_event("mandate.revoked", {"mandate_id": mandate_id})
        return mandate









    #### Step 5: Manage runs and deliver purchases ####

    # Start a run of a scenario with an active mandate
    def start_run(self, scenario_id, mandate_id):
        mandate = self.mandates[mandate_id]
        run_id = make_identifier("run_")
        run = FakeRun(
            run_id = run_id,
            scenario_id = scenario_id,
            mandate = mandate,
            attempts = self.scenarios[scenario_id],
            run_state = FakeRunState(mandate_id = mandate_id, profile_id = make_identifier("PROFILE_")),
            started_at = self.read_clock(),
        )
        self.runs[run_id] = run
        self.runs_started = self.runs_started + 1
        self.record_event("run.started", {"run_id": run_id, "scenario_id": scenario_id})
        return run



    # Describe the progress of a run
    def describe_run(self, run):
        authorizations = [self.authorizations[authorization_id] for authorization_id in run.authorization_ids]
        decided_count = sum(1 for authorization in authorizations if authorization.decision is not None)
        pending_count = sum(1 for authorization in authorizations if authorization.status == "pending")
        return {
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "mandate_id": run.mandate["mandate_id"],
            "profile_id": run.run_state.profile_id,
            "status": run.status,
            "started_at": format_moment(run.started_at),
            "counters": {
                "total_events": len(run.attempts),
                "delivered": len(run.authorization_ids),
                "decided": decided_count,
                "pending_human": pending_count,
            },
        }



    # Queue the next purchase of a run, built from the attempt at the run's position
    def queue_next_purchase(self, run):
        attempt = run.attempts[run.position]
        now = self.read_clock()
        live_authorization_id = make_identifier("LA_")
        message = self.message_builder(
            attempt = attempt["attempt"],
            cart_lines = attempt["cart_lines"],
            run_state = run.run_state,
            uncertainty_policy = run.mandate["uncertainty_policy"],
            live_authorization_id = live_authorization_id,
            received_at = now,
        )



        # Write the stored mandate into the message, as the platform sends the snapshot it holds
        message["mandate"] = {
            **message["mandate"],
            "mandate_id": run.mandate["mandate_id"],
            "status": "active",
            "instruction": run.mandate["instruction"],
            "hard_rules": run.mandate["hard_rules"],
            "uncertainty_policy": run.mandate["uncertainty_policy"],
            "profile_id": run.run_state.profile_id,
        }
        message["authorization"]["mandate_id"] = run.mandate["mandate_id"]
        message["authorization"]["profile_id"] = run.run_state.profile_id
        deadline_at = now + timedelta(seconds = self.settings["decision_timeout_seconds"])
        message["deadline_at"] = format_moment(deadline_at)



        # Remember the purchase, its source and its place among the earlier purchases of the run
        authorization = FakeAuthorization(
            authorization_id = live_authorization_id,
            run_id = run.run_id,
            event = message,
            queued_at = now,
            deadline_at = deadline_at,
        )
        self.authorizations[live_authorization_id] = authorization
        run.authorization_ids.append(live_authorization_id)
        run.run_state.live_id_by_source_id[attempt["attempt"]["authorization_id"]] = live_authorization_id
        run.run_state.earlier_purchases.append({
            "authorization_id": live_authorization_id,
            "timestamp": message["authorization"]["timestamp"],
            "merchant_id": message["authorization"]["merchant"]["merchant_id"],
            "billing_amount_chf": message["authorization"]["billing_amount_chf"],
            "status": "pending",
        })
        run.position = run.position + 1
        return authorization



    # Find the purchase to deliver now, which is an unanswered one due again or the next one of a run, or None
    def pick_purchase_to_deliver(self):
        now = self.read_clock()
        for run in self.runs.values():
            if run.status != "active":
                continue



            # Deliver an unanswered purchase again once the redelivery pause has passed
            in_flight = [
                self.authorizations[authorization_id]
                for authorization_id in run.authorization_ids
                if self.authorizations[authorization_id].status in ("queued", "delivered")
            ]
            if in_flight:
                authorization = in_flight[0]
                pause = timedelta(seconds = self.settings["redelivery_after_seconds"])
                if authorization.delivered_at is None or now - authorization.delivered_at >= pause:
                    return authorization
                continue



            # Queue the next purchase of the run, or close the run when none is left
            if run.position < len(run.attempts):
                return self.queue_next_purchase(run)
            run.status = "completed"
            self.record_event("run.completed", {"run_id": run.run_id})
        return None



    # Wrap a purchase in the envelope of the poll answer and count the delivery
    def deliver(self, authorization):
        authorization.delivered_at = self.read_clock()
        authorization.delivery_count = authorization.delivery_count + 1
        authorization.status = "delivered"
        return {
            "run_id": authorization.run_id,
            "event_id": make_identifier("evt_"),
            "type": "authorization.request",
            "authorization_id": authorization.authorization_id,
            "status": "pending_decision",
            "occurred_at": format_moment(authorization.queued_at),
            "delivery_count": authorization.delivery_count,
            "data": authorization.event,
        }



    # Wait for a purchase up to the given seconds, checking every fifty milliseconds
    async def wait_for_purchase(self, wait_seconds):
        wait_seconds = min(float(wait_seconds), float(self.settings["long_poll_max_wait_seconds"]))
        waited = 0.0
        while True:
            authorization = self.pick_purchase_to_deliver()
            if authorization is not None:
                return self.deliver(authorization)
            if waited >= wait_seconds:
                return None
            await asyncio.sleep(0.05)
            waited = waited + 0.05









    #### Step 6: Record decisions and customer answers ####

    # Record the decision of the engine, which is refused for an unknown, already decided or expired purchase
    def record_decision(self, authorization_id, body):
        authorization = self.authorizations.get(authorization_id)
        if authorization is None:
            return None, 404, "not_found", "Unknown authorization"
        if body.get("authorization_id") != authorization_id:
            return None, 400, "id_mismatch", "The body names another authorization"
        if body.get("decision") not in ALLOWED_DECISIONS:
            return None, 400, "invalid_decision", "The decision must be approve, decline or step_up"
        if authorization.decision is not None:
            return None, 409, "already_decided", "This authorization already has a decision"
        if authorization.status == "cancelled":
            return None, 409, "cancelled", "This authorization was cancelled"
        now = self.read_clock()
        if now > authorization.deadline_at:
            authorization.status = "expired"
            authorization.late = True
            return None, 409, "deadline_passed", "The decision deadline has passed"



        # Keep the decision and move the purchase to its status
        authorization.decision = {**body, "recorded_at": format_moment(now)}
        status_by_decision = {"approve": "approved", "decline": "declined", "step_up": "pending"}
        authorization.status = status_by_decision[body["decision"]]
        self.update_earlier_purchase(authorization)
        self.record_event("authorization.decided", {"run_id": authorization.run_id, "authorization_id": authorization_id, "decision": body["decision"]})
        return self.describe_authorization(authorization), 200, None, None



    # Record the customer's answer, which is accepted only for a purchase that waits for one
    def record_resolution(self, authorization_id, body):
        authorization = self.authorizations.get(authorization_id)
        if authorization is None:
            return None, 404, "not_found", "Unknown authorization"
        if body.get("decision") not in ALLOWED_HUMAN_DECISIONS:
            return None, 400, "invalid_decision", "The customer's answer must be approve or decline"
        if authorization.status != "pending":
            return None, 409, "not_pending", "This authorization does not wait for the customer"
        now = self.read_clock()
        authorization.resolution = {**body, "recorded_at": format_moment(now)}
        authorization.status = "approved" if body["decision"] == "approve" else "declined"
        self.update_earlier_purchase(authorization)
        self.record_event("authorization.resolved", {"run_id": authorization.run_id, "authorization_id": authorization_id, "decision": body["decision"]})
        return self.describe_authorization(authorization), 200, None, None



    # Copy the status of a purchase into the run's list of earlier purchases, which feeds the context of the later messages
    def update_earlier_purchase(self, authorization):
        run = self.runs[authorization.run_id]
        for earlier_purchase in run.run_state.earlier_purchases:
            if earlier_purchase["authorization_id"] == authorization.authorization_id:
                earlier_purchase["status"] = authorization.status if authorization.status in ("approved", "declined", "pending", "cancelled") else "cancelled"



    # Describe one purchase as the list endpoint shows it
    def describe_authorization(self, authorization):
        return {
            "authorization_id": authorization.authorization_id,
            "run_id": authorization.run_id,
            "source_authorization_id": authorization.event["authorization"]["source_authorization_id"],
            "status": authorization.status,
            "deadline_at": format_moment(authorization.deadline_at),
            "delivery_count": authorization.delivery_count,
            "decision": authorization.decision,
            "resolution": authorization.resolution,
        }









#### Step 7: Build the endpoints ####

# Build the FastAPI app that exposes the platform, so the client reaches it through an ASGI transport
def build_fake_app(platform):
    app = FastAPI(title = "Fake authorization platform")



    # Refuse every call without the team key, except the health check
    @app.middleware("http")
    async def require_bearer_key(request: Request, call_next):
        if request.url.path != "/healthz":
            expected = "Bearer " + platform.team_api_key
            if request.headers.get("authorization") != expected:
                return error_response(401, "unauthorized", "A valid team bearer token is required")
        return await call_next(request)



    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "service": "fake-platform", "api_version": "0.1.0", "pack_version": "saw26"}



    @app.get("/v1/bootstrap")
    async def bootstrap():
        return {
            "api_version": "0.1.0",
            "pack_version": "saw26",
            "scenarios": platform.describe_scenarios(),
            **platform.settings,
        }



    @app.get("/v1/reference-data")
    async def reference_data():
        return {
            "scenarios": platform.describe_scenarios(),
            "fx_rates_to_chf": {"CHF": 1.0, "EUR": 0.96, "GBP": 1.12, "USD": 0.88},
        }



    @app.post("/v1/mandates", status_code = 201)
    async def create_mandate(request: Request):
        body = await request.json()
        if not isinstance(body.get("instruction"), str) or body["instruction"] == "":
            return error_response(400, "invalid_instruction", "instruction must be a nonempty string")
        if body.get("uncertainty_policy") not in ALLOWED_UNCERTAINTY_POLICIES:
            return error_response(400, "invalid_policy", "uncertainty_policy must be ask, decline or approve")
        return platform.create_draft(body)



    @app.post("/v1/mandates/{draft_id}/confirm")
    async def confirm_mandate(draft_id: str, request: Request):
        body = await request.json()
        if draft_id not in platform.drafts:
            return error_response(404, "not_found", "Unknown draft")
        if body.get("confirmed") is not True:
            return error_response(400, "not_confirmed", "confirmed must be true")
        return platform.confirm_draft(draft_id)



    @app.get("/v1/mandates/{mandate_id}")
    async def get_mandate(mandate_id: str):
        if mandate_id not in platform.mandates:
            return error_response(404, "not_found", "Unknown mandate")
        return platform.mandates[mandate_id]



    @app.patch("/v1/mandates/{mandate_id}")
    async def patch_mandate(mandate_id: str, request: Request):
        if mandate_id not in platform.mandates:
            return error_response(404, "not_found", "Unknown mandate")
        body = await request.json()
        mandate, code, message = platform.patch_mandate(mandate_id, body)
        if mandate is None:
            return error_response(409, code, message)
        return mandate



    @app.delete("/v1/mandates/{mandate_id}")
    async def revoke_mandate(mandate_id: str):
        if mandate_id not in platform.mandates:
            return error_response(404, "not_found", "Unknown mandate")
        return platform.revoke_mandate(mandate_id)



    @app.post("/v1/scenario-runs", status_code = 201)
    async def start_run(request: Request):
        body = await request.json()
        scenario_id = body.get("scenario_id")
        mandate_id = body.get("mandate_id")
        if scenario_id not in platform.scenarios:
            return error_response(404, "unknown_scenario", "Unknown scenario " + str(scenario_id))
        if mandate_id not in platform.mandates:
            return error_response(404, "unknown_mandate", "Unknown mandate " + str(mandate_id))
        if platform.mandates[mandate_id]["status"] != "active":
            return error_response(409, "mandate_not_active", "The mandate is not active")
        active_runs = [run for run in platform.runs.values() if run.status == "active"]
        if len(active_runs) >= platform.settings["max_active_runs"]:
            return error_response(429, "too_many_active_runs", "The team has too many active runs")
        if platform.runs_started >= platform.settings["max_runs_per_team"]:
            return error_response(429, "run_budget_exhausted", "The team has used all its runs")
        return platform.describe_run(platform.start_run(scenario_id, mandate_id))



    @app.get("/v1/scenario-runs/{run_id}")
    async def get_run(run_id: str):
        if run_id not in platform.runs:
            return error_response(404, "not_found", "Unknown run")
        return platform.describe_run(platform.runs[run_id])



    @app.get("/v1/decision-requests/next")
    async def next_decision_request(wait: int = 25):
        envelope = await platform.wait_for_purchase(wait)
        if envelope is None:
            return Response(status_code = 204)
        return envelope



    @app.post("/v1/authorizations/{authorization_id}/decision")
    async def post_decision(authorization_id: str, request: Request):
        body = await request.json()
        result, status_code, code, message = platform.record_decision(authorization_id, body)
        if result is None:
            return error_response(status_code, code, message)
        return result



    @app.post("/v1/authorizations/{authorization_id}/resolve")
    async def resolve_authorization(authorization_id: str, request: Request):
        body = await request.json()
        result, status_code, code, message = platform.record_resolution(authorization_id, body)
        if result is None:
            return error_response(status_code, code, message)
        return result



    @app.get("/v1/authorizations")
    async def list_authorizations():
        return {"authorizations": [platform.describe_authorization(authorization) for authorization in platform.authorizations.values()]}



    @app.get("/v1/events")
    async def read_events(since: int = 0):
        events = [event for event in platform.events if event["cursor"] > since]
        next_cursor = events[-1]["cursor"] if events else since
        return {"events": events, "next_cursor": next_cursor}



    @app.post("/v1/team/reset")
    async def reset_team():
        if not platform.settings["team_reset_enabled"]:
            return error_response(403, "reset_disabled", "Team reset is disabled")
        platform.reset()
        return {"status": "reset"}

    return app
