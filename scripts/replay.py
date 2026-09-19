# Script: replay.py
# Purpose: Replay the public purchases offline as live-shaped messages, decide each one in order and write a result table
# Author: Andrés Gallardo
# Date: September 2026

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd
from jsonschema import Draft202012Validator









#### Step 1: Define the paths and the run-level configuration ####

# Locate the repository folders from the location of this file
REPOSITORY_FOLDER = Path(__file__).resolve().parent.parent
BACKEND_FOLDER = REPOSITORY_FOLDER / "backend"
CASE_DATA_FOLDER = REPOSITORY_FOLDER / "data" / "raw" / "2026-09-18_viseca-2026" / "data"
EVENT_SCHEMA_PATH = CASE_DATA_FOLDER / "schemas" / "authorization_event.schema.json"
OUTPUT_FOLDER = REPOSITORY_FOLDER / "outputs" / "replay"



# Make the backend importable, so the strict message reader and the engine can be used from this folder
if str(BACKEND_FOLDER) not in sys.path:
    sys.path.insert(0, str(BACKEND_FOLDER))

from app.config import get_settings
from app.engine.guards.registry import build_guard_pipeline, list_placeholder_guard_ids
from app.engine.pipeline import decide
from app.models.events import InvalidEventError, read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.state.ledger import build_ledger_snapshot_for_event, find_budget_objection



# State the row counts the published files must have
EXPECTED_ATTEMPT_COUNT = 45
EXPECTED_CART_LINE_COUNT = 56
EXPECTED_MERCHANT_COUNT = 58
EXPECTED_SCENARIO_COUNT = 5
EXPECTED_AUTHORITY_COUNT = 5



# State the fixed values of the runtime block and the length of the automated answer window
HISTORY_WINDOW_MINUTES = 10
CONTEXT_BASIS = "run_decisions_and_scenario_timestamps"
DECISION_DEADLINE_SECONDS = 8



# State the closed value lists of the replay
ALLOWED_DECISIONS = ("approve", "decline", "step_up")
ALLOWED_RESOLVE_MODES = ("ask", "approve", "decline")
ALLOWED_UNCERTAINTY_POLICIES = ("ask", "decline", "approve")



# Name a run that the engine decides, which stands in the file names where the baseline name stands otherwise
ENGINE_RUN_NAME = "engine"



# Name the two columns of the reference decisions. One judges every purchase on its own facts,
# and the other judges it in the sequence of its run, where a second order of the one requested thing asks the customer.
REFERENCE_COLUMN_ON_OWN_FACTS = "decision_on_own_facts"
REFERENCE_COLUMN_IN_SEQUENCE = "decision_in_sequence"
REFERENCE_COLUMNS = (REFERENCE_COLUMN_ON_OWN_FACTS, REFERENCE_COLUMN_IN_SEQUENCE)



# Find an amount written as CHF followed by a number, with optional thousands separators and decimals
ORDER_LIMIT_PATTERN = re.compile(r"CHF\s*([0-9][0-9,']*(?:\.[0-9]+)?)")









#### Step 2: Load and join the published tables ####

# Hold the joined purchase rows and the cart lines of every purchase
@dataclass(frozen = True)
class ReplayTables:
    joined_attempts: pd.DataFrame
    cart_lines_by_source_id: dict



# Read one published table with every cell as text, so "false", "5411" and an empty cell stay exactly as written
def read_case_table(file_name, data_folder):
    return pd.read_csv(data_folder / file_name, dtype = str, keep_default_na = False)



