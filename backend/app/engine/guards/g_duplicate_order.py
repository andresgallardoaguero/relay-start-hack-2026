# Script: g_duplicate_order.py
# Purpose: Notice an order that repeats an order the customer already has, which is the same cart at the same shop for nearly the same amount shortly after
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from datetime import timedelta

from app.engine.display import describe_francs
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode
from app.state.ledger import LedgerSnapshot, is_repeat_of_amount









#### Step 1: Name the sources of the evidence ####

# Name the fields of the message, of the policy and of the memory of the run that the evidence points to
REPEATED_ORDERS_SOURCE = "approved orders of the run with the same cart at the same shop inside the hours and the amount share, on simulated time"
WINDOW_SOURCE = "policy.expectations.duplicate_window_hours"
AMOUNT_SHARE_SOURCE = "policy.expectations.duplicate_amount_share"
AMOUNT_SOURCE = "authorization.billing_amount_chf"
RELATED_ORDER_SOURCE = "authorization.related_authorization_status"
MEMORY_SOURCE = "memory of the run"



# Name the one status of an earlier order that counts, because only an order the customer already has can be bought twice.
# An open question has bought nothing yet, and an order that was refused never went through.
COUNTED_STATUS = "approved"



# Name the statuses of a related order that make this order a new attempt after a refusal, which is normal behavior and no repeat
REFUSED_RELATED_STATUSES = ("declined", "cancelled")



# State the seconds of one minute and of one hour, and from how many hours on the time is no longer written in minutes and in hours
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
HOURS_WRITTEN_IN_MINUTES = 2
HOURS_WRITTEN_IN_HOURS = 48









#### Step 2: Write the sentences for the customer ####

# Write a whole number with its unit, as in "1 minute" and "25 minutes"
def describe_count(count, unit_name):
    return str(count) + " " + unit_name + ("" if count == 1 else "s")



# Write how long ago the earlier order was, in whole minutes below two hours, in whole hours below two days and in whole days from there on
def describe_time_ago(time_since_earlier_order):
    whole_seconds = int(time_since_earlier_order.total_seconds())
    if whole_seconds < SECONDS_PER_MINUTE:
        return "less than a minute ago"
    if whole_seconds < HOURS_WRITTEN_IN_MINUTES * SECONDS_PER_HOUR:
        return describe_count(whole_seconds // SECONDS_PER_MINUTE, "minute") + " ago"
    if whole_seconds < HOURS_WRITTEN_IN_HOURS * SECONDS_PER_HOUR:
        return describe_count(whole_seconds // SECONDS_PER_HOUR, "hour") + " ago"
    return describe_count(whole_seconds // (24 * SECONDS_PER_HOUR), "day") + " ago"



# Write the question, which names the latest of the repeated orders with its time and its amount and says what the customer has to decide
def build_question(latest_repeated_entry, time_since_latest_order):
    return (
        "This looks like the same order again. You bought the same items at this shop " + describe_time_ago(time_since_latest_order)
        + " for " + describe_francs(latest_repeated_entry.amount_chf) + ". Approve it a second time?"
    )









#### Step 3: Define the guard ####

# Check for an order that repeats an order the customer already has. The guard reads the amount in Swiss francs, the status of the related order
# that the message names, the policy and the frozen memory of the run. It sees no store and no clock, the hours run on the simulated time
# of the purchases, and the memory tells it whether an earlier order was at the same shop and held the same cart, so it never compares an identifier.
# It only ever asks, because buying the same thing twice can be what the customer wants.
@dataclass(frozen = True)
class DuplicateOrderGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        facts = decision_input.facts
        expectations = decision_input.policy.expectations
        snapshot = decision_input.state



        # Refuse to judge without a complete memory of the run, because a missing memory is never permission
        memory_is_usable = isinstance(snapshot, LedgerSnapshot) and snapshot.is_complete
        if not memory_is_usable:
            return build_guard_result(
                self,
                GuardVerdict.UNCERTAIN,
                reason_code = ReasonCode.LEDGER_UNAVAILABLE,
                evidence = (
                    EvidenceItem(fact = "ledger_is_usable", value = False, comparator = None, threshold = None, source = MEMORY_SOURCE),
                ),
                customer_message = (
                    "Your earlier orders could not be read, so this order of " + describe_francs(facts.billing_amount_chf)
                    + " cannot be checked against them. Approve it?"
                ),
            )



        # Pass a new attempt after a refusal without comparing anything, because trying again after a refused order is normal behavior
        related_order_status = decision_input.event.authorization.related_authorization_status
        if related_order_status in REFUSED_RELATED_STATUSES:
            return build_guard_result(
                self,
                GuardVerdict.PASS,
                reason_code = ReasonCode.RE_QUOTE_COMPLIANT,
                evidence = (
                    EvidenceItem(fact = "related_order_status", value = related_order_status, comparator = "in", threshold = " ".join(REFUSED_RELATED_STATUSES), source = RELATED_ORDER_SOURCE),
                ),
            )



        # Take the approved orders with the same cart at the same shop inside the hours, where an order exactly that long ago still counts,
        # and whose amount this amount repeats, where a difference of exactly the share still counts
        amount_chf = facts.billing_amount_chf
        window_hours = expectations.duplicate_window_hours
        amount_share = expectations.duplicate_amount_share
        window_start = snapshot.purchase_time - timedelta(hours = window_hours)
        repeated_entries = tuple(
            entry
            for entry in snapshot.entries
            if entry.same_shop
            and entry.same_cart
            and entry.status == COUNTED_STATUS
            and entry.sim_time >= window_start
            and is_repeat_of_amount(entry.amount_chf, amount_chf, amount_share)
        )
        repeat_evidence = (
            EvidenceItem(fact = "repeated_orders_in_window", value = len(repeated_entries), comparator = "=", threshold = 0, source = REPEATED_ORDERS_SOURCE),
            EvidenceItem(fact = "duplicate_window_hours", value = window_hours, comparator = None, threshold = None, source = WINDOW_SOURCE),
            EvidenceItem(fact = "duplicate_amount_share", value = float(amount_share), comparator = None, threshold = None, source = AMOUNT_SHARE_SOURCE),
            EvidenceItem(fact = "billing_amount_chf", value = float(amount_chf), comparator = None, threshold = None, source = AMOUNT_SOURCE),
        )



        # Pass when no such order exists, and ask the customer otherwise, with the latest of them named in the question
        if not repeated_entries:
            return build_guard_result(self, GuardVerdict.PASS, evidence = repeat_evidence)
        latest_repeated_entry = repeated_entries[-1]
        return build_guard_result(
            self,
            GuardVerdict.STEP_UP,
            reason_code = ReasonCode.DUPLICATE_SUSPECTED,
            evidence = repeat_evidence,
            customer_message = build_question(latest_repeated_entry, snapshot.purchase_time - latest_repeated_entry.sim_time),
        )
