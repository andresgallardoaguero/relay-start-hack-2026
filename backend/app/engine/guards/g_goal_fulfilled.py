# Script: g_goal_fulfilled.py
# Purpose: Notice an order for the one thing the customer asked for after that thing was already bought in the run, and ask the customer about it
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode
from app.state.ledger import LedgerSnapshot









#### Step 1: Name the sources of the evidence ####

# Name the fields of the policy, the part of the message and the memory of the run that the evidence points to
REQUESTED_FIELD_SOURCE = "policy.expectations.requested_item"
ACTION_SOURCE = "policy.expectations.goal_fulfilled_action"
EARLIER_PURCHASES_SOURCE = "approved purchases of the run whose cart held the requested thing against policy.expectations.requested_item.goal_quantity"
CART_LINES_SOURCE = "authorization.items against policy.expectations.requested_item.kind_keywords"
MEMORY_SOURCE = "memory of the run"



# Name the one status of an earlier purchase that fulfils the goal. An open question has bought nothing yet,
# and a purchase that was refused never went through.
COUNTED_STATUS = "approved"



# Tell the customer times on the Swiss clock, and name the months in words
SWISS_TIME_ZONE = ZoneInfo("Europe/Zurich")
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)









#### Step 2: Find the requested thing ####

# Report whether the words of one item name are the requested thing, which they are when they hold every keyword.
# Without keywords every name is, because the instruction asks for one thing and names no particular one.
def name_words_hold_keywords(name_words, kind_keywords):
    return all(kind_keyword in name_words for kind_keyword in kind_keywords)



# Report whether one earlier purchase held the requested thing on any of its lines. A purchase whose cart is not known has no lines and held nothing.
def entry_held_requested_thing(entry, kind_keywords):
    return any(name_words_hold_keywords(name_words, kind_keywords) for name_words in entry.line_name_words)









#### Step 3: Write the sentences for the customer ####

# Write a moment on the Swiss clock, as in "12 August at 11.40"
def describe_swiss_moment(moment):
    swiss_moment = moment.astimezone(SWISS_TIME_ZONE)
    return str(swiss_moment.day) + " " + MONTH_NAMES[swiss_moment.month - 1] + " at " + f"{swiss_moment.hour:02d}.{swiss_moment.minute:02d}"



# Write the note, which names the moment of the first purchase of the requested thing
def build_note(first_fulfilling_entry):
    return "You already bought this earlier, on " + describe_swiss_moment(first_fulfilling_entry.sim_time) + ". This order would be a second one."



# Write the question, which is the note followed by what the customer has to decide
def build_question(first_fulfilling_entry):
    return build_note(first_fulfilling_entry) + " Approve it anyway?"



# State the question for a run whose earlier orders could not be read
MISSING_MEMORY_QUESTION = "Your earlier orders could not be read, so it cannot be checked whether you already bought this. Approve it anyway?"









#### Step 4: Define the guard ####

# Check whether the one thing the customer asked for was already bought in the run. The guard reads the name words of the cart lines,
# the requested item and the chosen answer of the policy, and the frozen memory of the run, which tells it the name words of every earlier cart.
# One thing means one, so the guard asks the customer by default, who stays responsible for a second one. It writes a note on a pass or stays off
# when the policy says so, and it never declines, because a second purchase that is clean on its own facts breaks no instruction.
# Under the question the guard takes a decision, so a missing memory is uncertain and never a pass. A note is no decision,
# so under the note a missing memory passes without one and is left to the guards that decide on the memory.
@dataclass(frozen = True)
class GoalFulfilledGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        expectations = decision_input.policy.expectations
        requested_item = expectations.requested_item
        action = expectations.goal_fulfilled_action
        snapshot = decision_input.state



        # Skip when the instruction asks for no single thing or for no number of them, which is the case for a recurring instruction
        if requested_item is None or requested_item.goal_quantity is None:
            return build_guard_result(
                self,
                GuardVerdict.SKIP,
                evidence = (
                    EvidenceItem(fact = "goal_quantity", value = "not_stated", comparator = None, threshold = None, source = REQUESTED_FIELD_SOURCE),
                ),
            )



        # Skip when the check is switched off
        if action == "off":
            return build_guard_result(
                self,
                GuardVerdict.SKIP,
                evidence = (
                    EvidenceItem(fact = "goal_fulfilled_action", value = action, comparator = None, threshold = None, source = ACTION_SOURCE),
                ),
            )



        # Refuse to judge without a complete memory of the run when the policy asks the customer, because a missing memory is never permission.
        # Under the note pass without a note, because a note decides nothing. Both say in the evidence that the memory could not be used.
        memory_is_usable = isinstance(snapshot, LedgerSnapshot) and snapshot.is_complete
        missing_memory_evidence = (
            EvidenceItem(fact = "ledger_is_usable", value = False, comparator = None, threshold = None, source = MEMORY_SOURCE),
        )
        if not memory_is_usable and action == "step_up":
            return build_guard_result(
                self,
                GuardVerdict.UNCERTAIN,
                reason_code = ReasonCode.LEDGER_UNAVAILABLE,
                evidence = missing_memory_evidence,
                customer_message = MISSING_MEMORY_QUESTION,
            )
        if not memory_is_usable:
            return build_guard_result(self, GuardVerdict.PASS, evidence = missing_memory_evidence)



        # Count the approved purchases of the run that held the requested thing, and look for the requested thing in the cart of this purchase
        kind_keywords = tuple(requested_item.kind_keywords)
        goal_quantity = requested_item.goal_quantity
        fulfilling_entries = tuple(
            entry
            for entry in snapshot.entries
            if entry.status == COUNTED_STATUS and entry_held_requested_thing(entry, kind_keywords)
        )
        cart_holds_requested_thing = any(name_words_hold_keywords(line.name_words, kind_keywords) for line in decision_input.facts.lines)
        goal_evidence = (
            EvidenceItem(fact = "earlier_purchases_of_requested_thing", value = len(fulfilling_entries), comparator = "<", threshold = goal_quantity, source = EARLIER_PURCHASES_SOURCE),
            EvidenceItem(fact = "cart_holds_requested_thing", value = cart_holds_requested_thing, comparator = None, threshold = None, source = CART_LINES_SOURCE),
        )



        # Pass without a note while the goal is open, which is below the requested number, and for a cart that does not hold the requested thing.
        # A goal needs at least one earlier purchase to be fulfilled, whatever number the policy states.
        goal_is_fulfilled = len(fulfilling_entries) >= max(goal_quantity, 1)
        if not (cart_holds_requested_thing and goal_is_fulfilled):
            return build_guard_result(self, GuardVerdict.PASS, evidence = goal_evidence)



        # Ask the customer when the policy says so, and pass with the note otherwise. Both name the first purchase of the requested thing.
        first_fulfilling_entry = fulfilling_entries[0]
        if action == "step_up":
            return build_guard_result(
                self,
                GuardVerdict.STEP_UP,
                reason_code = ReasonCode.GOAL_ALREADY_FULFILLED,
                evidence = goal_evidence,
                customer_message = build_question(first_fulfilling_entry),
            )
        return build_guard_result(
            self,
            GuardVerdict.PASS,
            reason_code = ReasonCode.GOAL_ALREADY_FULFILLED,
            evidence = goal_evidence,
            note = build_note(first_fulfilling_entry),
        )