# Load the five tables, check their row counts and join them on identifiers only
def load_replay_tables(data_folder = CASE_DATA_FOLDER):

    # Read the five tables as text
    attempts = read_case_table("purchase_attempts.csv", data_folder)
    cart_lines = read_case_table("purchase_attempt_items.csv", data_folder)
    merchants = read_case_table("merchants.csv", data_folder)
    scenarios = read_case_table("scenario_catalogue.csv", data_folder)
    authorities = read_case_table("scenario_authorities.csv", data_folder)



    # Check the row counts against the published figures
    assert len(attempts) == EXPECTED_ATTEMPT_COUNT, "Expected 45 purchase attempts, found " + str(len(attempts))
    assert len(cart_lines) == EXPECTED_CART_LINE_COUNT, "Expected 56 cart lines, found " + str(len(cart_lines))
    assert len(merchants) == EXPECTED_MERCHANT_COUNT, "Expected 58 merchants, found " + str(len(merchants))
    assert len(scenarios) == EXPECTED_SCENARIO_COUNT, "Expected 5 scenarios, found " + str(len(scenarios))
    assert len(authorities) == EXPECTED_AUTHORITY_COUNT, "Expected 5 authorities, found " + str(len(authorities))



    # Keep only the needed columns of the authority and the scenario, under names that cannot collide
    authority_identities = (
        authorities
        .loc[:, ["authority_id", "customer_id", "card_id"]]
        .rename(columns = {"customer_id": "authority_customer_id", "card_id": "authority_card_id"})
    )
    scenario_instructions = (
        scenarios
        .loc[:, ["scenario_id", "cardholder_instruction"]]
    )



    # Join the purchases to their shop, authority and instruction, and put them in delivery order
    joined_attempts = (
        attempts
        .merge(merchants, on = "merchant_id", how = "left", validate = "many_to_one", indicator = "merchant_join")
        .merge(authority_identities, on = "authority_id", how = "left", validate = "many_to_one", indicator = "authority_join")
        .merge(scenario_instructions, on = "scenario_id", how = "left", validate = "many_to_one", indicator = "scenario_join")
        .assign(replay_order_number = lambda table: table["replay_order"].astype(int))
        .sort_values(["scenario_id", "replay_order_number"])
        .reset_index(drop = True)
    )



    # Check that every purchase found its shop, its authority and its instruction
    assert len(joined_attempts) == EXPECTED_ATTEMPT_COUNT, "The joins changed the number of purchases"
    assert (joined_attempts["merchant_join"] == "both").all(), "A purchase has no matching merchant"
    assert (joined_attempts["authority_join"] == "both").all(), "A purchase has no matching authority"
    assert (joined_attempts["scenario_join"] == "both").all(), "A purchase has no matching scenario"
    assert (joined_attempts["card_id"] == joined_attempts["authority_card_id"]).all(), "A purchase uses a card other than the card of its authority"



    # Check that every cart line belongs to a purchase and every purchase has at least one cart line
    attempt_identifiers = set(joined_attempts["authorization_id"])
    cart_line_identifiers = set(cart_lines["authorization_id"])
    assert cart_line_identifiers <= attempt_identifiers, "A cart line points to an unknown purchase"
    assert attempt_identifiers <= cart_line_identifiers, "A purchase has no cart line"



    # Group the cart lines by purchase, in line order
    ordered_cart_lines = (
        cart_lines
        .assign(line_number = lambda table: table["line_no"].astype(int))
        .sort_values(["authorization_id", "line_number"])
        .drop(columns = ["line_number"])
    )
    cart_lines_by_source_id = {
        source_authorization_id: lines_of_one_purchase.to_dict("records")
        for source_authorization_id, lines_of_one_purchase in ordered_cart_lines.groupby("authorization_id")
    }

    return ReplayTables(
        joined_attempts = joined_attempts.drop(columns = ["merchant_join", "authority_join", "scenario_join"]),
        cart_lines_by_source_id = cart_lines_by_source_id,
    )



# List the scenario identifiers of the loaded purchases in ascending order
def list_scenario_ids(replay_tables):
    return sorted(replay_tables.joined_attempts["scenario_id"].unique())









#### Step 3: Define the identifiers, the clock and the state of one run ####

# Make a fresh live purchase identifier, which a caller may replace with its own factory
def make_live_authorization_id():
    return "LA-" + uuid.uuid4().hex[:12]



# Make a fresh local identifier with a readable prefix
def make_local_identifier(prefix):
    return prefix + uuid.uuid4().hex[:12]



# Write a real clock moment the way the live messages write it
def format_utc_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")



# Read a simulated timestamp from its text, where a trailing Z means UTC
def read_simulated_time(timestamp_text):
    return datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))



# Keep what one run remembers, which is its local identifiers, the live identifier of every delivered purchase and their outcomes.
# Each earlier purchase is a dictionary with the keys authorization_id, timestamp, merchant_id, billing_amount_chf and status,
# and with its cart under cart_lines, a list of dictionaries with item_id, item_name and quantity.
@dataclass
class ReplayRunState:
    mandate_id: str
    profile_id: str
    live_id_by_source_id: dict = field(default_factory = dict)
    earlier_purchases: list = field(default_factory = list)









#### Step 4: Build one live-shaped message ####

# Turn an empty cell into None and any other cell into a number
def read_optional_amount(cell_text):
    if cell_text == "":
        return None
    return float(cell_text)



# Turn an empty cell into None and keep any other cell as text
def read_optional_text(cell_text):
    if cell_text == "":
        return None
    return cell_text



# Build one cart line with numbers where the live message carries numbers
def build_cart_line(cart_line):
    return {
        "line_no": int(cart_line["line_no"]),
        "item_id": cart_line["item_id"],
        "item_name": cart_line["item_name"],
        "item_category": cart_line["item_category"],
        "quantity": int(cart_line["quantity"]),
        "unit_price": float(cart_line["unit_price"]),
        "currency": cart_line["currency"],
        "item_details": cart_line["item_details"],
    }



