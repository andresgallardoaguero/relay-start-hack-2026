# Script: registry.py
# Purpose: List the 23 guards with their number, id and family, and build them in the order the pipeline runs them
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from functools import lru_cache

from app.engine.guards.base import PlaceholderGuard
from app.engine.guards.g_addon import AddonGuard
from app.engine.guards.g_category_scope import CategoryScopeGuard
from app.engine.guards.g_device import DeviceGuard
from app.engine.guards.g_duplicate_order import DuplicateOrderGuard
from app.engine.guards.g_geo import GeoGuard
from app.engine.guards.g_goal_fulfilled import GoalFulfilledGuard
from app.engine.guards.g_injection import InjectionGuard
from app.engine.guards.g_item_match import ItemMatchGuard
from app.engine.guards.g_item_shop_consistency import ItemShopConsistencyGuard
from app.engine.guards.g_lookalike_merchant import LookalikeMerchantGuard
from app.engine.guards.g_merchant_familiarity import MerchantFamiliarityGuard
from app.engine.guards.g_merchant_type import MerchantTypeGuard
from app.engine.guards.g_order_terms import OrderTermsGuard
from app.engine.guards.g_per_order_limit import PerOrderLimitGuard
from app.engine.guards.g_period_budget import PeriodBudgetGuard
from app.engine.guards.g_session_integrity import SessionIntegrityGuard
from app.engine.guards.g_split_order import SplitOrderGuard
from app.engine.guards.g_velocity import VelocityGuard
from app.models.decision import GuardFamily









#### Step 1: List the 23 guards ####

# Describe one row of the guard table
@dataclass(frozen = True)
class GuardTableRow:
    guard_number: int
    guard_id: str
    family: GuardFamily



# State how many guards the pipeline has
EXPECTED_GUARD_COUNT = 23



# List every guard once, grouped by the family the customer sees on screen
GUARD_TABLE = (
    GuardTableRow(1, "authority", GuardFamily.SPENDING_LIMITS),
    GuardTableRow(2, "arithmetic_integrity", GuardFamily.SPENDING_LIMITS),
    GuardTableRow(3, "per_order_limit", GuardFamily.SPENDING_LIMITS),
    GuardTableRow(4, "period_budget", GuardFamily.SPENDING_LIMITS),
    GuardTableRow(5, "split_order", GuardFamily.SPENDING_LIMITS),
    GuardTableRow(20, "issuer_settings", GuardFamily.SPENDING_LIMITS),
    GuardTableRow(23, "price_against_last_time", GuardFamily.SPENDING_LIMITS),
    GuardTableRow(6, "category_scope", GuardFamily.ITEM_AND_TERMS),
    GuardTableRow(7, "addon", GuardFamily.ITEM_AND_TERMS),
    GuardTableRow(18, "item_match", GuardFamily.ITEM_AND_TERMS),
    GuardTableRow(19, "order_terms", GuardFamily.ITEM_AND_TERMS),
    GuardTableRow(21, "item_shop_consistency", GuardFamily.ITEM_AND_TERMS),
    GuardTableRow(8, "merchant_type", GuardFamily.SELLER),
    GuardTableRow(9, "lookalike_merchant", GuardFamily.SELLER),
    GuardTableRow(10, "merchant_familiarity", GuardFamily.SELLER),
    GuardTableRow(11, "device", GuardFamily.SESSION),
    GuardTableRow(12, "velocity", GuardFamily.SESSION),
    GuardTableRow(13, "geo", GuardFamily.SESSION),
    GuardTableRow(14, "session_integrity", GuardFamily.SESSION),
    GuardTableRow(22, "usual_purchase_pattern", GuardFamily.SESSION),
    GuardTableRow(15, "duplicate_order", GuardFamily.REPEATS_AND_MANIPULATION),
    GuardTableRow(16, "goal_fulfilled", GuardFamily.REPEATS_AND_MANIPULATION),
    GuardTableRow(17, "injection", GuardFamily.REPEATS_AND_MANIPULATION),
)



