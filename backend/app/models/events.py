# Script: events.py
# Purpose: Turn one raw purchase message into a strictly validated typed object
# Author: Andrés Gallardo
# Date: September 2026

import json
from datetime import date
from typing import Annotated, Literal, Optional, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError









#### Step 1: Define the reusable field types ####

# Require text with at least one character
NonEmptyText = Annotated[str, Field(min_length = 1)]



# Require money amounts as numbers, either above zero or from zero upward
AmountAboveZero = Annotated[float, Field(gt = 0)]
AmountFromZero = Annotated[float, Field(ge = 0)]



# Require whole numbers, either from one upward or from zero upward
WholeNumberFromOne = Annotated[int, Field(ge = 1)]
WholeNumberFromZero = Annotated[int, Field(ge = 0)]



# Spell the closed value lists exactly as the message schema does
CurrencyCode = Literal["CHF", "EUR", "GBP", "USD"]
OrderTermAnswer = Literal["true", "false", "unknown", "not_applicable"]



# Require the fixed formats of the scenario, the merchant category code and the country code
ScenarioIdentifier = Annotated[str, Field(pattern = r"^SCEN[0-9]{4}$")]
MerchantCategoryCode = Annotated[str, Field(pattern = r"^[0-9]{4}$")]
CountryCode = Annotated[str, Field(pattern = r"^[A-Z]{2}$")]



# Allow a rule value to be a whole number, a number, a text or a list of texts, and nothing else.
# The list of texts arrives as a tuple, so a rule value cannot be changed either.
RuleValue = Union[int, float, str, tuple[str, ...]]









#### Step 2: Define the strict base model ####

# Refuse type conversion, unknown fields and the non-finite numbers NaN and Infinity on every model.
# Refuse any change after creation as well, so nothing that reads a message can alter it.
class StrictEventModel(BaseModel):

    model_config = ConfigDict(
        strict = True,
        extra = "forbid",
        allow_inf_nan = False,
        frozen = True,
    )









#### Step 3: Define the parts of the purchase message ####

# Describe the shop that receives the payment
class Merchant(StrictEventModel):

    merchant_id: NonEmptyText
    merchant_name: NonEmptyText
    merchant_category: NonEmptyText
    merchant_mcc: MerchantCategoryCode
    merchant_country: CountryCode
    merchant_city: NonEmptyText
    availability: Literal["online", "store", "store_and_online", "atm"]
    recurring_capable: Literal["true", "false"]



# Describe one line of the cart
class CartItem(StrictEventModel):

    line_no: WholeNumberFromOne
    item_id: NonEmptyText
    item_name: NonEmptyText
    item_category: NonEmptyText
    quantity: WholeNumberFromOne
    unit_price: AmountAboveZero
    currency: CurrencyCode
    item_details: str



# Describe the proposed purchase with its shop, cart and session facts
class Authorization(StrictEventModel):

    # Keep the identifiers as plain text
    authorization_id: NonEmptyText
    source_authorization_id: NonEmptyText
    scenario_id: ScenarioIdentifier
    replay_order: WholeNumberFromOne
    mandate_id: NonEmptyText
    profile_id: NonEmptyText
    card_id: NonEmptyText
    initiator_type: Literal["agent"]



    # Keep the shop, the purchase time and the amounts as the message carries them
    merchant: Merchant
    timestamp: AwareDatetime
    amount: AmountAboveZero
    currency: CurrencyCode
    billing_amount_chf: AmountAboveZero
    items_subtotal: AmountAboveZero
    delivery_fee: AmountFromZero



    # Keep the session and card facts, where the device identifier may be empty
    channel: Literal["ecommerce", "in_store", "mobile_wallet", "recurring", "atm"]
    customer_device_id: str
    authority_status: Literal["active", "revoked", "expired"]
    card_status_at_attempt: Literal["active", "blocked"]
    spend_in_period_before_chf: Optional[AmountFromZero]
    recent_attempt_count_10m: WholeNumberFromZero



    # Keep the order terms, where a null stays None and a missing field fails because there is no default
    fulfillment_method: NonEmptyText
    delivery_by: Optional[date]
    order_returnable: OrderTermAnswer
    order_cancellable: OrderTermAnswer
    related_authorization_id: Optional[str]
    related_authorization_status: Optional[Literal["pending", "approved", "declined", "cancelled"]]



    # Keep the description and the cart, which holds at least one line and is a tuple so it cannot grow or shrink
    purchase_description: NonEmptyText
    items: Annotated[tuple[CartItem, ...], Field(min_length = 1)]