# Name the five keys the message format allows for one recent purchase. The format forbids any other key,
# so the cart that the run remembers next to them never travels in a message.
RECENT_AUTHORIZATION_KEYS = ("authorization_id", "timestamp", "merchant_id", "billing_amount_chf", "status")



# List the earlier purchases of the run inside the ten minutes before the current purchase, whatever their status, each with the five allowed keys only
def build_recent_authorizations(run_state, current_timestamp_text):
    current_time = read_simulated_time(current_timestamp_text)
    window_start = current_time - timedelta(minutes = HISTORY_WINDOW_MINUTES)
    return [
        {key: earlier_purchase[key] for key in RECENT_AUTHORIZATION_KEYS}
        for earlier_purchase in run_state.earlier_purchases
        if window_start <= read_simulated_time(earlier_purchase["timestamp"]) < current_time
    ]



# Sum the purchases finally approved earlier in the run, in exact decimals rounded to two places
def sum_approved_spend(run_state):
    approved_amounts = [
        Decimal(str(earlier_purchase["billing_amount_chf"]))
        for earlier_purchase in run_state.earlier_purchases
        if earlier_purchase["status"] == "approved"
    ]
    return float(sum(approved_amounts, Decimal("0")).quantize(Decimal("0.01")))



# Rewrite the identifier of a related purchase to the live identifier it received earlier in the same run
def read_related_live_id(attempt, run_state):
    related_source_id = attempt["related_authorization_id"]
    if related_source_id == "":
        return None
    assert related_source_id in run_state.live_id_by_source_id, (
        "Purchase " + attempt["authorization_id"] + " points to " + related_source_id + ", which was not delivered earlier in this run"
    )
    return run_state.live_id_by_source_id[related_source_id]



# Build the complete message of one purchase, field by field in the order of the published example
def build_purchase_message(attempt, cart_lines, run_state, uncertainty_policy, live_authorization_id, received_at):

    # Build the context from the earlier decisions of this run
    recent_authorizations = build_recent_authorizations(run_state, attempt["timestamp"])
    recent_attempt_count = int(attempt["recent_attempt_count_10m"])
    assert len(recent_authorizations) == recent_attempt_count, (
        "Purchase " + attempt["authorization_id"] + " has " + str(len(recent_authorizations))
        + " earlier purchases inside ten minutes, but the file states " + str(recent_attempt_count)
    )



    # Assemble the message, with real clock times only in deadline_at and received_at
    return {
        "type": "authorization.request",
        "request_id": make_local_identifier("req_local_"),
        "deadline_at": format_utc_time(received_at + timedelta(seconds = DECISION_DEADLINE_SECONDS)),
        "authorization": {
            "authorization_id": live_authorization_id,
            "source_authorization_id": attempt["authorization_id"],
            "scenario_id": attempt["scenario_id"],
            "replay_order": int(attempt["replay_order"]),
            "mandate_id": run_state.mandate_id,
            "profile_id": run_state.profile_id,
            "card_id": attempt["card_id"],
            "initiator_type": "agent",
            "merchant": {
                "merchant_id": attempt["merchant_id"],
                "merchant_name": attempt["merchant_name"],
                "merchant_category": attempt["merchant_category"],
                "merchant_mcc": attempt["merchant_mcc"],
                "merchant_country": attempt["merchant_country"],
                "merchant_city": attempt["merchant_city"],
                "availability": attempt["availability"],
                "recurring_capable": attempt["recurring_capable"],
            },
            "timestamp": attempt["timestamp"],
            "amount": float(attempt["amount"]),
            "currency": attempt["currency"],
            "billing_amount_chf": float(attempt["billing_amount_chf"]),
            "items_subtotal": float(attempt["items_subtotal"]),
            "delivery_fee": float(attempt["delivery_fee"]),
            "channel": attempt["channel"],
            "customer_device_id": attempt["customer_device_id"],
            "authority_status": attempt["authority_status"],
            "card_status_at_attempt": attempt["card_status_at_attempt"],
            "spend_in_period_before_chf": read_optional_amount(attempt["spend_in_period_before_chf"]),
            "recent_attempt_count_10m": recent_attempt_count,
            "fulfillment_method": attempt["fulfillment_method"],
            "delivery_by": read_optional_text(attempt["delivery_by"]),
            "order_returnable": attempt["order_returnable"],
            "order_cancellable": attempt["order_cancellable"],
            "related_authorization_id": read_related_live_id(attempt, run_state),
            "related_authorization_status": read_optional_text(attempt["related_authorization_status"]),
            "purchase_description": attempt["purchase_description"],
            "items": [build_cart_line(cart_line) for cart_line in cart_lines],
        },
        "mandate": {
            "mandate_id": run_state.mandate_id,
            "status": "active",
            "customer_id": attempt["authority_customer_id"],
            "card_id": attempt["authority_card_id"],
            "instruction": attempt["cardholder_instruction"],
            "hard_rules": [],
            "uncertainty_policy": uncertainty_policy,
            "profile_id": run_state.profile_id,
        },
        "context": {
            "approved_spend_in_period_chf": sum_approved_spend(run_state),
            "recent_authorizations": recent_authorizations,
        },
        "runtime": {
            "received_at": format_utc_time(received_at),
            "history_window_minutes": HISTORY_WINDOW_MINUTES,
            "context_basis": CONTEXT_BASIS,
        },
    }



