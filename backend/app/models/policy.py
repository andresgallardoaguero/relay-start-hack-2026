# Script: policy.py
# Purpose: Define the policy the engine enforces for one customer instruction, as data without any logic
# Author: Andrés Gallardo
# Date: September 2026

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.events import MandateRule









#### Step 1: Define the frozen base model ####

# Refuse unknown fields and refuse any change after creation on every model of the policy
class FrozenPolicyModel(BaseModel):

    model_config = ConfigDict(
        frozen = True,
        extra = "forbid",
    )









#### Step 2: Define what the instruction expects ####

# Describe the one thing the customer asked for, by the words that name it and the attributes it must have
class RequestedItem(FrozenPolicyModel):

    kind_keywords: tuple[str, ...]
    attributes: dict[str, str] = Field(default_factory = dict)
    max_quantity: Optional[int] = None
    goal_quantity: Optional[int] = None



# Describe everything the instruction expects, where every default means that the instruction does not state it
class Expectations(FrozenPolicyModel):

    # Keep the spending limits in CHF as exact decimals
    per_order_limit_chf: Optional[Decimal] = None
    period_limit_chf: Optional[Decimal] = None
    period_days: Optional[int] = None
    overshoot_tolerance_share: Decimal = Decimal("0.10")



    # Keep how the limit per order was read, where an exclusive limit means the instruction asked to stay under it.
    # The reading is not_stated when the instruction names no limit, read when one was found and unclear when something limit-like could not be read.
    per_order_limit_inclusive: bool = True
    per_order_limit_reading: Literal["not_stated", "read", "unclear"] = "not_stated"



    # Keep how the budget over a period was read, with the same meanings as for the limit per order.
    # The budget itself is period_limit_chf over period_days, where no number of days means the whole mandate.
    period_limit_inclusive: bool = True
    period_limit_reading: Literal["not_stated", "read", "unclear"] = "not_stated"



    # Keep how many minutes apart two orders at the same shop may be and still count as one order split in two
    split_order_window_minutes: int = 120



    # Keep how many hours after an approved order the same cart at the same shop counts as a repeated order,
    # and how far the two amounts may be apart, as a share of the earlier amount
    duplicate_window_hours: int = 48
    duplicate_amount_share: Decimal = Decimal("0.10")



    # Keep what happens when the instruction asks for one thing and that thing was already bought, which is nothing, a note on an approval or a question.
    # The question is the default, because one thing means one and the customer stays responsible for a second one.
    # No value declines, because a second purchase that is clean on its own facts breaks no instruction.
    goal_fulfilled_action: Literal["off", "note", "step_up"] = "step_up"



    # Keep the allowed and the prohibited categories, the kind of shop and the requested item.
    # A strict kind of shop means the instruction insists on it, as in "only from", so another kind of shop is refused and not asked about.
    allowed_item_categories: tuple[str, ...] = ()
    prohibited_item_categories: tuple[str, ...] = ()
    required_merchant_categories: tuple[str, ...] = ()
    merchant_category_is_strict: bool = False
    requested_item: Optional[RequestedItem] = None
    no_addons: bool = False



    # Keep the order terms
    min_return_days: Optional[int] = None
    require_cancellable: Optional[bool] = None
    required_fulfillment_method: Optional[str] = None
    latest_delivery_date: Optional[date] = None
    max_price_increase_share: Optional[Decimal] = None



    # Keep how familiar the seller must be and how closely the session is watched.
    # The bar "before" needs one approved purchase at the shop, and "regularly" needs familiarity_regular_min_purchases of them.
    merchant_familiarity: Literal["required", "preferred", "any"] = "any"
    familiarity_bar: Optional[Literal["regularly", "before"]] = None
    familiarity_regular_min_purchases: int = 3
    session_sensitivity: Literal["normal", "high"] = "normal"



    # Keep what happens when a text of the shop is aimed at the shopping agent, which is a question to the customer or a refusal.
    # No value approves, because such a text can never loosen a decision.
    injection_action: Literal["step_up", "decline"] = "step_up"



    # Keep the numbers of the session signals. That many distinct signals ask the customer and that many decline, where a count exactly at a number reaches it.
    # That many purchase attempts in the ten minutes before a purchase make a quick series, and a card history needs that many purchases
    # before an hour without any purchase counts as unusual.
    session_ask_signal_count: int = 2
    session_decline_signal_count: int = 3
    velocity_min_recent_attempts: int = 2
    hour_min_history_purchases: int = 20









#### Step 3: Define the policy ####

# Describe the complete policy, where open_questions lists what could not be read from the instruction
class InternalPolicy(FrozenPolicyModel):

    instruction: str
    hard_rules: tuple[MandateRule, ...]
    uncertainty_policy: Literal["ask", "decline", "approve"]
    expectations: Expectations
    open_questions: tuple[str, ...]
