# Script: g_addon.py
# Purpose: Find extras in the cart, which are goods beside what the customer asked for and more units than the customer asked for
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import describe_francs, describe_lines
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence ####

# Name the part of the message and the fields of the policy that the evidence points to
CART_LINES_SOURCE = "authorization.items"
ALLOWED_FIELD_SOURCE = "policy.expectations.allowed_item_categories"
MAX_QUANTITY_FIELD_SOURCE = "policy.expectations.requested_item.max_quantity"



# Write the small counts as words, so the customer reads "You asked for one"
COUNT_WORDS = {1: "one", 2: "two", 3: "three"}









#### Step 2: Build the evidence ####

# Describe one extra cart line, whose category is not among the covered ones
def build_extra_line_evidence(line, allowed_item_categories):
    return EvidenceItem(
        fact = "item_category",
        value = line.item_category,
        comparator = "in",
        threshold = ", ".join(allowed_item_categories),
        source = CART_LINES_SOURCE + " line " + str(line.line_no) + " against " + ALLOWED_FIELD_SOURCE,
    )



# Describe the units of the requested goods in the cart against the number the customer asked for
def build_units_evidence(requested_units, max_quantity):
    return EvidenceItem(
        fact = "requested_units",
        value = requested_units,
        comparator = "<=",
        threshold = max_quantity,
        source = MAX_QUANTITY_FIELD_SOURCE,
    )



# Describe a cart without extras, by its units when a number was asked for and by its count of extra lines otherwise
def build_pass_evidence(requested_units, max_quantity):
    if max_quantity is not None:
        return build_units_evidence(requested_units, max_quantity)
    return EvidenceItem(
        fact = "extra_lines",
        value = 0,
        comparator = "=",
        threshold = 0,
        source = CART_LINES_SOURCE + " against " + ALLOWED_FIELD_SOURCE,
    )









#### Step 3: Write the messages ####

# Write a count as a word where one exists, and as digits otherwise
def describe_count(count):
    return COUNT_WORDS.get(count, str(count))



# Tell the customer how many units were asked for and how many the order holds
def describe_surplus_units(requested_units, max_quantity):
    return "You asked for " + describe_count(max_quantity) + ", and this order holds " + str(requested_units) + "."



# Ask the customer about the extras, where each part appears only when the cart has that kind of extra
def build_question(extra_lines, units_are_over, requested_units, max_quantity, billing_amount_chf):
    extra_lines_sentences = ("Not asked for - " + describe_lines(extra_lines) + ".",) if extra_lines else ()
    surplus_sentences = (describe_surplus_units(requested_units, max_quantity),) if units_are_over else ()
    closing_sentences = ("Approve the whole order of " + describe_francs(billing_amount_chf) + "?",)
    return " ".join(extra_lines_sentences + surplus_sentences + closing_sentences)



# Tell the customer that the order was declined because the instruction forbids extras
def build_decline_message(extra_lines, units_are_over, requested_units, max_quantity):
    named_lines = " - " + describe_lines(extra_lines) if extra_lines else ""
    opening_sentences = ("Declined. Your instruction asked not to add anything" + named_lines + ".",)
    surplus_sentences = (describe_surplus_units(requested_units, max_quantity),) if units_are_over else ()
    return " ".join(opening_sentences + surplus_sentences)









#### Step 4: Define the guard ####

# Check the cart for extras. The guard reads the cart lines and the franc amount of the fact sheet,
# and from the policy the covered categories, the requested number of units and whether extras are forbidden.
# It decides on categories and quantities alone. The name of an item is shown to the customer and never compared with anything.
@dataclass(frozen = True)
class AddonGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        facts = decision_input.facts
        lines = facts.lines
        expectations = decision_input.policy.expectations
        allowed_item_categories = tuple(expectations.allowed_item_categories)
        max_quantity = None if expectations.requested_item is None else expectations.requested_item.max_quantity



        # Skip when the instruction says neither which goods it covers nor how many units it asks for, so nothing can be an extra
        if len(allowed_item_categories) == 0 and max_quantity is None:
            shape_evidence = EvidenceItem(
                fact = "requested_goods_and_units",
                value = "not_stated",
                comparator = None,
                threshold = None,
                source = ALLOWED_FIELD_SOURCE + " and " + MAX_QUANTITY_FIELD_SOURCE,
            )
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (shape_evidence,))



        # Sort the lines into those inside the covered goods and those outside, where every line is inside when no goods are stated
        scope_is_stated = len(allowed_item_categories) > 0
        inside_lines = tuple(line for line in lines if not scope_is_stated or line.item_category in allowed_item_categories)
        outside_lines = tuple(line for line in lines if scope_is_stated and line.item_category not in allowed_item_categories)



        # Pass a cart that holds nothing the customer asked for. It is a wrong purchase and not an extra, and the category scope declines it.
        if len(inside_lines) == 0:
            wrong_purchase_evidence = EvidenceItem(
                fact = "lines_inside_scope",
                value = 0,
                comparator = None,
                threshold = None,
                source = CART_LINES_SOURCE + " against " + ALLOWED_FIELD_SOURCE,
            )
            return build_guard_result(self, GuardVerdict.PASS, evidence = (wrong_purchase_evidence,))



        # Count the units of the requested goods, and find the two kinds of extras.
        # Every line outside the covered goods is an extra, and so are the units beyond the number the customer asked for.
        requested_units = sum(line.quantity for line in inside_lines)
        units_are_over = max_quantity is not None and requested_units > max_quantity
        extra_lines = outside_lines
        if not extra_lines and not units_are_over:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_pass_evidence(requested_units, max_quantity),))



        # Write one evidence item per extra line in cart order, and one for the surplus units
        line_evidence = tuple(build_extra_line_evidence(line, allowed_item_categories) for line in extra_lines)
        units_evidence = (build_units_evidence(requested_units, max_quantity),) if units_are_over else ()
        evidence = line_evidence + units_evidence



        # Decline when the instruction forbids extras
        if expectations.no_addons:
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.UNREQUESTED_ADDON,
                evidence = evidence,
                customer_message = build_decline_message(extra_lines, units_are_over, requested_units, max_quantity),
            )



        # Ask the customer otherwise, because an instruction that does not forbid extras leaves them to the customer
        return build_guard_result(
            self,
            GuardVerdict.STEP_UP,
            reason_code = ReasonCode.UNREQUESTED_ADDON,
            evidence = evidence,
            customer_message = build_question(extra_lines, units_are_over, requested_units, max_quantity, facts.billing_amount_chf),
        )