# Build the validator of the published message schema
def build_event_schema_validator(schema_path = EVENT_SCHEMA_PATH):
    event_schema = json.loads(schema_path.read_text(encoding = "utf-8"))
    Draft202012Validator.check_schema(event_schema)
    return Draft202012Validator(event_schema)



# Validate one message against the published schema and with the strict reader, and return the typed event
def validate_purchase_message(message, event_schema_validator):
    source_authorization_id = message["authorization"]["source_authorization_id"]

    # Stop the replay when the published schema rejects the message
    schema_problems = [
        "/".join(str(path_part) for path_part in schema_error.absolute_path) + " - " + schema_error.message
        for schema_error in event_schema_validator.iter_errors(message)
    ]
    if schema_problems:
        raise ValueError("Purchase " + source_authorization_id + " fails the published schema - " + " | ".join(sorted(schema_problems)))



    # Stop the replay when the strict reader rejects the message
    try:
        return read_purchase_message(message)
    except InvalidEventError as invalid_event_error:
        raise ValueError("Purchase " + source_authorization_id + " fails the strict reader - " + str(invalid_event_error)) from invalid_event_error









#### Step 5: Define the decision seam, the two baselines and the engine ####

# Describe the answer of a decision function, where trace holds the full decision record as a plain dictionary when there is one
@dataclass(frozen = True)
class ReplayDecision:
    decision: str
    reason_codes: tuple
    customer_message: str
    trace: Optional[dict] = None



# A decision function takes the typed event and the earlier purchases of the same run, and returns a ReplayDecision.
# It judges the purchase on its facts and never reads scenario_id, request_id, any authorization identifier or replay_order.
# The engine copies identifiers into its decision record for logging and decides nothing with them.
# The two baselines judge every purchase alone and ignore the earlier purchases.



# Approve every purchase, which shows what happens without any control
def decide_with_no_control(event, earlier_purchases):
    return ReplayDecision(
        decision = "approve",
        reason_codes = ("NO_CONTROL",),
        customer_message = "Approved without any check.",
    )



# Read the order limit for the limit baseline, which is the first amount written as CHF and a number in the instruction
def read_order_limit_for_baseline(instruction):
    limit_match = ORDER_LIMIT_PATTERN.search(instruction)
    if limit_match is None:
        raise ValueError("The instruction states no amount written as CHF followed by a number - " + instruction)
    limit_text = limit_match.group(1).replace(",", "").replace("'", "")
    return Decimal(limit_text)



# Decline a purchase above the order limit and approve every other purchase, where exactly the limit passes
def decide_with_order_limit_only(event, earlier_purchases):
    order_limit = read_order_limit_for_baseline(event.mandate.instruction)
    billing_amount = Decimal(str(event.authorization.billing_amount_chf))
    if billing_amount > order_limit:
        return ReplayDecision(
            decision = "decline",
            reason_codes = ("OVER_ORDER_LIMIT",),
            customer_message = f"Declined because CHF {billing_amount:.2f} is above the order limit of CHF {order_limit:.2f}.",
        )
    return ReplayDecision(
        decision = "approve",
        reason_codes = ("WITHIN_ORDER_LIMIT",),
        customer_message = f"Approved because CHF {billing_amount:.2f} is within the order limit of CHF {order_limit:.2f}.",
    )



# Keep the policy of every mandate the engine has seen in this process, so each policy is built once
POLICIES_BY_MANDATE_ID = {}



# Let the engine decide, with the policy built from the mandate inside the message and the memory of the run built from its earlier purchases
def decide_with_engine(event, earlier_purchases):

    # Build the policy on the first purchase of a mandate and reuse it afterwards
    mandate_id = event.mandate.mandate_id
    if mandate_id not in POLICIES_BY_MANDATE_ID:
        POLICIES_BY_MANDATE_ID[mandate_id] = build_policy_from_mandate(event.mandate)



    # Freeze the earlier purchases of the run as they stand now, so the guards see a snapshot and never the run itself
    ledger_snapshot = build_ledger_snapshot_for_event(earlier_purchases, event)



    # Run the guard pipeline and hand back the answer together with the full record
    decision_trace = decide(event, POLICIES_BY_MANDATE_ID[mandate_id], ledger_snapshot)
    return ReplayDecision(
        decision = decision_trace.decision.value,
        reason_codes = tuple(reason_code.value for reason_code in decision_trace.reason_codes),
        customer_message = decision_trace.customer_message,
        trace = decision_trace.model_dump(mode = "json"),
    )



