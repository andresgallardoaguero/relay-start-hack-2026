# Script: live_scenario_run.py
# Purpose: Run one scenario against the local backend from the terminal and answer its questions from a fixed table
# Author: Andrés Gallardo
# Date: September 2026

import argparse
import time
from datetime import datetime, timezone

import httpx









#### Step 1: Fix the instructions, the answers and the expected results ####

# Hold the instruction of every scenario exactly as published
SCENARIO_INSTRUCTIONS = {
    "SCEN0001": "Order our household groceries for delivery. Keep each order at or below CHF 120 including delivery, and keep the total across any seven days at or below CHF 300. Ask me when uncertain.",
    "SCEN0002": "Replace my worn road-running shoes in size 43. Buy only from a specialist sports retailer, only if the order can be returned within 14 days or more, and pay no more than CHF 200. Ask me when uncertain.",
    "SCEN0003": "The agent may buy clothing for me, up to CHF 250 per order, from shops I have used before. Pause anything that looks like someone other than me is driving the session. Ask me when uncertain.",
    "SCEN0004": "Buy the 27-inch monitor I chose, from a seller I have bought from before, for CHF 400 or less. Do not add anything I did not ask for. Ask me when uncertain.",
}



# Hold the answer to every expected question, keyed by the reading, by scenario and by the delivery position of the purchase
# The reading own_facts approves a clean second order of the requested thing, the reading in_sequence asks the customer about it
QUESTION_ANSWERS_BY_READING = {
    "own_facts": {
        "SCEN0001": {3: "decline", 5: "decline", 6: "decline", 8: "decline"},
        "SCEN0002": {5: "decline", 7: "decline", 10: "decline"},
        "SCEN0003": {3: "approve", 10: "approve", 11: "decline"},
        "SCEN0004": {2: "decline", 6: "decline", 10: "decline"},
    },
    "in_sequence": {
        "SCEN0001": {3: "decline", 5: "decline", 6: "decline", 8: "decline"},
        "SCEN0002": {5: "decline", 7: "decline", 8: "approve", 10: "decline", 12: "approve"},
        "SCEN0003": {3: "approve", 10: "approve", 11: "decline"},
        "SCEN0004": {2: "decline", 4: "decline", 6: "decline", 8: "decline", 10: "decline", 11: "decline"},
    },
}



# Hold the expected decisions of every scenario in delivery order, where A is an approval, Q a question and D a refusal
EXPECTED_DECISION_LETTERS_BY_READING = {
    "own_facts": {
        "SCEN0001": "A A Q A Q Q A Q D A",
        "SCEN0002": "A D D D Q D Q A D Q D A",
        "SCEN0003": "A A Q D D D D A A Q Q",
        "SCEN0004": "A Q D A D Q D A D Q A",
    },
    "in_sequence": {
        "SCEN0001": "A A Q A Q Q A Q D A",
        "SCEN0002": "A D D D Q D Q Q D Q D Q",
        "SCEN0003": "A A Q D D D D A A Q Q",
        "SCEN0004": "A Q D Q D Q D Q D Q Q",
    },
}
DEFAULT_READING = "own_facts"



# Name the letter of every decision of the engine
DECISION_LETTERS = {"approve": "A", "step_up": "Q", "decline": "D"}



# Name every status that says a run is over. Any other status, such as active or running, means the run is still open
FINISHED_RUN_STATUSES = ("completed", "finished", "done", "cancelled", "failed", "expired")



# Fix the pace of the polling, how long an answer is retried and how long the script watches a run at most
DEFAULT_BASE_URL = "http://localhost:8000"
UNCERTAINTY_POLICY = "ask"
POLL_INTERVAL_SECONDS = 1.0
RESOLVE_RETRY_LIMIT_SECONDS = 100.0
MAXIMUM_WATCH_SECONDS = 1800.0
SECONDS_OF_POLLING_AFTER_THE_RUN = 5.0
HTTP_TIMEOUT_SECONDS = 20.0









#### Step 2: Small helpers ####

# Read the options
def parse_command_line(command_line_arguments = None):
    parser = argparse.ArgumentParser(description = "Run one scenario against the local backend and answer its questions from a fixed table.")
    parser.add_argument("--scenario", required = True, choices = sorted(SCENARIO_INSTRUCTIONS.keys()), help = "The scenario to run")
    parser.add_argument("--base-url", default = DEFAULT_BASE_URL, help = "The address of the local backend")
    parser.add_argument("--reading", default = DEFAULT_READING, choices = sorted(QUESTION_ANSWERS_BY_READING.keys()), help = "The reading that chooses the expected line and the answer table")
    return parser.parse_args(command_line_arguments)



# Say whether a run is still open. Only a status that names the end closes it, so an unknown word keeps the questions answered
def run_is_open(run_status):
    return str(run_status).strip().lower() not in FINISHED_RUN_STATUSES



