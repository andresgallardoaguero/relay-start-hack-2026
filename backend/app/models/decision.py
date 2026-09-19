# Script: decision.py
# Purpose: Define the record of one decision, from the verdict of every guard to the compact body sent to the authorization service
# Author: Andrés Gallardo
# Date: September 2026

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field









#### Step 1: Define the versions and the closed value lists ####

# State the version of the record layout and the version of the engine that writes it
TRACE_VERSION = "1"
ENGINE_VERSION = "relay-0.1.0"



# List the three answers to a purchase, spelled in lowercase as the authorization service expects them
class Decision(str, Enum):

    APPROVE = "approve"
    DECLINE = "decline"
    STEP_UP = "step_up"



# List the five verdicts a guard can give, where UNCERTAIN is settled later by the customer's uncertainty policy
class GuardVerdict(str, Enum):

    PASS = "PASS"
    STEP_UP = "STEP_UP"
    DECLINE = "DECLINE"
    UNCERTAIN = "UNCERTAIN"
    SKIP = "SKIP"



# List the five families of guards, where each value is the name the customer sees on screen
class GuardFamily(str, Enum):

    SPENDING_LIMITS = "Spending limits"
    ITEM_AND_TERMS = "Item and terms"
    SELLER = "Seller"
    SESSION = "Session"
    REPEATS_AND_MANIPULATION = "Repeats and manipulation"



# List every reason a guard or the engine can give, where each value equals its name
class ReasonCode(str, Enum):

    # Name the reasons of the spending limit guards
    MANDATE_INACTIVE = "MANDATE_INACTIVE"
    CARD_BLOCKED = "CARD_BLOCKED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    SMALL_OVERSHOOT = "SMALL_OVERSHOOT"
    OVER_PER_ORDER_LIMIT = "OVER_PER_ORDER_LIMIT"
    ORDER_LIMIT_UNCLEAR = "ORDER_LIMIT_UNCLEAR"
    OVER_PERIOD_LIMIT = "OVER_PERIOD_LIMIT"
    PERIOD_LIMIT_UNCLEAR = "PERIOD_LIMIT_UNCLEAR"
    SPLIT_ORDER_SUSPECTED = "SPLIT_ORDER_SUSPECTED"



    # Name the reasons about the items, the seller and the session
    OFF_SCOPE_ITEM = "OFF_SCOPE_ITEM"
    PROHIBITED_ITEM = "PROHIBITED_ITEM"
    UNREQUESTED_ADDON = "UNREQUESTED_ADDON"
    MERCHANT_TYPE_MISMATCH = "MERCHANT_TYPE_MISMATCH"
    LOOKALIKE_MERCHANT = "LOOKALIKE_MERCHANT"
    UNFAMILIAR_MERCHANT = "UNFAMILIAR_MERCHANT"
    SESSION_ANOMALY = "SESSION_ANOMALY"



    # Name the reasons about repeated orders and manipulation
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    RE_QUOTE_COMPLIANT = "RE_QUOTE_COMPLIANT"
    GOAL_ALREADY_FULFILLED = "GOAL_ALREADY_FULFILLED"
    PROMPT_INJECTION = "PROMPT_INJECTION"



    # Name the reasons about the requested item and the order terms
    ITEM_MISMATCH = "ITEM_MISMATCH"
    RETURN_TERMS_UNMET = "RETURN_TERMS_UNMET"
    RETURN_TERMS_UNKNOWN = "RETURN_TERMS_UNKNOWN"
    CANCELLATION_TERMS_UNMET = "CANCELLATION_TERMS_UNMET"
    CANCELLATION_TERMS_UNKNOWN = "CANCELLATION_TERMS_UNKNOWN"
    FULFILLMENT_MISMATCH = "FULFILLMENT_MISMATCH"
    DELIVERY_TOO_LATE = "DELIVERY_TOO_LATE"



    # Name the reasons about the card settings, the shop and the price history
    CARD_SETTING_BLOCKS = "CARD_SETTING_BLOCKS"
    OVER_ISSUER_LIMIT = "OVER_ISSUER_LIMIT"
    ITEM_SHOP_MISMATCH = "ITEM_SHOP_MISMATCH"
    PRICE_INCREASE = "PRICE_INCREASE"



    # Name the reasons the engine gives about itself
    INVALID_EVENT = "INVALID_EVENT"
    ENGINE_TIMEOUT_FALLBACK = "ENGINE_TIMEOUT_FALLBACK"
    GUARD_ERROR = "GUARD_ERROR"
    LEDGER_UNAVAILABLE = "LEDGER_UNAVAILABLE"
    GUARD_NOT_BUILT = "GUARD_NOT_BUILT"









#### Step 2: Define the frozen base model ####

# Refuse unknown fields and refuse any change after creation on every model of the record
class FrozenDecisionModel(BaseModel):

    model_config = ConfigDict(
        frozen = True,
        extra = "forbid",
    )









#### Step 3: Define the result of one guard ####

# Allow an evidence value to be a text, a number, a boolean or nothing
EvidenceValue = Union[str, int, float, bool, None]