# This is the seam. The replay loop calls whatever function is chosen here and knows nothing else about it.
DECISION_FUNCTIONS_BY_BASELINE = {
    "none": decide_with_no_control,
    "limit_only": decide_with_order_limit_only,
}



# Choose the decision function, which is the engine when no baseline is named
def choose_decision_function(baseline_name = None):
    if baseline_name is None:
        return decide_with_engine
    assert baseline_name in DECISION_FUNCTIONS_BY_BASELINE, "Unknown baseline " + baseline_name
    return DECISION_FUNCTIONS_BY_BASELINE[baseline_name]



# Count the guards of the engine that have no logic yet, and the guards in total
def count_guards_not_built():
    guards = build_guard_pipeline()
    return len(list_placeholder_guard_ids(guards)), len(guards)









#### Step 6: Replay the purchases in order and keep the state of the run ####

# Turn a decision into the final status of the purchase, with the simulated customer answering every step_up.
# An approval of the customer goes through only when the budget raises no objection at that moment, and the question stays open otherwise.
def resolve_final_status(decision_name, resolve_mode, budget_objection = None):
    if decision_name == "approve":
        return "approved"
    if decision_name == "decline":
        return "declined"
    if resolve_mode == "ask":
        return "pending"
    if resolve_mode == "approve":
        return "approved" if budget_objection is None else "pending"
    return "declined"



# Check the budget again at the moment the simulated customer approves a question, against the run as it stands then
def find_objection_to_an_approval(replay_decision, resolve_mode, earlier_purchases, event):
    if replay_decision.decision != "step_up" or resolve_mode != "approve":
        return None
    snapshot_at_the_answer = build_ledger_snapshot_for_event(earlier_purchases, event, keep_later_rows = True)
    return find_budget_objection(replay_decision.trace, snapshot_at_the_answer)



# Replay the purchases of one scenario as one run, one after the other
def replay_one_scenario(scenario_attempts, cart_lines_by_source_id, decide_purchase, resolve_mode, uncertainty_policy, make_live_id, event_schema_validator):

    # Check that the purchases arrive in delivery order, starting at one without gaps
    replay_orders = scenario_attempts["replay_order_number"].tolist()
    assert replay_orders == list(range(1, len(replay_orders) + 1)), "The replay order of a scenario must run from 1 upward without gaps"



    # Start the run with fresh local identifiers and no earlier purchases
    run_state = ReplayRunState(
        mandate_id = make_local_identifier("TM-LOCAL-"),
        profile_id = make_local_identifier("PROFILE-LOCAL-"),
    )
    purchase_results = []



    # Decide the purchases one after the other, because each message depends on the earlier decisions
    for attempt in scenario_attempts.to_dict("records"):

        # Build the message with a fresh live identifier and validate it twice
        source_authorization_id = attempt["authorization_id"]
        live_authorization_id = make_live_id()
        message = build_purchase_message(
            attempt = attempt,
            cart_lines = cart_lines_by_source_id[source_authorization_id],
            run_state = run_state,
            uncertainty_policy = uncertainty_policy,
            live_authorization_id = live_authorization_id,
            received_at = datetime.now(timezone.utc),
        )
        event = validate_purchase_message(message, event_schema_validator)



        # Call the decision function and time it
        started_at = time.perf_counter()
        replay_decision = decide_purchase(event, run_state.earlier_purchases)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
        assert replay_decision.decision in ALLOWED_DECISIONS, "Purchase " + source_authorization_id + " received the unknown decision " + str(replay_decision.decision)



        # Let the simulated customer answer a step_up and remember the outcome for the later purchases
        budget_objection = find_objection_to_an_approval(replay_decision, resolve_mode, run_state.earlier_purchases, event)
        final_status = resolve_final_status(replay_decision.decision, resolve_mode, budget_objection)
        run_state.live_id_by_source_id[source_authorization_id] = live_authorization_id
        run_state.earlier_purchases.append({
            "authorization_id": live_authorization_id,
            "timestamp": attempt["timestamp"],
            "merchant_id": attempt["merchant_id"],
            "billing_amount_chf": message["authorization"]["billing_amount_chf"],
            "status": final_status,
            "cart_lines": [
                {"item_id": cart_line["item_id"], "item_name": cart_line["item_name"], "quantity": cart_line["quantity"]}
                for cart_line in message["authorization"]["items"]
            ],
        })



        # Keep the record for the result table, where the scenario and the order appear for the table only
        record = {
            "scenario_id": attempt["scenario_id"],
            "replay_order": message["authorization"]["replay_order"],
            "source_authorization_id": source_authorization_id,
            "authorization_id": live_authorization_id,
            "merchant_name": attempt["merchant_name"],
            "billing_amount_chf": message["authorization"]["billing_amount_chf"],
            "decision": replay_decision.decision,
            "final_status": final_status,
            "reason_codes": list(replay_decision.reason_codes),
            "customer_message": replay_decision.customer_message,
            "elapsed_ms": elapsed_ms,
        }



        # Attach the full decision record when the decision function gave one, for the line-per-purchase file only
        if replay_decision.trace is not None:
            record["trace"] = replay_decision.trace
        purchase_results.append({"record": record, "message": message})

    return purchase_results



