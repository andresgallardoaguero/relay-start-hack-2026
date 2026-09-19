# Script: live_smoke.py
# Purpose: Check the connection to the authorization service with the team key, and on request run the connection check scenario end to end
# Author: Jonas Lüthi
# Date: September 2026

import argparse
import asyncio
import json
import sys
from pathlib import Path

import pandas as pd









#### Step 1: Locate the folders and make the backend importable ####

# Locate the repository folders from the location of this file
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
BACKEND_FOLDER = REPOSITORY_FOLDER / "backend"
CASE_DATA_FOLDER = REPOSITORY_FOLDER / "data" / "raw" / "2026-09-18_viseca-2026" / "data"



# Make the backend importable
if str(BACKEND_FOLDER) not in sys.path:
    sys.path.insert(0, str(BACKEND_FOLDER))

from app.api_client.leash import LeashApiError, find_value
from app.config import get_settings
from app.service import RelayService, ServiceError



# Name the connection check scenario and how long the cycle may take
CONNECTION_CHECK_SCENARIO = "SCEN0000"
CYCLE_TIMEOUT_SECONDS = 90.0
POLL_WAIT_SECONDS = 3









#### Step 2: Print answers ####

# Print one labeled block with the answer as JSON
def print_block(title, payload):
    print("--- " + title + " ---")
    print(json.dumps(payload, indent = 2, ensure_ascii = False, default = str))
    print()



# Read the instruction of a scenario from the published catalogue, exactly as written
def read_instruction(scenario_id):
    catalogue = pd.read_csv(CASE_DATA_FOLDER / "scenario_catalogue.csv", dtype = str, keep_default_na = False)
    rows = catalogue.query("scenario_id == @scenario_id")
    assert len(rows) == 1, "Scenario " + scenario_id + " is not in the catalogue"
    return rows.iloc[0]["cardholder_instruction"]









#### Step 3: Run the checks ####

# Read the service, the settings and the reference data, which changes nothing on the platform
async def read_only_checks(service):
    print_block("healthz", await service.client.healthz())
    bootstrap_payload = await service.client.bootstrap()
    print_block("bootstrap", bootstrap_payload)
    scenarios = find_value(bootstrap_payload, "scenarios")
    if isinstance(scenarios, list):
        print("Scenarios listed by the platform - " + str(len(scenarios)))
        for scenario in scenarios:
            print("  " + json.dumps(scenario, ensure_ascii = False))
        print()
    reference_payload = await service.client.reference_data()
    print_block("reference-data keys", sorted(reference_payload.keys()) if isinstance(reference_payload, dict) else reference_payload)
    for rate_key in ("fx_rates", "fx_rates_to_chf", "currency_rates", "rates"):
        rates = find_value(reference_payload, rate_key)
        if rates is not None:
            print_block("reference-data " + rate_key, rates)
            break
    return bootstrap_payload



# Compile and confirm a mandate for the scenario, start a run and let the worker answer until the run is over
async def run_one_cycle(service, scenario_id):
    instruction = read_instruction(scenario_id)
    policy_view = await service.compile_policy(instruction, "ask")
    print_block("compiled policy", policy_view)
    draft = await service.create_draft(instruction, policy_view["hard_rules"], "ask", [], policy_view["open_questions"])
    print_block("draft", draft)
    mandate = await service.confirm_draft(draft["draft_id"])
    print_block("confirmed mandate", mandate)
    run = await service.start_run(scenario_id, mandate["mandate_id"])
    print_block("run started", run)



    # Poll with short waits until the run is over or the time is up
    waited = 0.0
    while waited < CYCLE_TIMEOUT_SECONDS:
        await service.worker.poll_once(wait_seconds = POLL_WAIT_SECONDS)
        current_run = await service.describe_run(run["run_id"])
        if current_run["status"] != "active":
            break
        waited = waited + POLL_WAIT_SECONDS
    print_block("run after the cycle", await service.describe_run(run["run_id"]))
    print_block("worker", service.worker.describe())
    for record in service.store.list_decisions(run_id = run["run_id"]):
        print_block("decision record " + record["live_authorization_id"], record)
    try:
        print_block("authorizations on the platform", await service.client.list_authorizations())
    except LeashApiError as api_error:
        print_block("authorizations on the platform", str(api_error))



# Read the options
def parse_command_line(command_line_arguments = None):
    parser = argparse.ArgumentParser(description = "Check the connection to the authorization service, and run the connection check scenario on request.")
    parser.add_argument("--run", action = "store_true", help = "Compile and confirm a mandate, start " + CONNECTION_CHECK_SCENARIO + " and answer it")
    parser.add_argument("--scenario", default = CONNECTION_CHECK_SCENARIO, help = "The scenario to run with --run")
    parser.add_argument("--reset", action = "store_true", help = "Clear the team state on the platform first, which is disabled during judging")
    parser.add_argument("--offline", action = "store_true", help = "Use the in-process copy of the platform instead of the live service")
    return parser.parse_args(command_line_arguments)



# Run the checks from the command line
async def main_async(options):
    settings = get_settings()
    if options.offline:
        settings = settings.model_copy(update = {"leash_mode": "offline"})
    elif settings.team_api_key == "":
        raise SystemExit("TEAM_API_KEY is empty. Put the team key into backend/.env, or use --offline.")
    service = RelayService.build(settings)
    await service.start()
    try:
        if options.reset:
            print_block("team reset", await service.reset_team())
        await read_only_checks(service)
        if options.run:
            await run_one_cycle(service, options.scenario)
    except (LeashApiError, ServiceError) as failure:
        print("FAILED - " + str(failure))
        raise SystemExit(1)
    finally:
        await service.stop()



def main(command_line_arguments = None):
    asyncio.run(main_async(parse_command_line(command_line_arguments)))



if __name__ == "__main__":
    main()