# State the order in which the guards run.
# Guard 22 runs before guard 14, because guard 14 combines the signals of guards 10 to 13 and 22.
# Guard 16 runs last, because the customer reads the message of the first guard at the final severity.
# Its question about a second order of the one requested thing then shows only when no other guard asks,
# and never hides a finding that matters more, such as a return policy that is not stated.
# The order changes no decision, because severity can only rise.
EXECUTION_ORDER = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 22, 14, 15, 17, 18, 19, 20, 21, 23, 16)



# Map the id of every guard that is built to its class, where a guard missing here is still a placeholder
BUILT_GUARD_CLASSES_BY_ID = {
    "per_order_limit": PerOrderLimitGuard,
    "period_budget": PeriodBudgetGuard,
    "split_order": SplitOrderGuard,
    "category_scope": CategoryScopeGuard,
    "addon": AddonGuard,
    "merchant_type": MerchantTypeGuard,
    "item_match": ItemMatchGuard,
    "order_terms": OrderTermsGuard,
    "lookalike_merchant": LookalikeMerchantGuard,
    "merchant_familiarity": MerchantFamiliarityGuard,
    "device": DeviceGuard,
    "velocity": VelocityGuard,
    "geo": GeoGuard,
    "session_integrity": SessionIntegrityGuard,
    "duplicate_order": DuplicateOrderGuard,
    "goal_fulfilled": GoalFulfilledGuard,
    "item_shop_consistency": ItemShopConsistencyGuard,
    "injection": InjectionGuard,
}









#### Step 2: Build the pipeline ####

# Build the 23 guards in execution order as a tuple, the real guard where one exists and a placeholder otherwise
def build_guard_pipeline():

    # Check the table, which must hold 23 guards with the numbers 1 to 23 and no id twice
    guard_numbers = [table_row.guard_number for table_row in GUARD_TABLE]
    guard_ids = [table_row.guard_id for table_row in GUARD_TABLE]
    assert len(GUARD_TABLE) == EXPECTED_GUARD_COUNT, "Expected 23 guards, found " + str(len(GUARD_TABLE))
    assert sorted(guard_numbers) == list(range(1, EXPECTED_GUARD_COUNT + 1)), "The guard numbers must be 1 to 23, each exactly once"
    assert len(set(guard_ids)) == EXPECTED_GUARD_COUNT, "A guard id appears twice"
    assert sorted(EXECUTION_ORDER) == sorted(guard_numbers), "The execution order must name every guard exactly once"



    # Check that every built guard belongs to a row of the table
    unknown_built_guard_ids = sorted(set(BUILT_GUARD_CLASSES_BY_ID) - set(guard_ids))
    assert unknown_built_guard_ids == [], "A built guard has no table row - " + ", ".join(unknown_built_guard_ids)



    # Build one guard per table row and put the guards in execution order
    table_rows_by_number = {table_row.guard_number: table_row for table_row in GUARD_TABLE}
    return tuple(
        build_one_guard(table_rows_by_number[guard_number])
        for guard_number in EXECUTION_ORDER
    )



# Build the guard of one table row, with the real class where one exists, so both kinds take their identity from the same row
def build_one_guard(table_row):
    guard_class = BUILT_GUARD_CLASSES_BY_ID.get(table_row.guard_id, PlaceholderGuard)
    return guard_class(
        guard_number = table_row.guard_number,
        guard_id = table_row.guard_id,
        family = table_row.family,
    )



# Build the pipeline on the first call and hand back the same tuple for the rest of the process
@lru_cache(maxsize = None)
def get_default_guard_pipeline():
    return build_guard_pipeline()



# List the ids of the guards that are still placeholders, in execution order
def list_placeholder_guard_ids(guards):
    return [guard.guard_id for guard in guards if isinstance(guard, PlaceholderGuard)]
