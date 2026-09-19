# Script: service.py
# Purpose: Wire the store, the platform client, the worker and the event stream together, and carry out what the customer does on the screens
# Author: Jonas Lüthi
# Date: September 2026

from datetime import datetime, timezone

import httpx

from app.api_client.leash import LeashApiError, LeashClient, find_value
from app.api_client.offline import build_offline_platform
from app.config import get_settings, resolve_audit_folder, resolve_state_db_path
from app.llm.breaker import CircuitState
from app.llm.client import OpenAICompatibleTransport
from app.llm.extractor import FactExtractor
from app.llm.policy_compiler import PolicyModelCompiler
from app.models.decision import ENGINE_VERSION
from app.policyc.compiler import build_policy_from_mandate
from app.state.audit import AuditLog
from app.state.ledger import build_ledger_snapshot_from_records, find_budget_objection, read_ledger_time, read_record_cart_lines
from app.state.store import DecisionStore
from app.trust.score import TRUST_SCORE_VERSION
from app.webapi.policy_view import MandateContent, describe_policy
from app.webapi.sse import EventBroadcaster
from app.worker.loop import DecisionWorker









#### Step 1: Define the errors the screens can show ####

# Carry a refusal of the service with the status code the web API answers with
class ServiceError(Exception):

    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super().__init__(message)



# Name the key under which the store keeps the mandate the screens work with
CURRENT_MANDATE_KEY = "current_mandate_id"



# Write a moment as ISO 8601 text in UTC
def format_moment(moment):
    return moment.astimezone(timezone.utc).isoformat()









#### Step 2: Define the service ####

