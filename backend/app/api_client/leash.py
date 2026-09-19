# Script: leash.py
# Purpose: Call the authorization service with the team key, one method per endpoint, and turn every error answer into one typed exception
# Author: Jonas Lüthi
# Date: September 2026

import httpx









#### Step 1: Define the error ####

# Carry the status code, the path and the error body of an answer that was not a success
class LeashApiError(Exception):

    def __init__(self, status_code, path, error_payload):
        self.status_code = status_code
        self.path = path
        self.error_payload = error_payload
        super().__init__("The platform answered " + str(status_code) + " on " + path + " - " + describe_error_payload(error_payload))



# Write the error body of the platform as one line
def describe_error_payload(error_payload):
    if isinstance(error_payload, dict):
        error_content = error_payload.get("error", error_payload)
        if isinstance(error_content, dict):
            code = error_content.get("code", "")
            message = error_content.get("message", "")
            return (str(code) + " " + str(message)).strip() or str(error_content)
        return str(error_content)
    return str(error_payload)



# Read the body of an answer as JSON, and as text inside a dictionary when it is not JSON
def read_response_payload(response):
    if response.content == b"":
        return None
    try:
        return response.json()
    except ValueError:
        return {"error": {"code": "not_json", "message": response.text[:500]}}



# Look a field up at the top level of an answer, then one level down, because the platform may wrap its answers
def find_value(payload, field_name, default = None):
    if not isinstance(payload, dict):
        return default
    if field_name in payload:
        return payload[field_name]
    for nested_value in payload.values():
        if isinstance(nested_value, dict) and field_name in nested_value:
            return nested_value[field_name]
    return default









#### Step 2: Define the client ####

# Call the platform, where every method returns the parsed answer and raises LeashApiError on a status that is not a success.
# Network failures raise the httpx exceptions unchanged, so the caller can tell a refused answer from no answer.
class LeashClient:

    def __init__(self, base_url, team_api_key, http_client = None, timeout_seconds = 30.0):
        self.base_url = base_url.rstrip("/")
        self.team_api_key = team_api_key
        self.timeout_seconds = timeout_seconds
        self.owns_http_client = http_client is None
        if http_client is None:
            http_client = httpx.AsyncClient(base_url = self.base_url, timeout = timeout_seconds)
        self.http_client = http_client



    # Close the connection pool when this client created it
    async def close(self):
        if self.owns_http_client:
            await self.http_client.aclose()



    # Build the headers, with the bearer key on every call except the health check
    def build_headers(self, with_key = True):
        headers = {"Accept": "application/json"}
        if with_key:
            headers["Authorization"] = "Bearer " + self.team_api_key
        return headers



    # Send one request and return the parsed answer, where 204 gives None and any status outside 200 to 299 raises
    async def request(self, method, path, json_body = None, params = None, with_key = True, timeout_seconds = None):
        response = await self.http_client.request(
            method,
            path,
            json = json_body,
            params = params,
            headers = self.build_headers(with_key),
            timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
        )
        if response.status_code == 204:
            return None
        payload = read_response_payload(response)
        if not 200 <= response.status_code < 300:
            raise LeashApiError(response.status_code, path, payload)
        return payload









    #### Step 3: Read the service and the reference data ####

    # Check that the service is up, without the key
    async def healthz(self):
        return await self.request("GET", "/healthz", with_key = False)



    # Read the settings of the team, the scenario list and the timeouts
    async def bootstrap(self):
        return await self.request("GET", "/v1/bootstrap")



    # Read the small catalogues and the fixed currency rates
    async def reference_data(self):
        return await self.request("GET", "/v1/reference-data")









    #### Step 4: Create, read, tighten and revoke a mandate ####

    # Store the instruction with the structured rules as a draft
    async def create_mandate(self, instruction, hard_rules, uncertainty_policy, guidance = (), open_questions = ()):
        body = {
            "instruction": instruction,
            "hard_rules": list(hard_rules),
            "uncertainty_policy": uncertainty_policy,
            "guidance": list(guidance),
            "open_questions": list(open_questions),
        }
        return await self.request("POST", "/v1/mandates", json_body = body)



    # Record the customer's agreement, which activates the mandate
    async def confirm_mandate(self, draft_id):
        return await self.request("POST", "/v1/mandates/" + draft_id + "/confirm", json_body = {"confirmed": True})



    # Read the stored mandate
    async def get_mandate(self, mandate_id):
        return await self.request("GET", "/v1/mandates/" + mandate_id)



    # Tighten an active mandate, sending only the fields that are given
    async def patch_mandate(self, mandate_id, hard_rules = None, uncertainty_policy = None, guidance = None, open_questions = None):
        body = {}
        if hard_rules is not None:
            body["hard_rules"] = list(hard_rules)
        if uncertainty_policy is not None:
            body["uncertainty_policy"] = uncertainty_policy
        if guidance is not None:
            body["guidance"] = list(guidance)
        if open_questions is not None:
            body["open_questions"] = list(open_questions)
        return await self.request("PATCH", "/v1/mandates/" + mandate_id, json_body = body)



    # Revoke the mandate, which withdraws the permission
    async def revoke_mandate(self, mandate_id):
        return await self.request("DELETE", "/v1/mandates/" + mandate_id)









    #### Step 5: Start and read a run, receive and answer purchases ####

    # Start a scenario with an active mandate
    async def start_scenario_run(self, scenario_id, mandate_id):
        return await self.request("POST", "/v1/scenario-runs", json_body = {"scenario_id": scenario_id, "mandate_id": mandate_id})



    # Read the progress of a run
    async def get_scenario_run(self, run_id):
        return await self.request("GET", "/v1/scenario-runs/" + run_id)



    # Wait for the next purchase, up to the given seconds, and return the envelope or None when nothing came
    async def next_decision_request(self, wait_seconds = 25):
        return await self.request(
            "GET",
            "/v1/decision-requests/next",
            params = {"wait": int(wait_seconds)},
            timeout_seconds = float(wait_seconds) + 10.0,
        )



    # Send the decision of the engine for one live purchase
    async def post_decision(self, authorization_id, body):
        return await self.request("POST", "/v1/authorizations/" + authorization_id + "/decision", json_body = body, timeout_seconds = 10.0)



    # Send the customer's answer to a purchase that was paused
    async def resolve_authorization(self, authorization_id, decision, customer_message, evidence = ()):
        body = {"decision": decision, "customer_message": customer_message, "evidence": list(evidence)}
        return await self.request("POST", "/v1/authorizations/" + authorization_id + "/resolve", json_body = body, timeout_seconds = 10.0)



    # List the pending and final purchases of the team
    async def list_authorizations(self):
        return await self.request("GET", "/v1/authorizations")



    # Read the event feed from a cursor
    async def read_events(self, since = 0):
        return await self.request("GET", "/v1/events", params = {"since": since})



    # Clear the state of the team on the platform, which is disabled during judging
    async def reset_team(self):
        return await self.request("POST", "/v1/team/reset")
