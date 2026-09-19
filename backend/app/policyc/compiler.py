# Script: compiler.py
# Purpose: Build the policy of the engine from the mandate that arrives inside a purchase message
# Author: Andrés Gallardo
# Date: September 2026

from decimal import Decimal

from app.config import get_settings
from app.models.policy import Expectations, InternalPolicy, RequestedItem
from app.policyc.familiarity import read_familiarity
from app.policyc.item_scope import read_item_scope
from app.policyc.merchant_type import read_merchant_type
from app.policyc.order_limit import read_order_limit
from app.policyc.order_terms import read_order_terms
from app.policyc.period_budget import read_period_budget
from app.policyc.request_shape import read_request_shape
from app.policyc.requested_item import read_requested_item
from app.policyc.session import read_session_sensitivity
from app.state.item_catalogue import get_default_item_catalogue









#### Step 1: Build the policy from a mandate ####

# Describe the requested item of an instruction that asks for one single thing, which is one unit to buy and one unit as the goal.
# The words that name the thing and the attributes it must have come from the reading of the requested item.
# Both may be empty, because the kind of goods is kept in the allowed categories.
def build_requested_item(asks_for_one_thing, requested_item_reading):
    if not asks_for_one_thing:
        return None
    return RequestedItem(
        kind_keywords = requested_item_reading.kind_keywords,
        attributes = dict(requested_item_reading.attributes),
        max_quantity = 1,
        goal_quantity = 1,
    )



# Copy the instruction, the rules and the uncertainty policy, and read the limit per order, the budget over a period, the kinds of goods,
# the shape of the request, the requested item, the order terms, the kind of shop, how familiar the shop has to be
# and how closely the session is watched from the instruction and the rules. The numbers of the session signals, the hours and the amount share of a repeated order
# and the answer to a goal that was already bought come from the settings.
# Whatever a reader could not settle becomes an open question for the customer, and every other expectation stays at its default.
def build_policy_from_mandate(mandate, overshoot_tolerance_share = None, item_catalogue = None):

    # Take the tolerance from the settings when none is handed in, where a handed-in zero keeps every limit strictly hard
    if overshoot_tolerance_share is None:
        overshoot_tolerance_share = get_settings().overshoot_tolerance_share
    overshoot_tolerance_share = Decimal(str(overshoot_tolerance_share))



    # Take the catalogue that is loaded once per process when none is handed in
    if item_catalogue is None:
        item_catalogue = get_default_item_catalogue()



    # Read everything without any model, so the policy is ready at once.
    # The open questions about the limit per order come first, then those about the budget over a period,
    # the goods, the shape of the request, the requested item, the order terms, the kind of shop, the familiarity of the shop and last the watch over the session.
    order_limit_reading = read_order_limit(mandate.instruction, mandate.hard_rules)
    period_budget_reading = read_period_budget(mandate.instruction, mandate.hard_rules)
    item_scope_reading = read_item_scope(mandate.instruction, mandate.hard_rules, item_catalogue)
    request_shape_reading = read_request_shape(mandate.instruction)
    requested_item_reading = read_requested_item(mandate.instruction, item_catalogue, request_shape_reading.asks_for_one_thing)
    order_terms_reading = read_order_terms(mandate.instruction)
    merchant_type_reading = read_merchant_type(mandate.instruction, mandate.hard_rules)
    familiarity_reading = read_familiarity(mandate.instruction)
    session_reading = read_session_sensitivity(mandate.instruction)
    return InternalPolicy(
        instruction = mandate.instruction,
        hard_rules = tuple(mandate.hard_rules),
        uncertainty_policy = mandate.uncertainty_policy,
        expectations = Expectations(
            per_order_limit_chf = order_limit_reading.limit_chf,
            per_order_limit_inclusive = order_limit_reading.inclusive,
            per_order_limit_reading = order_limit_reading.reading,
            period_limit_chf = period_budget_reading.limit_chf,
            period_days = period_budget_reading.period_days,
            period_limit_inclusive = period_budget_reading.inclusive,
            period_limit_reading = period_budget_reading.reading,
            split_order_window_minutes = get_settings().split_order_window_minutes,
            duplicate_window_hours = get_settings().duplicate_window_hours,
            duplicate_amount_share = get_settings().duplicate_amount_share,
            goal_fulfilled_action = get_settings().goal_fulfilled_action,
            overshoot_tolerance_share = overshoot_tolerance_share,
            allowed_item_categories = item_scope_reading.allowed_item_categories,
            prohibited_item_categories = item_scope_reading.prohibited_item_categories,
            required_merchant_categories = merchant_type_reading.required_merchant_categories,
            merchant_category_is_strict = merchant_type_reading.is_strict,
            requested_item = build_requested_item(request_shape_reading.asks_for_one_thing, requested_item_reading),
            no_addons = request_shape_reading.no_addons,
            min_return_days = order_terms_reading.min_return_days,
            merchant_familiarity = familiarity_reading.merchant_familiarity,
            familiarity_bar = familiarity_reading.familiarity_bar,
            familiarity_regular_min_purchases = get_settings().familiarity_regular_min_purchases,
            injection_action = get_settings().injection_action,
            session_sensitivity = session_reading.session_sensitivity,
            session_ask_signal_count = get_settings().session_ask_signal_count,
            session_decline_signal_count = get_settings().session_decline_signal_count,
            velocity_min_recent_attempts = get_settings().velocity_min_recent_attempts,
            hour_min_history_purchases = get_settings().hour_min_history_purchases,
        ),
        open_questions = (
            order_limit_reading.open_questions
            + period_budget_reading.open_questions
            + item_scope_reading.open_questions
            + request_shape_reading.open_questions
            + requested_item_reading.open_questions
            + order_terms_reading.open_questions
            + merchant_type_reading.open_questions
            + familiarity_reading.open_questions
            + session_reading.open_questions
        ),
    )