# Describe one machine-readable rule of the customer's instruction
class MandateRule(StrictEventModel):

    field: NonEmptyText
    operator: Literal["<", "<=", "=", "!=", ">", ">=", "in", "not_in"]
    value: RuleValue



    # Default the three optional fields to None, the only fields that may be missing
    currency: Optional[CurrencyCode] = None
    scope: Optional[Literal["purchase", "period"]] = None
    period_days: Optional[WholeNumberFromOne] = None



# Describe the customer's confirmed instruction and permissions
class Mandate(StrictEventModel):

    mandate_id: NonEmptyText
    status: Literal["active", "superseded", "revoked", "expired"]
    customer_id: NonEmptyText
    card_id: NonEmptyText
    instruction: NonEmptyText
    hard_rules: tuple[MandateRule, ...]
    uncertainty_policy: Literal["ask", "decline", "approve"]
    profile_id: NonEmptyText



# Describe one earlier purchase of the same run
class RecentAuthorization(StrictEventModel):

    authorization_id: NonEmptyText
    timestamp: AwareDatetime
    merchant_id: NonEmptyText
    billing_amount_chf: AmountFromZero
    status: Literal["approved", "declined", "pending", "cancelled"]



# Describe the spend and the recent purchases of the run, where the spend may be null but never missing
class EventContext(StrictEventModel):

    approved_spend_in_period_chf: Optional[AmountFromZero]
    recent_authorizations: tuple[RecentAuthorization, ...]



# Describe when the message was received and how its context was built
class EventRuntime(StrictEventModel):

    received_at: AwareDatetime
    history_window_minutes: WholeNumberFromOne
    context_basis: Literal["run_decisions_and_scenario_timestamps"]



# Describe the complete purchase message
class AuthorizationEvent(StrictEventModel):

    type: Literal["authorization.request"]
    request_id: NonEmptyText
    deadline_at: AwareDatetime
    authorization: Authorization
    mandate: Mandate
    context: EventContext
    runtime: EventRuntime









#### Step 4: Define the error for an invalid message ####

# Carry every problem as one plain sentence that names the field path and what is wrong
class InvalidEventError(ValueError):

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("Invalid purchase message - " + " | ".join(self.problems))



# Turn one validation finding into a plain sentence
def describe_one_problem(error_details):
    location_parts = [str(location_part) for location_part in error_details["loc"]]
    field_path = ".".join(location_parts)
    if field_path == "":
        field_path = "whole message"
    return field_path + " - " + error_details["msg"]









#### Step 5: Read a raw purchase message ####

# Accept text, bytes or a dictionary, and return the typed event or raise InvalidEventError
def read_purchase_message(raw_message):

    # Serialize a dictionary to JSON first, so every input takes the same validation path
    if isinstance(raw_message, dict):
        try:
            message_as_json = json.dumps(raw_message)
        except (TypeError, ValueError, RecursionError) as serialization_error:
            problem = "whole message - cannot be written as JSON, " + str(serialization_error)
            raise InvalidEventError([problem]) from serialization_error
    elif isinstance(raw_message, (str, bytes)):
        message_as_json = raw_message
    else:
        problem = "whole message - expected text, bytes or a dictionary, received " + type(raw_message).__name__
        raise InvalidEventError([problem])



    # Validate the JSON and translate every finding, malformed JSON included, into the typed error
    try:
        event = AuthorizationEvent.model_validate_json(message_as_json)
    except ValidationError as validation_error:
        problems = [describe_one_problem(error_details) for error_details in validation_error.errors()]
        raise InvalidEventError(problems) from validation_error

    return event