# Replay the chosen scenarios, each as its own run, and return one result per purchase
def replay_scenarios(replay_tables, scenario_ids, decide_purchase, resolve_mode = "ask", uncertainty_policy = "ask", make_live_id = make_live_authorization_id):

    # Check the options before any purchase is decided
    assert resolve_mode in ALLOWED_RESOLVE_MODES, "Unknown resolve mode " + str(resolve_mode)
    assert uncertainty_policy in ALLOWED_UNCERTAINTY_POLICIES, "Unknown uncertainty policy " + str(uncertainty_policy)
    unknown_scenario_ids = sorted(set(scenario_ids) - set(list_scenario_ids(replay_tables)))
    assert not unknown_scenario_ids, "Unknown scenario " + ", ".join(unknown_scenario_ids)



    # Replay every scenario with the same validator and the same decision function
    event_schema_validator = build_event_schema_validator()
    results_per_scenario = [
        replay_one_scenario(
            scenario_attempts = replay_tables.joined_attempts.query("scenario_id == @scenario_id"),
            cart_lines_by_source_id = replay_tables.cart_lines_by_source_id,
            decide_purchase = decide_purchase,
            resolve_mode = resolve_mode,
            uncertainty_policy = uncertainty_policy,
            make_live_id = make_live_id,
            event_schema_validator = event_schema_validator,
        )
        for scenario_id in scenario_ids
    ]
    return [purchase_result for scenario_results in results_per_scenario for purchase_result in scenario_results]









#### Step 7: Build the result table and the summary ####

# Build the result table once, from the records of all purchases, without the full decision record that only the file carries
def build_result_table(purchase_results):
    return (
        pd.DataFrame([purchase_result["record"] for purchase_result in purchase_results])
        .drop(columns = ["trace"], errors = "ignore")
    )



# Sum amounts in exact decimals rounded to two places
def sum_amounts_chf(amounts):
    return sum((Decimal(str(amount)) for amount in amounts), Decimal("0")).quantize(Decimal("0.01"))



# Count the purchases per decision and total the amounts by final status
def summarize_replay(result_table):
    decision_counts = (
        result_table["decision"]
        .value_counts()
        .reindex(list(ALLOWED_DECISIONS), fill_value = 0)
        .rename_axis("decision")
        .reset_index(name = "purchases")
    )
    approved_purchases = result_table.query("final_status == 'approved'")
    other_purchases = result_table.query("final_status != 'approved'")
    return {
        "purchase_count": len(result_table),
        "decision_counts": decision_counts,
        "total_all_chf": sum_amounts_chf(result_table["billing_amount_chf"]),
        "total_approved_chf": sum_amounts_chf(approved_purchases["billing_amount_chf"]),
        "total_declined_or_pending_chf": sum_amounts_chf(other_purchases["billing_amount_chf"]),
    }



# Lay the three CHF totals out as a small table with two decimals
def build_totals_table(summary):
    return pd.DataFrame({
        "figure": ["All purchases", "Approved", "Declined or pending"],
        "amount_chf": [
            f"{summary['total_all_chf']:,.2f}",
            f"{summary['total_approved_chf']:,.2f}",
            f"{summary['total_declined_or_pending_chf']:,.2f}",
        ],
    })









#### Step 8: Compare the finished run with the reference decisions ####

# Choose the column of the reference decisions that fits the settings. An engine that asks the customer about a second order
# of the one requested thing is compared with the decisions in sequence, and any other engine with the decisions on the own facts of each purchase.
def choose_reference_column():
    if get_settings().goal_fulfilled_action == "step_up":
        return REFERENCE_COLUMN_IN_SEQUENCE
    return REFERENCE_COLUMN_ON_OWN_FACTS



# Name the column of the reference decisions that a finished comparison used
def read_reference_column(comparison_table):
    used_columns = [column_name for column_name in REFERENCE_COLUMNS if column_name in comparison_table.columns]
    assert len(used_columns) == 1, "A comparison must carry exactly one column of reference decisions"
    return used_columns[0]



