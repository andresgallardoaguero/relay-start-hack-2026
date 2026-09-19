# Script: pipeline.py
# Purpose: Decide one purchase by running every guard in order, timing each one and recording the whole decision
# Author: Andrés Gallardo
# Date: September 2026

import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

from app.config import get_settings
from app.engine.aggregate import aggregate
from app.engine.display import clean_shop_text
from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.registry import get_default_guard_pipeline
from app.llm.schemas import ModelExtraction
from app.models.decision import (
    DecisionTrace,
    EvidenceItem,
    GuardResult,
    GuardVerdict,
    ReasonCode,
    TraceAmounts,
    TraceFacts,
    TraceIdentifiers,
    TraceLanguageModel,
    TraceMerchant,
    TraceTimings,
)
from app.state.baselines import BaselinesUnavailableError, get_default_baselines
from app.state.familiarity import build_familiarity_facts
from app.state.session import build_session_facts









#### Step 1: Read the clock and measure time ####

# Read the real clock in UTC, which a test replaces to fix the time
def read_utc_clock():
    return datetime.now(timezone.utc)



# Measure the milliseconds since a starting point of the performance counter
def measure_elapsed_ms(started_at):
    return round((time.perf_counter() - started_at) * 1000, 3)



# Measure the milliseconds left between the decision and the deadline, which is negative when the deadline has passed
def measure_margin_ms(deadline_at, decided_at):
    return (deadline_at - decided_at) / timedelta(milliseconds = 1)









#### Step 2: Copy the identifiers and the facts into the record ####

# Copy the five identifiers of the purchase into the record.
# This is the only place of the engine that names them, and nothing in the engine ever branches on an identifier.
def collect_trace_identifiers(event):
    return TraceIdentifiers(
        authorization_id = event.authorization.authorization_id,
        source_authorization_id = event.authorization.source_authorization_id,
        request_id = event.request_id,
        mandate_id = event.authorization.mandate_id,
        scenario_id = event.authorization.scenario_id,
    )



# Accept the facts of a language model only as the one type the model module hands out, and treat anything else as no facts at all
def read_model_extraction(extracted_facts):
    return extracted_facts if isinstance(extracted_facts, ModelExtraction) else None



# Clean a text a language model wrote, which may repeat words of a shop, and keep a missing text missing
def clean_model_text(model_text):
    return None if model_text is None else clean_shop_text(model_text)



# Clean the two free texts of one line of model facts, which are the kind of product and the size
def clean_extracted_line(line_as_dictionary):
    return {
        **line_as_dictionary,
        "product_kind": clean_model_text(line_as_dictionary["product_kind"]),
        "size": clean_model_text(line_as_dictionary["size"]),
    }



# Copy the facts a language model read into the record as plain values, which is None without a model and for a model call that gave no facts.
# Every free text goes through the shared cleaning of shop text first, so no raw word of a shop reaches the record.
# The guards never read this copy. They read the facts of the model themselves.
def collect_extracted_item_facts(model_extraction):
    if model_extraction is None or model_extraction.facts is None:
        return None
    facts_as_dictionary = model_extraction.facts.model_dump(mode = "json")
    return {
        **facts_as_dictionary,
        "lines": [clean_extracted_line(line_as_dictionary) for line_as_dictionary in facts_as_dictionary["lines"]],
        "injection_quotes": [clean_shop_text(injection_quote) for injection_quote in facts_as_dictionary["injection_quotes"]],
    }



# Say how the language model took part in the decision, which is off without a model, live after a call that succeeded and degraded after any other call
def read_language_model_mode(model_extraction):
    if model_extraction is None:
        return "off"
    return "live" if model_extraction.call.status == "success" else "degraded"



# Copy the mode and the record of the model call into the record, where the list of calls stays empty without a model
def collect_trace_language_model(model_extraction):
    if model_extraction is None:
        return TraceLanguageModel()
    return TraceLanguageModel(
        mode = read_language_model_mode(model_extraction),
        calls = [model_extraction.call.model_dump(mode = "json")],
    )



# Look up how familiar the shop of the purchase is, with the name similarity of the settings.
# Counts that cannot be loaded give None, which every guard treats as a missing fact and never as permission.
def look_up_familiarity(event, baselines):
    try:
        if baselines is None:
            baselines = get_default_baselines()
    except BaselinesUnavailableError:
        return None
    return build_familiarity_facts(event, baselines, get_settings().lookalike_name_similarity)



# Look up how usual the device, the hour and the country of the purchase are for the card, from the same counts as the familiarity lookup.
# Counts that cannot be loaded give None, which every guard treats as a missing fact and never as permission.
def look_up_session(event, baselines):
    try:
        if baselines is None:
            baselines = get_default_baselines()
    except BaselinesUnavailableError:
        return None
    return build_session_facts(event, baselines)



# Write the familiarity counts as a plain dictionary and put the session counts next to them under the key "session".
# Both come from the same counts of the card history, so without the familiarity counts there is nothing to write.
def collect_familiarity_record(familiarity_facts, session_facts):
    if familiarity_facts is None:
        return None
    return {
        **asdict(familiarity_facts),
        "session": None if session_facts is None else asdict(session_facts),
    }



# Copy the cart into the record in line order, each line with its item, its name cleaned like every text a shop wrote, and its quantity.
# A stored record tells through this copy what an earlier purchase held. The guards never read it.
def collect_trace_cart_lines(event):
    return [
        {
            "item_id": cart_item.item_id,
            "item_name": clean_shop_text(cart_item.item_name),
            "quantity": cart_item.quantity,
        }
        for cart_item in event.authorization.items
    ]