# Describe one fact a guard looked at, what it was compared with and where it came from
class EvidenceItem(FrozenDecisionModel):

    fact: str
    value: EvidenceValue
    comparator: Optional[str]
    threshold: EvidenceValue
    source: str



# Describe one behavioral signal a guard noticed, which a later guard may combine with others
class GuardSignal(FrozenDecisionModel):

    name: str
    strength: Literal["normal", "strong"]



# Describe the answer of one guard, where the guard fills in its own number, id and family
class GuardResult(FrozenDecisionModel):

    # Keep the identity of the guard and its verdict
    guard_number: int
    guard_id: str
    family: GuardFamily
    verdict: GuardVerdict
    reason_code: Optional[ReasonCode] = None



    # Keep what the guard saw and what it wants to tell the customer
    evidence: list[EvidenceItem] = Field(default_factory = list)
    signal: Optional[GuardSignal] = None
    note: Optional[str] = None
    customer_message: Optional[str] = None



    # Keep the running time of the guard, which the pipeline measures and fills in
    elapsed_ms: float = 0.0









#### Step 4: Define the parts of the decision record ####

# Describe how the verdicts became one decision, where raised_by names the guards at the final severity
class AggregationRecord(FrozenDecisionModel):

    initial: Decision
    final: Decision
    raised_by: list[str]
    uncertainty_policy_applied: bool



# Keep the identifiers of the purchase, where scenario_id is kept for logging only and never decides anything
class TraceIdentifiers(FrozenDecisionModel):

    authorization_id: str
    source_authorization_id: str
    request_id: str
    mandate_id: str
    scenario_id: str



# Keep the amounts of the purchase as the message carries them
class TraceAmounts(FrozenDecisionModel):

    amount: float
    currency: str
    billing_amount_chf: float



# Keep the identity of the shop as the message carries it
class TraceMerchant(FrozenDecisionModel):

    merchant_id: str
    merchant_name: str
    merchant_category: str
    merchant_country: str



# Keep the facts the decision rests on. familiarity holds the counts of the card history for the shop, the resembled shop name and the score,
# and is empty when the counts could not be loaded. The extracted item facts stay empty for now.
# cart_lines holds the cart in line order, each line as a plain dictionary with item_id, the cleaned item_name and quantity.
# It is kept so that a stored record can later tell what an earlier purchase held. No guard reads it.
class TraceFacts(FrozenDecisionModel):

    amounts: TraceAmounts
    merchant: TraceMerchant
    familiarity: Optional[dict[str, Any]] = None
    extracted_item_facts: Optional[dict[str, Any]] = None
    cart_lines: list[dict[str, Any]] = Field(default_factory = list)



# Keep the use of the language model. The mode is off when no model took part in the decision, live when its call succeeded,
# and degraded when the call failed in any way, so the decision rests on the fixed patterns alone.
class TraceLanguageModel(FrozenDecisionModel):

    mode: Literal["off", "live", "degraded"] = "off"
    calls: list[dict[str, Any]] = Field(default_factory = list)



# Keep the running time of the whole decision
class TraceTimings(FrozenDecisionModel):

    total_ms: float









#### Step 5: Define the decision record ####

# Describe one complete decision, where every guard of the pipeline appears in guards
class DecisionTrace(FrozenDecisionModel):

    # Keep the versions and the identifiers
    trace_version: str = TRACE_VERSION
    engine_version: str = ENGINE_VERSION
    ids: TraceIdentifiers



    # Keep the three moments, where margin_ms is deadline_at minus decided_at in milliseconds and may be negative
    received_at: AwareDatetime
    decided_at: AwareDatetime
    deadline_at: AwareDatetime
    margin_ms: float



    # Keep the answer and what the customer reads about it
    decision: Decision
    reason_codes: list[ReasonCode]
    customer_message: str
    notes: list[str]



    # Keep the facts, the result of every guard and how the results were combined
    facts: TraceFacts
    guards: list[GuardResult]
    aggregation: AggregationRecord



    # Keep the language model use and the timings, where the customer's answer to a step_up stays empty for now
    llm: TraceLanguageModel
    timings: TraceTimings
    resolution: Optional[dict[str, Any]] = None









#### Step 6: Build the compact body for the authorization service ####

# Build the body of the decision request as a plain dictionary, with the evidence of the guards that raised the decision
def build_decision_request_body(trace):

    # Collect the evidence of the guards named in raised_by, which is empty when nothing was raised
    raising_guard_ids = set(trace.aggregation.raised_by)
    evidence_of_raising_guards = [
        evidence_item.model_dump(mode = "json")
        for guard_result in trace.guards
        if guard_result.guard_id in raising_guard_ids
        for evidence_item in guard_result.evidence
    ]



    # Assemble the six fields with plain values only
    return {
        "authorization_id": trace.ids.authorization_id,
        "decision": trace.decision.value,
        "reason_codes": [reason_code.value for reason_code in trace.reason_codes],
        "customer_message": trace.customer_message,
        "evidence": evidence_of_raising_guards,
        "engine_version": trace.engine_version,
    }
