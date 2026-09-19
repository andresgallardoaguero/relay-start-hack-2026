# Script: g_item_match.py
# Purpose: Compare the cart with the one thing the customer asked for, by the words of the item name and by the attributes the thing must have
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import clean_shop_text, quote_shop_text
from app.engine.guards.base import build_guard_result
from app.engine.shop_text import find_model_line, pick_stricter_result, settle_line_facts
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence ####

# Name the part of the message and the fields of the policy that the evidence points to
CART_LINES_SOURCE = "authorization.items"
REQUESTED_FIELD_SOURCE = "policy.expectations.requested_item"
KEYWORDS_FIELD_SOURCE = "policy.expectations.requested_item.kind_keywords"
ATTRIBUTES_FIELD_SOURCE = "policy.expectations.requested_item.attributes"









#### Step 2: Find the lines that are the requested thing ####

# Report whether one cart line is the requested thing, which it is when the words of its name hold every keyword.
# Without keywords every line is, because the instruction names no particular thing.
def line_matches_keywords(line, kind_keywords):
    return all(kind_keyword in line.name_words for kind_keyword in kind_keywords)



# Find the cart lines that are the requested thing, where every line counts when nothing was requested
def find_requested_lines(lines, requested_item):
    if requested_item is None:
        return tuple(lines)
    return tuple(line for line in lines if line_matches_keywords(line, requested_item.kind_keywords))









#### Step 3: Compare the attributes of one line ####

# Hold how one requested attribute stands on one cart line, where found_value is None when the line does not say
@dataclass(frozen = True)
class AttributeStanding:
    line: object
    attribute_name: str
    requested_value: str
    found_value: object
    standing: str



# Read the value one cart line has for an attribute, from the settled facts of the line. Only the size can be read, and any other attribute is missing.
def read_line_attribute(settled_facts, attribute_name):
    return settled_facts.size if attribute_name == "size" else None



# Compare one requested attribute with one cart line, in upper case. An equal value is equal, another value is different and no value is missing.
def read_attribute_standing(line, settled_facts, attribute_name, requested_value):
    requested_value_in_upper_case = requested_value.strip().upper()
    found_value = read_line_attribute(settled_facts, attribute_name)
    if found_value is None:
        standing = "missing"
    elif found_value.upper() == requested_value_in_upper_case:
        standing = "equal"
    else:
        standing = "different"
    return AttributeStanding(
        line = line,
        attribute_name = attribute_name,
        requested_value = requested_value_in_upper_case,
        found_value = found_value,
        standing = standing,
    )









#### Step 4: Build the evidence ####

# Describe one cart line against the keywords, with the cleaned name of the line
def build_keywords_evidence(line, kind_keywords):
    return EvidenceItem(
        fact = "item_name",
        value = clean_shop_text(line.item_name),
        comparator = "contains_all",
        threshold = " ".join(kind_keywords),
        source = CART_LINES_SOURCE + " line " + str(line.line_no) + " against " + KEYWORDS_FIELD_SOURCE,
    )



# Describe one attribute of one cart line against the requested value, where the value is a typed fact and never the text of the shop
def build_attribute_evidence(attribute_standing):
    return EvidenceItem(
        fact = attribute_standing.attribute_name,
        value = attribute_standing.found_value,
        comparator = "=",
        threshold = attribute_standing.requested_value,
        source = CART_LINES_SOURCE + " line " + str(attribute_standing.line.line_no) + " facts of the shop text against " + ATTRIBUTES_FIELD_SOURCE,
    )









#### Step 5: Write the messages ####

# Tell the customer that nothing in the cart is the requested thing
def build_wrong_item_message(lines, kind_keywords):
    quoted_names = ", ".join(quote_shop_text(line.item_name) for line in lines)
    return "Declined. This order is for " + quoted_names + ", and you asked for " + " ".join(kind_keywords) + "."



# Tell the customer about one attribute with another value than the requested one
def describe_different_attribute(attribute_standing):
    return (
        quote_shop_text(attribute_standing.line.item_name) + " in this order is " + attribute_standing.attribute_name + " " + attribute_standing.found_value
        + ", and you asked for " + attribute_standing.attribute_name + " " + attribute_standing.requested_value + "."
    )



