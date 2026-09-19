# Script: g_merchant_familiarity.py
# Purpose: Ask the customer about a shop that is less familiar than the instruction asks for, and say how widely the other customers use it
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass

from app.engine.display import quote_shop_text
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardSignal, GuardVerdict, ReasonCode
from app.state.ledger import LedgerSnapshot









#### Step 1: Name the sources of the evidence and the signal ####

# Name where the evidence comes from, which is the card history of the issuer, the policy and the memory of the run
HISTORY_SOURCE = "card history of the issuer"
CARD_COUNT_SOURCE = "approved purchases at the shop on this card against the bar of policy.expectations.familiarity_bar"
CUSTOMER_COUNT_SOURCE = "approved purchases at the shop on all cards of the holder of this card against the bar of policy.expectations.familiarity_bar"
ISSUER_CUSTOMER_COUNT_SOURCE = "customers of the issuer with an approved purchase at the shop"
MEMORY_SOURCE = "approved purchases of the run at the same shop"



# Name the signal of a shop that neither the card nor its holder has ever bought from.
# Many honest purchases are made at a new shop, so the signal is weak alone and a later guard may combine it with others.
UNFAMILIAR_MERCHANT_SIGNAL = GuardSignal(name = "unfamiliar_merchant", strength = "normal")



# State the purchases that the bar "before" needs, where the bar "regularly" takes its number from the policy
PURCHASES_FOR_USED_BEFORE = 1









#### Step 2: Write the sentences for the customer ####

# Write a number of purchases in words, as in "1 time" and "2 times"
def describe_times(purchase_count):
    if purchase_count == 1:
        return "1 time"
    return str(purchase_count) + " times"



# Write how widely the shop is used, in customers. The holder of the card is left out of the number when the holder bought there too.
def describe_how_widely_used(familiarity):
    if familiarity.issuer_customer_count == 0:
        return "No customer has ever bought from this seller."
    holder_has_bought_there = familiarity.customer_purchase_count > 0
    other_customer_count = familiarity.issuer_customer_count - 1 if holder_has_bought_there else familiarity.issuer_customer_count
    if other_customer_count <= 0:
        return "No other customer has bought there."
    if other_customer_count == 1:
        return "1 other customer has bought there."
    return str(other_customer_count) + " other customers have bought there."



# Write the question for a shop whose use on this card is below the bar while the use on all cards of the holder reaches it.
# The two honest readings of the instruction disagree here, so the customer decides.
def build_disagreement_question(quoted_shop_name, familiarity):
    purchases_with_other_cards = familiarity.customer_purchase_count - familiarity.card_purchase_count
    if familiarity.card_purchase_count == 0:
        opening = "You have not used this card at " + quoted_shop_name
    else:
        opening = "You have used this card at " + quoted_shop_name + " only " + describe_times(familiarity.card_purchase_count)
    return opening + ", but you bought there " + describe_times(purchases_with_other_cards) + " with another card. Approve this seller?"



# Write the question for a shop that is below the bar on every card of the holder
def build_unfamiliar_question(quoted_shop_name, familiarity, required_purchase_count):
    if familiarity.customer_purchase_count == 0:
        opening = quoted_shop_name + " is a shop you have not bought from."
    else:
        opening = (
            "You have bought at " + quoted_shop_name + " " + describe_times(familiarity.customer_purchase_count)
            + ", and your instruction asks for a shop you have used at least " + describe_times(required_purchase_count) + "."
        )
    return opening + " " + describe_how_widely_used(familiarity) + " Approve this seller?"









#### Step 3: Build the evidence ####

# Describe that no history was available, so nothing is known about the shop
def build_missing_history_evidence():
    return EvidenceItem(fact = "card_history_is_available", value = False, comparator = None, threshold = None, source = HISTORY_SOURCE)



# Describe the three counts, where the two counts of the customer stand against the bar when the instruction states one
def build_count_evidence(familiarity, required_purchase_count):
    comparator = None if required_purchase_count is None else ">="
    return (
        EvidenceItem(fact = "card_purchase_count", value = familiarity.card_purchase_count, comparator = comparator, threshold = required_purchase_count, source = CARD_COUNT_SOURCE),
        EvidenceItem(fact = "customer_purchase_count", value = familiarity.customer_purchase_count, comparator = comparator, threshold = required_purchase_count, source = CUSTOMER_COUNT_SOURCE),
        EvidenceItem(fact = "issuer_customer_count", value = familiarity.issuer_customer_count, comparator = None, threshold = None, source = ISSUER_CUSTOMER_COUNT_SOURCE),
    )