# Hold everything one running backend needs, and carry out the customer's actions
class RelayService:

    def __init__(self, settings, store, client, audit_log, broadcaster, worker, platform = None, policy_model_compiler = None):
        self.settings = settings
        self.store = store
        self.client = client
        self.audit_log = audit_log
        self.broadcaster = broadcaster
        self.worker = worker
        self.platform = platform
        self.policy_model_compiler = policy_model_compiler
        self.last_policy_model_call = None
        self.started_at = None
        self.bootstrap_payload = None
        self.bootstrap_error = None
        self.worker.status_describer = self.describe_status



    # Build the service from the settings, against the live platform or the in-process copy
    @classmethod
    def build(cls, settings = None, platform = None, store = None):
        if settings is None:
            settings = get_settings()
        if store is None:
            store = DecisionStore(resolve_state_db_path(settings))
        broadcaster = EventBroadcaster()



        # Choose the platform, where offline mode serves the public purchases from inside the process
        if platform is None and settings.leash_mode == "offline":
            platform = build_offline_platform(team_api_key = settings.team_api_key or "leash_offline")



        # Keep the audit files of offline runs apart from the files of live runs
        audit_folder = resolve_audit_folder(settings)
        if platform is not None:
            audit_folder = audit_folder / "offline"
        audit_log = AuditLog(audit_folder)
        if platform is not None:
            transport = httpx.ASGITransport(app = platform.app)
            http_client = httpx.AsyncClient(transport = transport, base_url = "http://offline-platform")
            client = LeashClient("http://offline-platform", platform.team_api_key, http_client = http_client)
            source = "offline"
        else:
            client = LeashClient(settings.leash_base_url, settings.team_api_key)
            source = "live"
        policy_model_compiler = None
        fact_extractor = None
        if settings.llm_model != "" and settings.llm_api_key != "":
            provider = "swisscom-apertus" if "swisscom" in settings.llm_base_url.casefold() else "openai"
            model_transport = OpenAICompatibleTransport(
                settings.llm_base_url,
                settings.llm_model,
                provider = provider,
                initial_api_key = settings.llm_api_key,
            )
            policy_model_compiler = PolicyModelCompiler(model_transport)
            fact_extractor = FactExtractor(model_transport, timeout_seconds = settings.llm_budget_ms / 1000)
        worker = DecisionWorker(client, store, audit_log, broadcaster, settings, source = source, fact_extractor = fact_extractor)
        return cls(settings, store, client, audit_log, broadcaster, worker, platform, policy_model_compiler)



    # Start the worker task, read the platform settings once, and poll from the start when asked to
    async def start(self):
        self.started_at = format_moment(datetime.now(timezone.utc))
        await self.refresh_bootstrap()
        self.worker.start_task()
        if self.settings.worker_autostart:
            self.worker.start_polling(continuous = True)



    # Stop the worker task and close the connections
    async def stop(self):
        await self.worker.stop_task()
        await self.client.close()
        self.store.close()



    # Read the platform settings, keeping the failure for the status endpoint instead of failing the start
    async def refresh_bootstrap(self):
        try:
            self.bootstrap_payload = await self.worker.load_platform_settings()
            self.bootstrap_error = None
        except (LeashApiError, httpx.HTTPError) as bootstrap_error:
            self.bootstrap_payload = None
            self.bootstrap_error = type(bootstrap_error).__name__ + " - " + str(bootstrap_error)
        return self.bootstrap_payload



    # Turn a platform refusal or a lost connection into a service error
    @staticmethod
    def translate_platform_error(platform_error):
        if isinstance(platform_error, LeashApiError):
            return ServiceError(platform_error.status_code, str(platform_error))
        return ServiceError(502, "The platform could not be reached - " + type(platform_error).__name__ + " - " + str(platform_error))









    #### Step 3: Describe the state ####

    # Describe the service for the status endpoint and the header badge
    def describe_status(self):
        model_mode = "off"
        if self.policy_model_compiler is not None:
            model_mode = "degraded" if self.policy_model_compiler.breaker.state == CircuitState.OPEN else "ready"
        return {
            "engine_version": ENGINE_VERSION,
            "engine_mode": "deterministic",
            "llm_mode": model_mode,
            "llm_model": self.settings.llm_model or None,
            "llm_last_call": self.last_policy_model_call,
            "trust_score_version": TRUST_SCORE_VERSION,
            "trust_score_in_evidence": self.settings.trust_score_in_evidence,
            "leash_mode": self.settings.leash_mode if self.platform is None else "offline",
            "leash_base_url": self.client.base_url,
            "team_key_present": self.settings.team_api_key != "",
            "started_at": self.started_at,
            "bootstrap": self.bootstrap_payload,
            "bootstrap_error": self.bootstrap_error,
            "worker": self.worker.describe(),
            "counts": self.store.count_by_status(),
            "current_mandate_id": self.store.get_value(CURRENT_MANDATE_KEY),
            "state_db_path": str(self.store.db_path),
            "audit_folder": str(self.audit_log.folder),
        }









    #### Step 4: Compile, confirm, tighten and revoke the policy ####

    # Compile deterministically first, then optionally let the bounded model improve the customer-confirmed draft.
    async def compile_policy(self, instruction, uncertainty_policy = "ask", hard_rules = ()):
        mandate_content = MandateContent(instruction, hard_rules, uncertainty_policy)
        policy = build_policy_from_mandate(mandate_content)
        if self.policy_model_compiler is not None:
            compilation = await self.policy_model_compiler.compile(instruction, uncertainty_policy, policy)
            policy = compilation.policy
            self.last_policy_model_call = compilation.call.model_dump(mode = "json")
        return describe_policy(policy)



    # Store the instruction with its rules as a draft on the platform, which the customer still has to confirm
    async def create_draft(self, instruction, hard_rules, uncertainty_policy, guidance = (), open_questions = ()):
        try:
            draft_payload = await self.client.create_mandate(instruction, hard_rules, uncertainty_policy, guidance, open_questions)
        except (LeashApiError, httpx.HTTPError) as platform_error:
            raise self.translate_platform_error(platform_error)
        draft_id = find_value(draft_payload, "draft_id")
        if not isinstance(draft_id, str):
            raise ServiceError(502, "The platform answered without a draft_id - " + str(draft_payload)[:300])
        self.store.set_value("draft:" + draft_id, {"instruction": instruction, "hard_rules": list(hard_rules), "uncertainty_policy": uncertainty_policy})
        return {"draft_id": draft_id, "draft": draft_payload}



    # Confirm a draft on the platform, which makes it the mandate the screens work with
    async def confirm_draft(self, draft_id):
        try:
            confirmation = await self.client.confirm_mandate(draft_id)
        except (LeashApiError, httpx.HTTPError) as platform_error:
            raise self.translate_platform_error(platform_error)
        mandate_id = find_value(confirmation, "mandate_id")
        if not isinstance(mandate_id, str):
            raise ServiceError(502, "The platform answered without a mandate_id - " + str(confirmation)[:300])



        # Keep the mandate as confirmed, taking the content from the answer and falling back to the draft we sent
        draft_content = self.store.get_value("draft:" + draft_id, {})
        mandate = self.store.save_mandate({
            "mandate_id": mandate_id,
            "draft_id": draft_id,
            "instruction": find_value(confirmation, "instruction", draft_content.get("instruction", "")),
            "hard_rules": find_value(confirmation, "hard_rules", draft_content.get("hard_rules", [])),
            "uncertainty_policy": find_value(confirmation, "uncertainty_policy", draft_content.get("uncertainty_policy", "ask")),
            "status": "active",
            "raw": confirmation,
        })
        self.store.set_value(CURRENT_MANDATE_KEY, mandate_id)
        self.broadcaster.publish("mandate", mandate)
        return mandate



    # Read the mandate the screens work with, refreshed from the platform when it can be reached
    async def get_current_mandate(self, refresh = True):
        mandate_id = self.store.get_value(CURRENT_MANDATE_KEY)
        if mandate_id is None:
            return None
        mandate = self.store.get_mandate(mandate_id)
        if refresh:
            try:
                platform_mandate = await self.client.get_mandate(mandate_id)
                mandate = self.store.save_mandate({
                    **mandate,
                    "instruction": find_value(platform_mandate, "instruction", mandate["instruction"]),
                    "hard_rules": find_value(platform_mandate, "hard_rules", mandate["hard_rules"]),
                    "uncertainty_policy": find_value(platform_mandate, "uncertainty_policy", mandate["uncertainty_policy"]),
                    "status": find_value(platform_mandate, "status", mandate["status"]),
                    "raw": platform_mandate,
                })
            except (LeashApiError, httpx.HTTPError):
                pass
        return mandate



    # Describe the mandate with its compiled policy for the policy screen, where a mandate the compiler cannot read still shows
    async def describe_current_mandate(self, refresh = True):
        mandate = await self.get_current_mandate(refresh)
        if mandate is None:
            return None
        try:
            policy_view = await self.compile_policy(mandate["instruction"], mandate["uncertainty_policy"], mandate["hard_rules"])
            policy_error = None
        except (ValueError, AssertionError) as compile_error:
            policy_view = None
            policy_error = "The stored mandate could not be compiled - " + str(compile_error)
        return {"mandate": mandate, "policy": policy_view, "policy_error": policy_error}



    # Read the current mandate for a change, and refuse when the screen names a different mandate than the one this backend works with
    async def current_mandate_for_change(self, mandate_id = None):
        mandate = await self.get_current_mandate(refresh = False)
        if mandate is None:
            raise ServiceError(409, "No mandate is confirmed yet")
        if mandate_id is not None and mandate_id != mandate["mandate_id"]:
            raise ServiceError(409, "The mandate " + mandate_id + " is not the current mandate, which is " + mandate["mandate_id"])
        return mandate



    # Tighten the mandate, adding rules and moving uncertainty to decline, and never loosening anything
    async def tighten_mandate(self, add_rules = (), uncertainty_policy = None, mandate_id = None):
        mandate = await self.current_mandate_for_change(mandate_id)
        if uncertainty_policy is not None and uncertainty_policy != "decline":
            raise ServiceError(400, "The uncertainty policy can only be tightened to decline")
        hard_rules = None
        if add_rules:
            hard_rules = list(mandate["hard_rules"]) + list(add_rules)
        try:
            platform_mandate = await self.client.patch_mandate(mandate["mandate_id"], hard_rules = hard_rules, uncertainty_policy = uncertainty_policy)
        except (LeashApiError, httpx.HTTPError) as platform_error:
            raise self.translate_platform_error(platform_error)
        mandate = self.store.save_mandate({
            **mandate,
            "hard_rules": find_value(platform_mandate, "hard_rules", hard_rules if hard_rules is not None else mandate["hard_rules"]),
            "uncertainty_policy": find_value(platform_mandate, "uncertainty_policy", uncertainty_policy or mandate["uncertainty_policy"]),
            "raw": platform_mandate,
        })
        self.broadcaster.publish("mandate", mandate)
        return mandate



    # Revoke the mandate, which withdraws the permission on the platform
    async def revoke_mandate(self, mandate_id = None):
        mandate = await self.current_mandate_for_change(mandate_id)
        try:
            platform_answer = await self.client.revoke_mandate(mandate["mandate_id"])
        except (LeashApiError, httpx.HTTPError) as platform_error:
            raise self.translate_platform_error(platform_error)
        mandate = self.store.save_mandate({**mandate, "status": "revoked", "raw": platform_answer})
        for run in self.store.list_runs(active_only = True):
            if run["mandate_id"] == mandate["mandate_id"]:
                self.store.save_run({**run, "status": "revoked"})
        await self.worker.refresh_runs()
        self.broadcaster.publish("mandate", mandate)
        return mandate









    #### Step 5: Start runs and answer questions ####

    # Start a scenario on the platform with the current mandate, with the worker polling before the first purchase is queued
    async def start_run(self, scenario_id, mandate_id = None):
        if mandate_id is None:
            mandate_id = self.store.get_value(CURRENT_MANDATE_KEY)
        if mandate_id is None:
            raise ServiceError(409, "No mandate is confirmed yet")
        was_polling = self.worker.polling
        self.worker.start_polling()
        try:
            run_payload = await self.client.start_scenario_run(scenario_id, mandate_id)
        except (LeashApiError, httpx.HTTPError) as platform_error:
            if not was_polling and not self.store.list_runs(active_only = True):
                self.worker.stop_polling()
            raise self.translate_platform_error(platform_error)
        run_id = find_value(run_payload, "run_id")
        if not isinstance(run_id, str):
            raise ServiceError(502, "The platform answered without a run_id - " + str(run_payload)[:300])
        run = self.store.save_run({"run_id": run_id, "scenario_id": scenario_id, "mandate_id": mandate_id, "status": "active", "raw": run_payload})
        self.audit_log.append(run_id, "run_started", {"run": run})
        self.broadcaster.publish("run", run)
        return run



    # Read one run, refreshed from the platform
    async def describe_run(self, run_id):
        run = self.store.get_run(run_id)
        if run is None:
            raise ServiceError(404, "Unknown run " + run_id)
        try:
            progress = await self.client.get_scenario_run(run_id)
            run = self.store.save_run({**run, "raw": progress, "status": self.read_run_status(progress, run["status"])})
        except (LeashApiError, httpx.HTTPError):
            pass
        return run



    # Read the status word of a progress answer
    @staticmethod
    def read_run_status(progress, current_status):
        status = find_value(progress, "status")
        if isinstance(status, str) and status != "":
            return status.lower()
        return current_status



    # Check the budget again before an approval is sent, against the run as it stands now, and return the objection as a sentence or None.
    # A question without a stored decision record, such as one about a message that could not be read, has no budget to check.
    # The sentence is a warning and never a refusal, because the customer always decides. The inbox shows it on the card, and an approval keeps it.
    def check_budget_before_approval(self, record):
        purchase_time = read_ledger_time(record.get("sim_timestamp"))
        if record.get("trace") is None or purchase_time is None:
            return None
        snapshot = build_ledger_snapshot_from_records(
            records = self.store.list_decisions(run_id = record.get("run_id")),
            run_id = record.get("run_id"),
            mandate_id = record.get("mandate_id"),
            purchase_time = purchase_time,
            shop_identifier = record.get("merchant_id"),
            own_identifier = record.get("live_authorization_id"),
            keep_later_rows = True,
            own_cart_lines = read_record_cart_lines(record),
        )
        objection = find_budget_objection(record.get("trace"), snapshot)
        if objection is None:
            return None



        # The sentence was written for a refusal, so the words that said nothing went through are left out of the warning
        return objection.removesuffix(" Nothing was approved.")



    # List the questions that wait for the customer, oldest first, each with the warning an approval would carry right now
    def list_pending(self):
        self.worker.expire_old_questions()
        return [{**record, "approval_warning": self.check_budget_before_approval(record)} for record in self.store.list_pending()]



    # Send the customer's answer to a question, and count the amount only once the platform accepted an approval
    async def resolve(self, live_authorization_id, decision, customer_message = None):
        if decision not in ("approve", "decline"):
            raise ServiceError(400, "The answer must be approve or decline")
        record = self.store.get_decision(live_authorization_id)
        if record is None:
            raise ServiceError(404, "Unknown purchase " + live_authorization_id)
        if record["status"] != "pending":
            raise ServiceError(409, "This purchase does not wait for an answer, its status is " + record["status"])
        if customer_message is None or customer_message == "":
            customer_message = "The customer confirmed this purchase." if decision == "approve" else "The customer declined this purchase."



        # Check the budget again before an approval, because two open questions can each fit and together break a limit.
        # The answer goes through either way, because the customer always decides, and the warning travels with it.
        budget_warning = self.check_budget_before_approval(record) if decision == "approve" else None



        # Send the answer, and keep the question open when the platform refuses it
        resolved_at = self.worker.read_clock()
        try:
            response = await self.client.resolve_authorization(live_authorization_id, decision, customer_message)
        except (LeashApiError, httpx.HTTPError) as platform_error:
            service_error = self.translate_platform_error(platform_error)
            self.store.save_resolution(live_authorization_id, {
                "decision": decision, "customer_message": customer_message, "resolved_at": format_moment(resolved_at),
                "posted": False, "post_status_code": service_error.status_code, "post_error": service_error.message,
            })
            raise service_error
        resolution = {
            "decision": decision,
            "customer_message": customer_message,
            "resolved_at": format_moment(resolved_at),
            "posted": True,
            "post_status_code": 200,
            "budget_warning": budget_warning,
            "response": response,
        }
        self.store.save_resolution(live_authorization_id, resolution)
        self.store.set_status(live_authorization_id, "approved" if decision == "approve" else "declined", resolution)
        record = self.store.get_decision(live_authorization_id)
        self.audit_log.append(record.get("run_id"), "resolution", {"live_authorization_id": live_authorization_id, "resolution": resolution, "status": record["status"]})
        self.broadcaster.publish("resolution", record)
        return record



    # Clear the state of the team on the platform and in the store, which is for development only
    async def reset_team(self):
        try:
            platform_answer = await self.client.reset_team()
        except (LeashApiError, httpx.HTTPError) as platform_error:
            raise self.translate_platform_error(platform_error)
        self.worker.stop_polling()
        self.worker.policies_by_key.clear()
        self.store.clear_all()
        self.broadcaster.publish("reset", {"platform": platform_answer})
        return {"platform": platform_answer}