# Copy the amounts and the shop of the purchase into the record, together with the counts, the resembled name and the score of the familiarity lookup,
# the session counts, the facts of the language model and the cart
def collect_trace_facts(event, familiarity_facts = None, model_extraction = None, session_facts = None):
    return TraceFacts(
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
        familiarity = collect_familiarity_record(familiarity_facts, session_facts),
        extracted_item_facts = collect_extracted_item_facts(model_extraction),
        cart_lines = collect_trace_cart_lines(event),
    )









#### Step 3: Run one guard safely ####

# Record a guard that failed as UNCERTAIN with one evidence item that says what went wrong
def build_guard_error_result(guard, evidence_fact, evidence_value):
    return GuardResult(
        guard_number = guard.guard_number,
        guard_id = guard.guard_id,
        family = guard.family,
        verdict = GuardVerdict.UNCERTAIN,
        reason_code = ReasonCode.GUARD_ERROR,
        evidence = [
            EvidenceItem(
                fact = evidence_fact,
                value = evidence_value,
                comparator = None,
                threshold = None,
                source = "engine",
            ),
        ],
    )



# Run one guard and time it, where any exception and any result under a foreign identity becomes a recorded failure
def run_one_guard(guard, decision_input, earlier_results):
    started_at = time.perf_counter()

    # Call the guard and catch whatever it raises, so one broken guard never stops the decision
    try:
        guard_result = guard.check(decision_input, earlier_results)
    except Exception as guard_exception:
        guard_result = build_guard_error_result(guard, "guard_exception_type", type(guard_exception).__name__)



    # Refuse an answer that is not a guard result or that carries the number or the id of another guard
    if not isinstance(guard_result, GuardResult):
        guard_result = build_guard_error_result(guard, "guard_returned_type", type(guard_result).__name__)
    elif guard_result.guard_number != guard.guard_number or guard_result.guard_id != guard.guard_id:
        guard_result = build_guard_error_result(guard, "guard_returned_identity", str(guard_result.guard_number) + " " + guard_result.guard_id)

    return guard_result.model_copy(update = {"elapsed_ms": measure_elapsed_ms(started_at)})









#### Step 4: Decide one purchase ####

# Decide one purchase and return the complete record, where every guard appears whatever happened.
# The clock is read for the received and decided moments, the margin and the timings only, and no guard sees it.
# extracted_facts may carry what a language model read from the sentences of the shop. It is optional, and the decision never waits for it.
# baselines holds the counts of the card history, and None means the counts that are loaded once per process.
def decide(
    event,
    policy,
    state,
    guards = None,
    read_clock = None,
    extracted_facts = None,
    baselines = None,
):

    # Fall back to the full pipeline, which is built once per process, and to the real clock
    if guards is None:
        guards = get_default_guard_pipeline()
    if read_clock is None:
        read_clock = read_utc_clock
    guard_ids = [guard.guard_id for guard in guards]
    assert len(set(guard_ids)) == len(guard_ids), "A guard id appears twice in the pipeline"



    # Note when the purchase arrived and start the timer of the whole decision
    received_at = read_clock()
    decision_started_at = time.perf_counter()



    # Build the fact sheet once and hand it to every guard, so all guards compare the same exact decimals.
    # A message whose facts cannot be built raises here, and the exception leaves this function unchanged.
    fact_sheet = build_fact_sheet(event)
    # Look the shop up in the card history once per purchase, so every guard reads the same counts and none of them sees an identifier
    familiarity_facts = look_up_familiarity(event, baselines)
    # Look the device, the hour and the country up in the same card history, once per purchase
    session_facts = look_up_session(event, baselines)
    model_extraction = read_model_extraction(extracted_facts)
    decision_input = DecisionInput(
        event = event,
        policy = policy,
        facts = fact_sheet,
        state = state,
        familiarity = familiarity_facts,
        session = session_facts,
        extracted_facts = model_extraction,
    )



    # Run the guards one after the other, because each guard receives the results of the guards before it.
    # Each guard receives a read-only copy, so it sees no later result and cannot change an earlier one.
    results_by_guard_id = {}
    for guard in guards:
        earlier_results = MappingProxyType(dict(results_by_guard_id))
        results_by_guard_id[guard.guard_id] = run_one_guard(guard, decision_input, earlier_results)
    guard_results = list(results_by_guard_id.values())



    # Combine the results into one decision under the customer's uncertainty policy
    aggregation_outcome = aggregate(guard_results, policy.uncertainty_policy)



    # Note when the decision was made and assemble the record
    decided_at = read_clock()
    return DecisionTrace(
        ids = collect_trace_identifiers(event),
        received_at = received_at,
        decided_at = decided_at,
        deadline_at = event.deadline_at,
        margin_ms = measure_margin_ms(event.deadline_at, decided_at),
        decision = aggregation_outcome.decision,
        reason_codes = aggregation_outcome.reason_codes,
        customer_message = aggregation_outcome.customer_message,
        notes = aggregation_outcome.notes,
        facts = collect_trace_facts(event, familiarity_facts, model_extraction, session_facts),
        guards = guard_results,
        aggregation = aggregation_outcome.aggregation_record,
        llm = collect_trace_language_model(model_extraction),
        timings = TraceTimings(total_ms = measure_elapsed_ms(decision_started_at)),
    )