# Describe whether the customer approved a purchase at the same shop earlier in the run
def build_taught_trust_evidence(shop_was_approved_in_run):
    return EvidenceItem(fact = "shop_approved_earlier_in_run", value = shop_was_approved_in_run, comparator = None, threshold = None, source = MEMORY_SOURCE)









#### Step 4: Read the bar and the memory of the run ####

# Read how many approved purchases the instruction asks for, which is None when it states no bar
def read_required_purchase_count(expectations):
    if expectations.familiarity_bar == "regularly":
        return expectations.familiarity_regular_min_purchases
    if expectations.familiarity_bar == "before":
        return PURCHASES_FOR_USED_BEFORE
    return None



# Report whether a purchase at the same shop was approved earlier in the run. A pending purchase teaches nothing, because the customer has not answered yet,
# and a memory that is missing or incomplete teaches nothing either, because a missing fact is never permission.
def read_taught_trust(snapshot):
    if not isinstance(snapshot, LedgerSnapshot) or not snapshot.is_complete:
        return False
    return any(entry.same_shop and entry.status == "approved" for entry in snapshot.entries)









#### Step 5: Define the guard ####

# Check how familiar the shop is against what the instruction asks for. The guard reads the familiarity facts, the policy and the frozen memory of the run,
# and it sees no identifier. It never declines, because a new shop is no harm in itself, so the strictest answer is a question to the customer.
# Once the customer has approved a purchase at a shop, the shop passes for the rest of the run. Only that approval widens trust, and no text of a shop can.
@dataclass(frozen = True)
class MerchantFamiliarityGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        familiarity = decision_input.familiarity
        expectations = decision_input.policy.expectations
        familiarity_is_required = expectations.merchant_familiarity == "required"
        history_is_available = familiarity is not None and familiarity.card_is_known
        quoted_shop_name = quote_shop_text(decision_input.facts.merchant.merchant_name)



        # Answer without a history. An instruction that asks for a familiar shop cannot be checked, which is uncertain and never a pass.
        # An instruction that does not ask for one passes, and no signal is given, because an unknown history is not an unfamiliar shop.
        if not history_is_available and familiarity_is_required:
            return build_guard_result(
                self,
                GuardVerdict.UNCERTAIN,
                reason_code = ReasonCode.UNFAMILIAR_MERCHANT,
                evidence = (build_missing_history_evidence(),),
                customer_message = (
                    "Your earlier purchases could not be read, so it cannot be checked whether you have bought from "
                    + quoted_shop_name + " before. Approve this seller?"
                ),
            )
        if not history_is_available:
            return build_guard_result(self, GuardVerdict.PASS, evidence = (build_missing_history_evidence(),))



        # Pass under an instruction that does not make a familiar shop a condition, and record a shop that is entirely new to the customer as a signal
        required_purchase_count = read_required_purchase_count(expectations)
        count_evidence = build_count_evidence(familiarity, required_purchase_count)
        shop_is_new_to_customer = familiarity.card_purchase_count == 0 and familiarity.customer_purchase_count == 0
        signal = UNFAMILIAR_MERCHANT_SIGNAL if shop_is_new_to_customer else None
        if not familiarity_is_required:
            return build_guard_result(self, GuardVerdict.PASS, evidence = count_evidence, signal = signal)



        # Use the bar of one earlier purchase when a familiar shop is a condition and no bar was read, which is the mildest reading of the condition
        if required_purchase_count is None:
            required_purchase_count = PURCHASES_FOR_USED_BEFORE
            count_evidence = build_count_evidence(familiarity, required_purchase_count)



        # Pass when the purchases on this card reach the bar, where a count exactly at the bar reaches it
        if familiarity.card_purchase_count >= required_purchase_count:
            return build_guard_result(self, GuardVerdict.PASS, evidence = count_evidence)



        # Pass when the customer approved a purchase at the same shop earlier in the run, and say so in the evidence
        shop_was_approved_in_run = read_taught_trust(decision_input.state)
        evidence_with_memory = count_evidence + (build_taught_trust_evidence(shop_was_approved_in_run),)
        if shop_was_approved_in_run:
            return build_guard_result(self, GuardVerdict.PASS, evidence = evidence_with_memory)



        # Ask the customer otherwise, with the disagreement named when the use on all cards of the holder reaches the bar and the use on this card does not
        if familiarity.customer_purchase_count >= required_purchase_count:
            question = build_disagreement_question(quoted_shop_name, familiarity)
        else:
            question = build_unfamiliar_question(quoted_shop_name, familiarity, required_purchase_count)
        return build_guard_result(
            self,
            GuardVerdict.STEP_UP,
            reason_code = ReasonCode.UNFAMILIAR_MERCHANT,
            evidence = evidence_with_memory,
            signal = UNFAMILIAR_MERCHANT_SIGNAL,
            customer_message = question,
        )
