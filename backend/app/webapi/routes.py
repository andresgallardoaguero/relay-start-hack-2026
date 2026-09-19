# Script: routes.py
# Purpose: Offer the three screens everything they need over HTTP, which is the policy, the inbox, the log, the runs, the status and the event stream
# Author: Jonas Lüthi
# Date: September 2026

from typing import Any, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.service import ServiceError









#### Step 1: Define the request bodies ####

# Refuse unknown fields in every body the interface sends
class StrictBody(BaseModel):
    model_config = ConfigDict(extra = "forbid")



# Ask for an instruction to be compiled, with the uncertainty policy the customer chose
class CompileBody(StrictBody):
    instruction: str = Field(min_length = 1)
    uncertainty_policy: Literal["ask", "decline", "approve"] = "ask"
    hard_rules: list[dict[str, Any]] = Field(default_factory = list)



# Ask for a draft on the platform, with the rules the customer saw
class DraftBody(StrictBody):
    instruction: str = Field(min_length = 1)
    hard_rules: list[dict[str, Any]] = Field(default_factory = list)
    uncertainty_policy: Literal["ask", "decline", "approve"] = "ask"
    guidance: list[str] = Field(default_factory = list)
    open_questions: list[str] = Field(default_factory = list)



# Confirm a draft
class ConfirmBody(StrictBody):
    draft_id: str = Field(min_length = 1)



# Tighten the mandate
class TightenBody(StrictBody):
    add_rules: list[dict[str, Any]] = Field(default_factory = list)
    uncertainty_policy: Optional[Literal["decline"]] = None



# Start a run
class RunStartBody(StrictBody):
    scenario_id: str = Field(pattern = r"^SCEN[0-9]{4}$")
    mandate_id: Optional[str] = None



# Answer a question
class ResolveBody(StrictBody):
    decision: Literal["approve", "decline"]
    customer_message: Optional[str] = None



# Start the worker
class WorkerStartBody(StrictBody):
    continuous: bool = False









#### Step 2: Define the router ####

router = APIRouter(prefix = "/api")



# Read the service the app carries
def service_of(request):
    return request.app.state.service



# Answer a refusal of the service with its status code and an error body like the platform's
def error_response(service_error):
    return JSONResponse(status_code = service_error.status_code, content = {"error": {"message": service_error.message}})









#### Step 3: The policy screen ####

# Compile an instruction and show the checks, without touching the platform
@router.post("/policy/compile")
async def compile_policy(body: CompileBody, request: Request):
    try:
        return await service_of(request).compile_policy(body.instruction, body.uncertainty_policy, body.hard_rules)
    except (ValueError, AssertionError) as compile_error:
        return error_response(ServiceError(400, "The instruction or the rules could not be read - " + str(compile_error)))



# Store a draft on the platform
@router.post("/policy/draft")
async def create_draft(body: DraftBody, request: Request):
    try:
        return await service_of(request).create_draft(body.instruction, body.hard_rules, body.uncertainty_policy, body.guidance, body.open_questions)
    except ServiceError as service_error:
        return error_response(service_error)



# Confirm a draft, which makes it the current mandate
@router.post("/policy/confirm")
async def confirm_draft(body: ConfirmBody, request: Request):
    try:
        return await service_of(request).confirm_draft(body.draft_id)
    except ServiceError as service_error:
        return error_response(service_error)



# Read the current mandate with its compiled policy
@router.get("/policy")
async def read_policy(request: Request):
    described = await service_of(request).describe_current_mandate()
    if described is None:
        return JSONResponse(status_code = 404, content = {"error": {"message": "No mandate is confirmed yet"}})
    return described



# Tighten the current mandate, where the path may name the mandate the screen holds and is refused when it is not the current one
@router.patch("/policy")
@router.patch("/policy/{mandate_id}")
async def tighten_policy(body: TightenBody, request: Request, mandate_id: Optional[str] = None):
    try:
        return await service_of(request).tighten_mandate(body.add_rules, body.uncertainty_policy, mandate_id = mandate_id)
    except ServiceError as service_error:
        return error_response(service_error)



# Revoke the current mandate, where the path may name the mandate the screen holds and is refused when it is not the current one
@router.delete("/policy")
@router.delete("/policy/{mandate_id}")
async def revoke_policy(request: Request, mandate_id: Optional[str] = None):
    try:
        return await service_of(request).revoke_mandate(mandate_id = mandate_id)
    except ServiceError as service_error:
        return error_response(service_error)









#### Step 4: The inbox and the log ####

# List the decisions in order of arrival, newest last, with the full record of each
@router.get("/decisions")
async def list_decisions(request: Request, run_id: Optional[str] = None, limit: Optional[int] = None):
    return {"decisions": service_of(request).store.list_decisions(run_id = run_id, limit = limit)}



# Read one decision
@router.get("/decisions/{live_authorization_id}")
async def read_decision(live_authorization_id: str, request: Request):
    record = service_of(request).store.get_decision(live_authorization_id)
    if record is None:
        return JSONResponse(status_code = 404, content = {"error": {"message": "Unknown purchase " + live_authorization_id}})
    return record



# List the questions that wait for the customer, each with a warning when an approval given meanwhile would now break the budget
@router.get("/pending")
async def list_pending(request: Request):
    return {"pending": service_of(request).list_pending()}



# Send the customer's answer to a question
@router.post("/resolve/{live_authorization_id}")
async def resolve(live_authorization_id: str, body: ResolveBody, request: Request):
    try:
        return await service_of(request).resolve(live_authorization_id, body.decision, body.customer_message)
    except ServiceError as service_error:
        return error_response(service_error)









#### Step 5: Runs, the worker and the status ####

# Start a scenario run with the current mandate
@router.post("/run/start")
async def start_run(body: RunStartBody, request: Request):
    try:
        return await service_of(request).start_run(body.scenario_id, body.mandate_id)
    except ServiceError as service_error:
        return error_response(service_error)



# List the runs, newest first
@router.get("/runs")
async def list_runs(request: Request):
    return {"runs": service_of(request).store.list_runs()}



# Read one run, refreshed from the platform
@router.get("/runs/{run_id}")
async def read_run(run_id: str, request: Request):
    try:
        return await service_of(request).describe_run(run_id)
    except ServiceError as service_error:
        return error_response(service_error)



# Make the worker poll, without end when asked, for a run that someone else starts
@router.post("/worker/start")
async def start_worker(body: WorkerStartBody, request: Request):
    service = service_of(request)
    service.worker.start_polling(continuous = body.continuous)
    return service.worker.describe()



# Stop the worker after its current poll
@router.post("/worker/stop")
async def stop_worker(request: Request):
    service = service_of(request)
    service.worker.stop_polling()
    return service.worker.describe()



# Describe the service, the worker and the platform settings
@router.get("/status")
async def read_status(request: Request):
    return service_of(request).describe_status()



# Read the platform settings again
@router.post("/status/refresh")
async def refresh_status(request: Request):
    service = service_of(request)
    await service.refresh_bootstrap()
    return service.describe_status()



# Clear the state of the team on the platform and in the store
@router.post("/team/reset")
async def reset_team(request: Request):
    try:
        return await service_of(request).reset_team()
    except ServiceError as service_error:
        return error_response(service_error)









#### Step 6: The event stream ####

# Push every decision, answer, status and run change to the interface as server-sent events
@router.get("/stream")
async def stream_events(request: Request):
    service = service_of(request)
    queue = service.broadcaster.subscribe()
    queue.put_nowait(("status", service.describe_status()))
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(service.broadcaster.stream(queue), media_type = "text/event-stream", headers = headers)