# This comparison runs only after every decision of the run is final, and it only reads the finished result table.
# No decision function and no message builder may import, call or receive anything from it.
def compare_with_reference_decisions(result_table, reference_path = None):

    # Read the reference decisions as text, in the column that fits the settings
    if reference_path is None:
        reference_path = REPOSITORY_FOLDER / "data" / "processed" / "purchase_verdicts.csv"
    reference_column = choose_reference_column()
    reference_decisions = (
        pd.read_csv(reference_path, dtype = str, keep_default_na = False)
        .loc[:, ["authorization_id", reference_column]]
        .rename(columns = {"authorization_id": "source_authorization_id"})
    )



    # Join on the source identifier and mark every purchase as a match or a mismatch
    compared_table = (
        result_table
        .merge(reference_decisions, on = "source_authorization_id", how = "left", validate = "one_to_one", indicator = "reference_join")
        .assign(decision_match = lambda table: (table["decision"] == table[reference_column]).map({True: "match", False: "MISMATCH"}))
    )
    assert (compared_table["reference_join"] == "both").all(), "A purchase has no reference decision"



    # Put the reference decision and the match mark right next to the decision
    other_columns = [
        column_name
        for column_name in result_table.columns
        if column_name != "decision"
    ]
    decision_position = list(result_table.columns).index("decision")
    ordered_columns = (
        other_columns[:decision_position]
        + ["decision", reference_column, "decision_match"]
        + other_columns[decision_position:]
    )
    return compared_table.loc[:, ordered_columns]









#### Step 9: Write the report files and print the summary ####

# Format the amounts, the reason codes and the timing of a table as display text
def format_table_for_display(table):
    return (
        table
        .assign(billing_amount_chf = lambda frame: frame["billing_amount_chf"].map("{:.2f}".format))
        .assign(reason_codes = lambda frame: frame["reason_codes"].str.join(", "))
        .assign(elapsed_ms = lambda frame: frame["elapsed_ms"].map("{:.3f}".format))
    )



# Write a table as markdown, with any pipe character inside a cell replaced so it cannot break the table
def format_markdown_table(table):
    text_table = (
        table
        .astype(str)
        .apply(lambda column: column.str.replace("|", "/", regex = False))
    )
    header_line = "| " + " | ".join(text_table.columns) + " |"
    divider_line = "| " + " | ".join("---" for column_name in text_table.columns) + " |"
    row_lines = ["| " + " | ".join(row_values) + " |" for row_values in text_table.to_numpy().tolist()]
    return "\n".join([header_line, divider_line] + row_lines)



# Say who decided the run, which is a named baseline or the engine
def describe_decision_source(baseline_name):
    if baseline_name is None:
        return ENGINE_RUN_NAME
    return "baseline " + baseline_name



# Say how many guards of the engine have no logic yet
def describe_guards_not_built(not_built_count, guard_count):
    return "Guards not built yet - " + str(not_built_count) + " of " + str(guard_count)



# Build the markdown report with one table per scenario in replay order, followed by the summary
def build_markdown_report(run_label, decision_source, resolve_mode, display_table, summary, comparison_table, guards_not_built_line = None):

    # Build one section per scenario
    formatted_table = format_table_for_display(display_table)
    scenario_sections = [
        "## " + scenario_id + "\n\n" + format_markdown_table(scenario_table.drop(columns = ["scenario_id"]))
        for scenario_id, scenario_table in formatted_table.groupby("scenario_id", sort = True)
    ]



    # Build the summary section, with the comparison only when it was requested
    summary_parts = [
        "## Summary",
        "Purchases per decision",
        format_markdown_table(summary["decision_counts"]),
        "CHF totals",
        format_markdown_table(build_totals_table(summary)),
    ]
    if comparison_table is not None:
        match_count = int((comparison_table["decision_match"] == "match").sum())
        summary_parts = summary_parts + [
            "Comparison with the reference decisions in the column " + read_reference_column(comparison_table)
            + " - matches " + str(match_count) + " of " + str(len(comparison_table))
        ]
    if guards_not_built_line is not None:
        summary_parts = summary_parts + [guards_not_built_line]

    title = "# Replay result - " + run_label + " - " + decision_source + " - step_up answers " + resolve_mode
    return "\n\n".join([title] + scenario_sections + summary_parts) + "\n"



# Write the markdown report and the line-per-purchase file, and return both paths
def write_replay_outputs(purchase_results, markdown_report, output_folder, file_stem):
    output_folder.mkdir(parents = True, exist_ok = True)
    markdown_path = output_folder / (file_stem + ".md")
    lines_path = output_folder / (file_stem + ".jsonl")
    markdown_path.write_text(markdown_report, encoding = "utf-8")
    purchase_lines = [json.dumps(purchase_result, ensure_ascii = False) for purchase_result in purchase_results]
    lines_path.write_text("\n".join(purchase_lines) + "\n", encoding = "utf-8")
    return markdown_path, lines_path