# Tell the customer about one attribute that cannot be read
def describe_missing_attribute(attribute_standing):
    return (
        "The " + attribute_standing.attribute_name + " of " + quote_shop_text(attribute_standing.line.item_name) + " cannot be read from what the shop wrote"
        + ", and you asked for " + attribute_standing.attribute_name + " " + attribute_standing.requested_value + "."
    )









#### Step 6: Define the guard ####

# Check that the cart holds the thing the customer asked for. The guard reads the name words and the text facts of the cart lines,
# the optional facts of a language model, and the requested item of the policy.
# It compares the item itself and not its category, because a catalogue holds near-twins such as road-running and trail-running shoes.
# A line that is not the requested thing next to one that is counts as an extra, which another guard judges.
# The cart is judged twice, on the shop text alone and with the facts of the model, and the stricter result is the answer.
@dataclass(frozen = True)
class ItemMatchGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        return pick_stricter_result(
            self.judge_cart(decision_input, None),
            self.judge_cart(decision_input, decision_input.extracted_facts),
        )

    # Judge the cart with the facts of the given model, or on the shop text alone when no model is given
    def judge_cart(self, decision_input, model_extraction):
        lines = decision_input.facts.lines
        requested_item = decision_input.policy.expectations.requested_item
        kind_keywords = () if requested_item is None else tuple(requested_item.kind_keywords)
        requested_attributes = {} if requested_item is None else dict(requested_item.attributes)



        # Skip when the instruction names no particular thing, which is the customer's choice
        if len(kind_keywords) == 0 and len(requested_attributes) == 0:
            request_evidence = EvidenceItem(
                fact = "requested_item",
                value = "not_stated",
                comparator = None,
                threshold = None,
                source = REQUESTED_FIELD_SOURCE,
            )
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (request_evidence,))



        # Decline a cart without any line that is the requested thing, with one evidence item per line in cart order
        requested_lines = find_requested_lines(lines, requested_item)
        if len(requested_lines) == 0:
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.ITEM_MISMATCH,
                evidence = tuple(build_keywords_evidence(line, kind_keywords) for line in lines),
                customer_message = build_wrong_item_message(lines, kind_keywords),
            )



        # Compare every requested attribute with every line that is the requested thing.
        # The fact of the shop text comes first, and the fact of the model counts only where the text says nothing.
        attribute_standings = tuple(
            read_attribute_standing(
                line,
                settle_line_facts(line.text_facts, find_model_line(model_extraction, line.line_no)),
                attribute_name,
                requested_value,
            )
            for line in requested_lines
            for attribute_name, requested_value in sorted(requested_attributes.items())
        )
        different_standings = tuple(attribute_standing for attribute_standing in attribute_standings if attribute_standing.standing == "different")
        missing_standings = tuple(attribute_standing for attribute_standing in attribute_standings if attribute_standing.standing == "missing")
        keywords_evidence = tuple(build_keywords_evidence(line, kind_keywords) for line in requested_lines) if kind_keywords else ()
        evidence = keywords_evidence + tuple(build_attribute_evidence(attribute_standing) for attribute_standing in attribute_standings)



        # Decline when a requested attribute has another value, as with size 42 against size 43
        if different_standings:
            return build_guard_result(
                self,
                GuardVerdict.DECLINE,
                reason_code = ReasonCode.ITEM_MISMATCH,
                evidence = evidence,
                customer_message = "Declined. " + " ".join(describe_different_attribute(attribute_standing) for attribute_standing in different_standings),
            )



        # Leave the decision to the customer's uncertainty policy when a requested attribute cannot be read, because a missing fact is never a pass
        if missing_standings:
            return build_guard_result(
                self,
                GuardVerdict.UNCERTAIN,
                reason_code = ReasonCode.ITEM_MISMATCH,
                evidence = evidence,
                customer_message = " ".join(describe_missing_attribute(attribute_standing) for attribute_standing in missing_standings) + " Approve anyway?",
            )



        # Pass a cart whose requested lines carry every requested attribute with the requested value
        return build_guard_result(self, GuardVerdict.PASS, evidence = evidence)
