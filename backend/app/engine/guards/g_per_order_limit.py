# Script: g_per_order_limit.py
# Purpose: Compare the amount of one purchase in Swiss francs with the customer's limit per order
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from decimal import Decimal

from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode









#### Step 1: Name the sources of the evidence ####

# Name the field of the message and the fields of the policy that the evidence points to
AMOUNT_AGAINST_LIMIT_SOURCE = "authorization.billing_amount_chf against policy.expectations.per_order_limit_chf"
TOLERANCE_SOURCE = "policy.expectations.overshoot_tolerance_share"
READING_SOURCE = "policy.expectations.per_order_limit_reading"



# Round the overshoot share in the evidence to four places
FOUR_PLACES = Decimal("0.0001")









#### Step 2: Write the amounts for the customer ####

# Write an amount in Swiss francs with two decimals
def describe_francs(amount_chf):
    return f"CHF {amount_chf:.2f}"



# Write the amount of the purchase, and name the original amount as well when the shop charges in another currency.
# The original amount is quoted for the customer only. It is never compared with anything.
def describe_purchase_amount(facts):
    if facts.currency == "CHF":
        return describe_francs(facts.billing_amount_chf)
    return f"{facts.currency} {facts.amount:.2f}, which is {describe_francs(facts.billing_amount_chf)}"



# Write the purchase amount as the subject of a sentence, where the inserted franc amount is closed with a comma
def describe_purchase_amount_as_subject(facts):
    if facts.currency == "CHF":
        return describe_purchase_amount(facts)
    return describe_purchase_amount(facts) + ","



# Say how the purchase stands against the limit, either exactly at a limit the customer wanted to stay under or above it by an amount
def describe_overshoot(facts, limit_chf, overshoot_chf):
    subject = describe_purchase_amount_as_subject(facts)
    if overshoot_chf == 0:
        return subject + " is exactly your limit of " + describe_francs(limit_chf) + ", and your instruction asked to stay under it."
    return subject + " is " + describe_francs(overshoot_chf) + " above your limit of " + describe_francs(limit_chf) + "."









#### Step 3: Define the guard ####

# Check the limit per order, where the engine never approves above the limit on its own.
# The guard reads the amount in Swiss francs and the policy, and the decision never rests on a division.
@dataclass(frozen = True)
class PerOrderLimitGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        facts = decision_input.facts
        expectations = decision_input.policy.expectations
        limit_chf = expectations.per_order_limit_chf
        limit_reading = expectations.per_order_limit_reading
        reading_evidence = EvidenceItem(
            fact = "per_order_limit_reading",
            value = limit_reading,
            comparator = None,
            threshold = None,
            source = READING_SOURCE,
        )



        # Ask about the purchase when the instruction seems to set a limit that could not be read
        if limit_reading == "unclear" or (limit_reading == "read" and limit_chf is None):
            return build_guard_result(
                self,
                GuardVerdict.UNCERTAIN,
                reason_code = ReasonCode.ORDER_LIMIT_UNCLEAR,
                evidence = (reading_evidence,),
                customer_message = "Your instruction does not state a clear limit per order. This purchase costs " + describe_purchase_amount(facts) + ". Approve it?",
            )



        # Skip when the instruction states no limit per order. A limit that is present is always enforced, whatever the reading says.
        if limit_chf is None:
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (reading_evidence,))



        # Compare the amount with the limit, where an exclusive limit is already broken by an amount equal to it
        amount_chf = facts.billing_amount_chf
        limit_is_inclusive = expectations.per_order_limit_inclusive
        purchase_is_over = amount_chf > limit_chf or (amount_chf == limit_chf and not limit_is_inclusive)
        amount_evidence = EvidenceItem(
            fact = "billing_amount_chf",
            value = float(amount_chf),
            comparator = "<=" if limit_is_inclusive else "<",
            threshold = float(limit_chf),
            source = AMOUNT_AGAINST_LIMIT_SOURCE,
        )
        if not purchase_is_over:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (amount_evidence,))



        # Measure the overshoot and compare it with the tolerated part of the limit by multiplication.
        # The share is divided out for the evidence only, and a limit of zero has no share.
        tolerance_share = expectations.overshoot_tolerance_share
        overshoot_chf = amount_chf - limit_chf
        overshoot_is_tolerated = tolerance_share > 0 and overshoot_chf <= limit_chf * tolerance_share
        overshoot_share = None if limit_chf == 0 else float((overshoot_chf / limit_chf).quantize(FOUR_PLACES))
        overshoot_evidence = EvidenceItem(
            fact = "overshoot_share",
            value = overshoot_share,
            comparator = "<=",
            threshold = float(tolerance_share),
            source = TOLERANCE_SOURCE,
        )



        # Ask the customer about a small overshoot and decline every other one, which is every overshoot when the share is zero
        overshoot_sentence = describe_overshoot(facts, limit_chf, overshoot_chf)
        if overshoot_is_tolerated:
            return build_guard_result(
                self,
                GuardVerdict.STEP_UP,
                reason_code = ReasonCode.SMALL_OVERSHOOT,
                evidence = (amount_evidence, overshoot_evidence),
                customer_message = overshoot_sentence + " Approve anyway?",
            )
        return build_guard_result(
            self,
            GuardVerdict.DECLINE,
            reason_code = ReasonCode.OVER_PER_ORDER_LIMIT,
            evidence = (amount_evidence, overshoot_evidence),
            customer_message = "Declined. " + overshoot_sentence,
        )
