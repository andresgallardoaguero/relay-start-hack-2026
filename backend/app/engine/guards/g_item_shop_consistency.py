# Script: g_item_shop_consistency.py
# Purpose: Look at whether the items of a cart fit the shop that sells them, by their category and by the way they are billed
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import describe_francs, describe_lines, quote_shop_text
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardSignal, GuardVerdict, ReasonCode









#### Step 1: Name what the guard knows ####

# List the item categories that are billed again and again, which only a shop that can bill on a recurring basis can sell
RECURRING_ITEM_CATEGORIES = ("subscriptions", "membership")



# Name the parts of the message that the evidence points to
CART_LINES_SOURCE = "authorization.items"
MERCHANT_CATEGORY_SOURCE = "authorization.merchant.merchant_category"
RECURRING_CAPABLE_SOURCE = "authorization.merchant.recurring_capable"



# Name the signal of a cart line whose category differs from the category of the shop.
# A shop may sell goods outside its own category, so this signal is recorded and never asks the customer on its own.
CATEGORY_DIFFERS_SIGNAL = GuardSignal(name = "item_category_differs_from_shop", strength = "normal")









#### Step 2: Build the evidence ####

# Describe one recurring item against a shop that cannot bill on a recurring basis
def build_recurring_line_evidence(line, recurring_capable):
    return EvidenceItem(
        fact = "recurring_capable",
        value = recurring_capable,
        comparator = "=",
        threshold = True,
        source = RECURRING_CAPABLE_SOURCE + " against " + CART_LINES_SOURCE + " line " + str(line.line_no) + " with item_category " + line.item_category,
    )



# Describe one cart line whose category differs from the category of the shop
def build_differing_line_evidence(line, merchant_category):
    return EvidenceItem(
        fact = "item_category",
        value = line.item_category,
        comparator = "=",
        threshold = merchant_category,
        source = CART_LINES_SOURCE + " line " + str(line.line_no) + " against " + MERCHANT_CATEGORY_SOURCE,
    )



# Describe a whole cart whose categories all equal the category of the shop
def build_cart_evidence(lines, merchant_category):
    return EvidenceItem(
        fact = "cart_item_categories",
        value = ", ".join(sorted({line.item_category for line in lines})),
        comparator = "=",
        threshold = merchant_category,
        source = CART_LINES_SOURCE + " against " + MERCHANT_CATEGORY_SOURCE,
    )









#### Step 3: Define the guard ####

# Check whether the items fit the shop. The guard reads the cart lines, the franc amount and the shop of the fact sheet and nothing of the policy.
# It decides on the item category, the merchant category and the recurring billing flag of the platform, and never on a name.
@dataclass(frozen = True)
class ItemShopConsistencyGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        facts = decision_input.facts
        lines = facts.lines
        merchant = facts.merchant



        # Ask the customer about a recurring item from a shop that cannot bill on a recurring basis, with one evidence item per such line
        recurring_lines = tuple(line for line in lines if line.item_category in RECURRING_ITEM_CATEGORIES)
        if recurring_lines and not merchant.recurring_capable:
            return build_guard_result(
                self,
                GuardVerdict.STEP_UP,
                reason_code = ReasonCode.ITEM_SHOP_MISMATCH,
                evidence = tuple(build_recurring_line_evidence(line, merchant.recurring_capable) for line in recurring_lines),
                customer_message = (
                    "Billed on a recurring basis - " + describe_lines(recurring_lines) + ". " + quote_shop_text(merchant.merchant_name)
                    + " cannot bill on a recurring basis. Approve the whole order of " + describe_francs(facts.billing_amount_chf) + "?"
                ),
            )



        # Record every line whose category differs from the category of the shop as a signal, which decides nothing
        differing_lines = tuple(line for line in lines if line.item_category != merchant.merchant_category)
        if differing_lines:
            return build_guard_result(
                self,
                GuardVerdict.PASS,
                evidence = tuple(build_differing_line_evidence(line, merchant.merchant_category) for line in differing_lines),
                signal = CATEGORY_DIFFERS_SIGNAL,
            )



        # Pass a cart whose lines all carry the category of the shop, without a signal
        return build_guard_result(self, GuardVerdict.PASS, evidence = (build_cart_evidence(lines, merchant.merchant_category),))