# Stop the script with one line that says why
def stop_with_message(message):
    print("STOPPED - " + message)
    raise SystemExit(1)



# Send one call that is never repeated, and stop the script when it fails
def call_once(client, method, path, body = None):
    try:
        response = client.request(method, path, json = body)
    except httpx.HTTPError as transport_error:
        stop_with_message(method + " " + path + " failed - " + repr(transport_error))
    if response.status_code != 200:
        stop_with_message(method + " " + path + " answered " + str(response.status_code) + " - " + response.text[:300])
    return response.json()



# Read the delivery position of a purchase from its decision record, or nothing when the record does not carry a whole number
def read_delivery_position(record):
    try:
        return int(record.get("replay_order"))
    except (TypeError, ValueError):
        return None



# Count the seconds a question has waited since the purchase arrived
def seconds_waited(record):
    received_at = datetime.fromisoformat(record["received_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - received_at).total_seconds()









#### Step 3: Check the backend, confirm the mandate and start the run ####

# Stop unless the worker is idle, no run is open and the mandate is compiled without a model
def check_status(client):
    status = call_once(client, "GET", "/api/status")
    print("--- Backend status ---")
    print("leash_mode - " + str(status["leash_mode"]))
    print("llm_mode - " + str(status["llm_mode"]))
    print("team_key_present - " + str(status["team_key_present"]))
    print("worker state - " + str(status["worker"]["state"]))
    print("open runs - " + str(status["worker"]["active_run_ids"]))
    print()
    if status["worker"]["state"] != "idle":
        stop_with_message("the worker is not idle")
    if status["worker"]["active_run_ids"] != []:
        stop_with_message("a run is open")
    if status["llm_mode"] != "off":
        stop_with_message("llm_mode is not off")



# Compile the instruction, store the draft with the compiled rules and confirm it, the same way the policy screen does
def confirm_mandate(client, instruction):
    compiled = call_once(client, "POST", "/api/policy/compile", {"instruction": instruction, "uncertainty_policy": UNCERTAINTY_POLICY, "hard_rules": []})
    draft_body = {
        "instruction": instruction,
        "hard_rules": compiled["hard_rules"],
        "uncertainty_policy": UNCERTAINTY_POLICY,
        "guidance": [],
        "open_questions": compiled["open_questions"],
    }
    draft = call_once(client, "POST", "/api/policy/draft", draft_body)
    mandate = call_once(client, "POST", "/api/policy/confirm", {"draft_id": draft["draft_id"]})
    assert mandate["instruction"] == instruction, "The confirmed mandate does not carry the instruction word for word"
    print("--- Mandate ---")
    print("mandate_id - " + str(mandate["mandate_id"]))
    print("rules - " + str(len(mandate["hard_rules"])) + ", open questions - " + str(len(compiled["open_questions"])))
    print()
    return mandate



# Start the run with exactly one call, which is never repeated
def start_run(client, scenario_id, mandate_id):
    try:
        response = client.post("/api/run/start", json = {"scenario_id": scenario_id, "mandate_id": mandate_id})
    except httpx.HTTPError as transport_error:
        stop_with_message("the start call failed and is not repeated. A run may be open on the platform - " + repr(transport_error))
    if response.status_code != 200:
        stop_with_message("the start was refused with " + str(response.status_code) + " and is not repeated - " + response.text[:300])
    run = response.json()
    print("--- Run ---")
    print("run_id - " + str(run["run_id"]))
    print()
    return run









#### Step 4: Answer the questions until the run is over ####

# Answer one open question from the table and say whether the answer arrived
def answer_question(client, question_answers, record):
    position = read_delivery_position(record)
    answer = question_answers.get(position)
    label = "ANSWER"
    if answer is None:
        answer = "decline"
        label = "UNEXPECTED"
    print(
        label + " position " + str(position)
        + " - CHF " + str(record.get("amount_chf"))
        + " - " + str(record.get("merchant_name"))
        + " - " + ", ".join(record.get("reason_codes") or [])
        + " - waited " + format(seconds_waited(record), ".1f") + " s"
        + " - " + answer
    )
    try:
        response = client.post("/api/resolve/" + record["live_authorization_id"], json = {"decision": answer})
    except httpx.HTTPError as transport_error:
        print("  the answer failed - " + repr(transport_error))
        return False
    if response.status_code == 200:
        print("  accepted, status " + str(response.json().get("status")))
        return True
    if response.status_code == 409:
        print("  the question no longer waits for an answer - " + response.text[:300])
        return True
    print("  the answer failed with " + str(response.status_code) + " - " + response.text[:300])
    return False



# Answer every open question of this run once, and give up on a question that has waited 100 seconds
def answer_open_questions(client, question_answers, run_id, abandoned_question_ids):
    pending_records = client.get("/api/pending").json()["pending"]
    for record in pending_records:
        question_id = record["live_authorization_id"]
        if record.get("run_id") != run_id or question_id in abandoned_question_ids:
            continue
        if seconds_waited(record) >= RESOLVE_RETRY_LIMIT_SECONDS:
            print("GIVEN UP position " + str(read_delivery_position(record)) + " - the question waited 100 seconds without an accepted answer")
            abandoned_question_ids.add(question_id)
            continue
        answer_question(client, question_answers, record)



# Poll the open questions every second while the run is open, and repeat a failed answer until the question has waited 100 seconds
def answer_until_run_is_over(client, question_answers, run_id):
    print("--- Questions ---")
    abandoned_question_ids = set()
    watch_started = time.monotonic()
    run_status = "active"
    while run_is_open(run_status) and time.monotonic() - watch_started < MAXIMUM_WATCH_SECONDS:
        try:
            answer_open_questions(client, question_answers, run_id, abandoned_question_ids)
            run_status = client.get("/api/runs/" + run_id).json()["status"]
        except (httpx.HTTPError, KeyError, ValueError) as poll_error:
            print("  this poll failed, the next one follows - " + repr(poll_error))
        time.sleep(POLL_INTERVAL_SECONDS)



    # Keep polling for a few more seconds after the run is over, so no question of this run is left behind
    polling_after_the_run_started = time.monotonic()
    while time.monotonic() - polling_after_the_run_started < SECONDS_OF_POLLING_AFTER_THE_RUN:
        try:
            answer_open_questions(client, question_answers, run_id, abandoned_question_ids)
        except (httpx.HTTPError, KeyError, ValueError) as poll_error:
            print("  this poll failed, the next one follows - " + repr(poll_error))
        time.sleep(POLL_INTERVAL_SECONDS)
    print()
    return run_status









#### Step 5: Compare the run with the expected result ####

# Print the decisions in delivery order, compare them with the expected line and say how every question ended
def report_run(client, scenario_id, run_id, run_status, expected_line, question_answers):
    decisions = call_once(client, "GET", "/api/decisions?run_id=" + run_id)["decisions"]
    ordered_records = sorted(decisions, key = lambda record: (read_delivery_position(record) is None, read_delivery_position(record) or 0))
    actual_letters = [DECISION_LETTERS.get(record.get("decision"), "?") for record in ordered_records]
    expected_letters = expected_line.split(" ")
    print("--- Result of " + scenario_id + ", run " + run_id + " ---")
    print("run status - " + run_status)
    print("expected - " + " ".join(expected_letters))
    print("actual   - " + " ".join(actual_letters))
    if run_is_open(run_status):
        print("NOT OVER - the watch ended while the run was still open, with status " + run_status)
    elif run_status != "completed":
        print("NOT COMPLETED - the run is over with status " + run_status)
    if actual_letters == expected_letters:
        print("MATCH")
    else:
        longest = max(len(actual_letters), len(expected_letters))
        padded_actual = actual_letters + ["-"] * (longest - len(actual_letters))
        padded_expected = expected_letters + ["-"] * (longest - len(expected_letters))
        differences = [
            "position " + str(index + 1) + " expected " + padded_expected[index] + " got " + padded_actual[index]
            for index in range(longest)
            if padded_actual[index] != padded_expected[index]
        ]
        print("DIFFERENT - " + ", ".join(differences))
    print()



    # Say how every question ended, with the answer that was given and whether the platform took it
    print("--- How every question ended ---")
    question_records = [record for record in ordered_records if record.get("decision") == "step_up"]
    for record in question_records:
        resolution = record.get("resolution") or {}
        print(
            "position " + str(read_delivery_position(record))
            + " - CHF " + str(record.get("amount_chf"))
            + " - " + str(record.get("merchant_name"))
            + " - status " + str(record.get("status"))
            + " - answer " + str(resolution.get("decision"))
            + " - posted " + str(resolution.get("posted"))
        )
    print("questions - " + str(len(question_records)) + ", expected - " + str(len(question_answers)))
    print()









#### Step 6: Run the scenario ####

def main(command_line_arguments = None):
    options = parse_command_line(command_line_arguments)
    question_answers = QUESTION_ANSWERS_BY_READING[options.reading][options.scenario]
    expected_line = EXPECTED_DECISION_LETTERS_BY_READING[options.reading][options.scenario]
    print("--- Reading ---")
    print("reading - " + options.reading)
    print("expected - " + expected_line)
    print("answers - " + ", ".join(str(position) + " " + answer for position, answer in sorted(question_answers.items())))
    print()
    with httpx.Client(base_url = options.base_url, timeout = HTTP_TIMEOUT_SECONDS) as client:
        check_status(client)
        mandate = confirm_mandate(client, SCENARIO_INSTRUCTIONS[options.scenario])
        run = start_run(client, options.scenario, mandate["mandate_id"])
        run_status = answer_until_run_is_over(client, question_answers, run["run_id"])
        report_run(client, options.scenario, run["run_id"], run_status, expected_line, question_answers)



if __name__ == "__main__":
    main()