# Print the summary of the run in labeled blocks
def print_replay_summary(run_label, decision_source, resolve_mode, summary, comparison_table, written_paths, guards_not_built_line = None):
    print("--- Replay run ---")
    print("Scenarios " + run_label + ", " + decision_source + ", step_up answers " + resolve_mode + ", purchases " + str(summary["purchase_count"]))
    print()

    if guards_not_built_line is not None:
        print("--- Engine ---")
        print(guards_not_built_line)
        print()

    print("--- Purchases per decision ---")
    print(summary["decision_counts"].to_string(index = False))
    print()

    print("--- CHF totals ---")
    print(build_totals_table(summary).to_string(index = False))
    print()

    if comparison_table is not None:
        match_count = int((comparison_table["decision_match"] == "match").sum())
        reference_column = read_reference_column(comparison_table)
        mismatches = (
            comparison_table
            .query("decision_match == 'MISMATCH'")
            .loc[:, ["scenario_id", "replay_order", "source_authorization_id", "decision", reference_column]]
        )
        print("--- Comparison with the reference decisions ---")
        print("reference column " + reference_column)
        print("matches " + str(match_count) + " of " + str(len(comparison_table)))
        print()
        print("--- Mismatches ---")
        print(mismatches.to_string(index = False) if len(mismatches) > 0 else "none")
        print()

    print("--- Files written ---")
    print("\n".join(str(written_path) for written_path in written_paths))
    print()









#### Step 10: Read the command line and run ####

# Read the command line options
def parse_command_line(command_line_arguments = None):
    parser = argparse.ArgumentParser(description = "Replay the public purchases offline and decide each one in order.")
    scenario_choice = parser.add_mutually_exclusive_group(required = True)
    scenario_choice.add_argument("--scenario", help = "Replay one scenario, for example SCEN0001")
    scenario_choice.add_argument("--all", action = "store_true", help = "Replay all scenarios")
    parser.add_argument("--baseline", default = None, choices = sorted(DECISION_FUNCTIONS_BY_BASELINE), help = "Decide with a baseline, where the engine decides when this option is left out")
    parser.add_argument("--resolve", default = "ask", choices = ALLOWED_RESOLVE_MODES, help = "How the simulated customer answers every step_up, where ask leaves it pending")
    parser.add_argument("--uncertainty-policy", default = "ask", choices = ALLOWED_UNCERTAINTY_POLICIES, help = "The uncertainty policy written into the mandate")
    parser.add_argument("--compare", action = "store_true", help = "Compare the finished run with the reference decisions")
    return parser.parse_args(command_line_arguments)



# Run one replay from the command line options and write the report files
def main(command_line_arguments = None, output_folder = OUTPUT_FOLDER):

    # Read the options and load the tables
    options = parse_command_line(command_line_arguments)
    replay_tables = load_replay_tables()
    all_scenario_ids = list_scenario_ids(replay_tables)
    if options.all:
        scenario_ids = all_scenario_ids
        run_label = "all"
    else:
        if options.scenario not in all_scenario_ids:
            raise SystemExit("Unknown scenario " + options.scenario + ". Known scenarios are " + ", ".join(all_scenario_ids) + ".")
        scenario_ids = [options.scenario]
        run_label = options.scenario



    # Replay the purchases with the chosen decision function
    purchase_results = replay_scenarios(
        replay_tables = replay_tables,
        scenario_ids = scenario_ids,
        decide_purchase = choose_decision_function(options.baseline),
        resolve_mode = options.resolve,
        uncertainty_policy = options.uncertainty_policy,
    )
    result_table = build_result_table(purchase_results)
    summary = summarize_replay(result_table)



    # Compare with the reference decisions only now, after every decision of the run is final
    comparison_table = None
    if options.compare:
        comparison_table = compare_with_reference_decisions(result_table)



    # Name who decided, and for an engine run say how many guards have no logic yet
    decision_source = describe_decision_source(options.baseline)
    run_name = ENGINE_RUN_NAME if options.baseline is None else options.baseline
    guards_not_built_line = None
    if options.baseline is None:
        guards_not_built_line = describe_guards_not_built(*count_guards_not_built())



    # Write the report files and print the summary
    display_table = result_table if comparison_table is None else comparison_table
    markdown_report = build_markdown_report(run_label, decision_source, options.resolve, display_table, summary, comparison_table, guards_not_built_line)
    file_stem = run_label + "_" + run_name + "_" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    written_paths = write_replay_outputs(purchase_results, markdown_report, output_folder, file_stem)
    print_replay_summary(run_label, decision_source, options.resolve, summary, comparison_table, written_paths, guards_not_built_line)
    return result_table



if __name__ == "__main__":
    main()
