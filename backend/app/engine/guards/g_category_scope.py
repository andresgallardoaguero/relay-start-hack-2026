# Script: g_category_scope.py
# Purpose: Compare the item category of every cart line with the kinds of goods the customer's instruction covers and rules out
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import clean_shop_text, describe_categories, describe_francs, describe_lines
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence ####

# Name the part of the message and the two fields of the policy that the evidence points to
CART_LINES_SOURCE = "authorization.items"
ALLOWED_FIELD_SOURCE = "policy.expectations.allowed_item_categories"
PROHIBITED_FIELD_SOURCE = "policy.expectations.prohibited_item_categories"



# State how many characters of an item name the customer gets to read
SHOWN_ITEM_NAME_LENGTH = 60









#### Step 2: Make an item name safe to show ####

# Clean an item name with the shared cleaning of shop text, cut to the length stated above
def clean_item_name(item_name):
    return clean_shop_text(item_name, SHOWN_ITEM_NAME_LENGTH)









#### Step 3: Judge one line and build the evidence ####

# Say how one cart line stands, judged on its category alone. A ruled-out category is prohibited.
# With covered categories stated, every other category is outside the scope, which includes a category that no catalogue knows.
def read_line_standing(line, allowed_item_categories, prohibited_item_categories):
    if line.item_category in prohibited_item_categories:
        return "prohibited"
    if len(allowed_item_categories) > 0 and line.item_category not in allowed_item_categories:
        return "outside"
    return "inside"



# Describe one cart line whose category is ruled out
def build_prohibited_line_evidence(line, prohibited_item_categories):
    return EvidenceItem(
        fact = "item_category",
        value = line.item_category,
        comparator = "not_in",
        threshold = ", ".join(prohibited_item_categories),
        source = CART_LINES_SOURCE + " line " + str(line.line_no) + " against " + PROHIBITED_FIELD_SOURCE,
    )



# Describe one cart line whose category is not among the covered ones
def build_outside_line_evidence(line, allowed_item_categories):
    return EvidenceItem(
        fact = "item_category",
        value = line.item_category,
        comparator = "in",
        threshold = ", ".join(allowed_item_categories),
        source = CART_LINES_SOURCE + " line " + str(line.line_no) + " against " + ALLOWED_FIELD_SOURCE,
    )



# Describe the categories of a whole cart that passed, against the covered categories when any are stated and against the ruled-out ones otherwise
def build_cart_evidence(lines, allowed_item_categories, prohibited_item_categories):
    allowed_is_stated = len(allowed_item_categories) > 0
    return EvidenceItem(
        fact = "cart_item_categories",
        value = ", ".join(sorted({line.item_category for line in lines})),
        comparator = "in" if allowed_is_stated else "not_in",
        threshold = ", ".join(allowed_item_categories if allowed_is_stated else prohibited_item_categories),
        source = CART_LINES_SOURCE + " against " + (ALLOWED_FIELD_SOURCE if allowed_is_stated else PROHIBITED_FIELD_SOURCE),
    )









#### Step 4: Define the guard ####

# Check the kind of goods in the cart. The guard reads the cart lines of the fact sheet and the two category lists of the policy.
# It decides on the category of a line alone. The name of an item is shown to the customer and never compared with anything.
@dataclass(frozen = True)
class CategoryScopeGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        facts = decision_input.facts
        lines = facts.lines
        allowed_item_categories = tuple(decision_input.policy.expectations.allowed_item_categories)
        prohibited_item_categories = tuple(decision_input.policy.expectations.prohibited_item_categories)



        # Skip when the instruction is open about the kind of goods, which is the customer's choice
        if len(allowed_item_categories) == 0 and len(prohibited_item_categories) == 0:
            scope_evidence = EvidenceItem(
                fact = "item_category_scope",
                value = "not_stated",
                comparator = None,
                threshold = None,
                source = ALLOWED_FIELD_SOURCE + " and " + PROHIBITED_FIELD_SOURCE,
            )
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (scope_evidence,))



        # Sort the lines into prohibited lines and lines outside the scope, with one evidence item per such line in cart order
        standings = tuple(read_line_standing(line, allowed_item_categories, prohibited_item_categories) for line in lines)
        prohibited_lines = tuple(line for line, standing in zip(lines, standings) if standing == "prohibited")
        outside_lines = tuple(line for line, standing in zip(lines, standings) if standing == "outside")
        line_evidence = tuple(
            build_prohibited_line_evidence(line, prohibited_item_categories)
            if standing == "prohibited"
            else build_outside_line_evidence(line, allowed_item_categories)
            for line, standing in zip(lines, standings)
            if standing != "inside"
        )



        # Decline a cart with a line the instruction rules out, whatever else the cart holds
        if prohibited_lines:
            prohibited_categories_in_cart = sorted({line.item_category for line in prohibited_lines})
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.PROHIBITED_ITEM,
                evidence = line_evidence,
                customer_message = "Declined. Your instruction rules out " + describe_categories(prohibited_categories_in_cart) + " - " + describe_lines(prohibited_lines) + ".",
            )



        # Pass a cart where every line is inside the scope, or where only ruled-out categories are stated and none is in the cart
        if not outside_lines:
            cart_evidence = build_cart_evidence(lines, allowed_item_categories, prohibited_item_categories)
            return build_guard_result(self, GuardVerdict.PASS, evidence = (cart_evidence,))



        # Decline a cart where no line is what the customer asked for
        covered_goods_sentence = "Your instruction covers " + describe_categories(allowed_item_categories) + "."
        if len(outside_lines) == len(lines):
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.OFF_SCOPE_ITEM,
                evidence = line_evidence,
                customer_message = "Declined. Nothing in this order is what you asked for - " + describe_lines(outside_lines) + ". " + covered_goods_sentence,
            )



        # Ask the customer about a cart that holds what was asked for together with other goods
        return build_guard_result(
            self,
            GuardVerdict.STEP_UP,
            reason_code = ReasonCode.OFF_SCOPE_ITEM,
            evidence = line_evidence,
            customer_message = (
                "Not covered by your instruction - " + describe_lines(outside_lines) + ". " + covered_goods_sentence
                + " Approve the whole order of " + describe_francs(facts.billing_amount_chf) + "?"
            ),
        )
