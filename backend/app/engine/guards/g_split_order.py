# Script: g_split_order.py
# Purpose: Notice an order that fits the limit per order on its own and breaks it together with the orders placed at the same shop shortly before
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.engine.display import describe_francs
from app.engine.guards.base import build_guard_result
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode
from app.state.ledger import LedgerSnapshot, is_repeat_of_amount









#### Step 1: Name the sources of the evidence ####

# Name the fields of the message, of the policy and of the memory of the run that the evidence points to
COMBINED_AGAINST_LIMIT_SOURCE = "orders of the run at the same shop inside the minutes plus authorization.billing_amount_chf against policy.expectations.per_order_limit_chf"
AMOUNT_AGAINST_LIMIT_SOURCE = "authorization.billing_amount_chf against policy.expectations.per_order_limit_chf"
ORDER_COUNT_SOURCE = "approved and pending orders of the run at the same shop, on simulated time"
WINDOW_SOURCE = "policy.expectations.split_order_window_minutes"
MEMORY_SOURCE = "memory of the run"



# Name the statuses of the earlier orders that count, where an open question counts because the customer may still approve it
COUNTED_STATUSES = ("approved", "pending")



# Name the one status of an earlier order that can be repeated, because only an order the customer already has can be bought twice
REPEAT_STATUS = "approved"



# State the seconds of one minute, for writing how long ago the last order was
SECONDS_PER_MINUTE = 60









#### Step 2: Write the sentences for the customer ####

# Write how long after the last order this one arrived, in whole minutes
def describe_minutes_after(time_since_last_order):
    whole_minutes = int(time_since_last_order.total_seconds() // SECONDS_PER_MINUTE)
    if whole_minutes < 1:
        return "less than a minute after"
    if whole_minutes == 1:
        return "1 minute after"
    return str(whole_minutes) + " minutes after"



# Name the earlier orders, which is one amount or a number of orders with their sum
def describe_earlier_orders(nearby_entries, nearby_sum_chf):
    if len(nearby_entries) == 1:
        return describe_francs(nearby_sum_chf)
    return str(len(nearby_entries)) + " other orders of " + describe_francs(nearby_sum_chf) + " in total"



# Say what the orders look like, which is one order split in two with one earlier order and one split into several with more
def describe_suspicion(nearby_entries):
    if len(nearby_entries) == 1:
        return " This looks like one order split in two."
    return " This looks like one order split into several."



# Write the question, which says what was ordered when, what it adds up to and what the customer has to decide
def build_question(amount_chf, nearby_entries, nearby_sum_chf, combined_chf, limit_chf, time_since_last_order):
    opening = (
        describe_francs(amount_chf) + ", " + describe_minutes_after(time_since_last_order) + " "
        + describe_earlier_orders(nearby_entries, nearby_sum_chf) + " at the same shop."
    )
    if combined_chf == limit_chf:
        standing = " Together they come to exactly your limit of " + describe_francs(limit_chf) + " per order, and your instruction asked to stay under it."
    else:
        standing = " Together they come to " + describe_francs(combined_chf) + ", above your limit of " + describe_francs(limit_chf) + " per order."
    return opening + standing + describe_suspicion(nearby_entries) + " Approve it?"









#### Step 3: Define the guard ####

# Check for an order split in two, which the limit per order cannot see, because each half fits on its own.
# The guard reads the amount in Swiss francs, the policy and the frozen memory of the run. It sees no store and no clock,
# the minutes run on the simulated time of the purchases, and it only ever asks, because two orders in a row can be honest.
@dataclass(frozen = True)
class SplitOrderGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        facts = decision_input.facts
        expectations = decision_input.policy.expectations
        snapshot = decision_input.state
        limit_chf = expectations.per_order_limit_chf



        # Skip when the instruction states no limit per order, because nothing can be split around a limit that is not there
        if limit_chf is None:
            return build_guard_result(self, GuardVerdict.SKIP)



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



        # Pass an order that is over the limit on its own, because the limit per order answers it and each half of a split order fits on its own
        amount_chf = facts.billing_amount_chf
        limit_is_inclusive = expectations.per_order_limit_inclusive
        comparator = "<=" if limit_is_inclusive else "<"
        amount_is_over = amount_chf > limit_chf or (amount_chf == limit_chf and not limit_is_inclusive)
        if amount_is_over:
            return build_guard_result(
                self,
                GuardVerdict.PASS,
                evidence = (
                    EvidenceItem(fact = "billing_amount_chf", value = float(amount_chf), comparator = comparator, threshold = float(limit_chf), source = AMOUNT_AGAINST_LIMIT_SOURCE),
                ),
            )



        # Take the approved and the pending orders at the same shop inside the minutes, where an order exactly that long ago still counts.
        # An approved earlier order that this one repeats, with the same cart at nearly the same amount, is left out, because two halves of one order
        # differ from each other, and the same order twice is a repeat that the check for repeated orders answers.
        # That check counts approved orders only, so a repeat of an open question stays here, and an identical second order never passes both checks.
        window_minutes = expectations.split_order_window_minutes
        window_start = snapshot.purchase_time - timedelta(minutes = window_minutes)
        nearby_entries = tuple(
            entry
            for entry in snapshot.entries
            if entry.same_shop
            and entry.status in COUNTED_STATUSES
            and entry.sim_time >= window_start
            and not (entry.status == REPEAT_STATUS and entry.same_cart and is_repeat_of_amount(entry.amount_chf, amount_chf, expectations.duplicate_amount_share))
        )
        nearby_sum_chf = sum((entry.amount_chf for entry in nearby_entries), Decimal("0"))
        combined_chf = nearby_sum_chf + amount_chf
        combined_is_over = combined_chf > limit_chf or (combined_chf == limit_chf and not limit_is_inclusive)
        split_evidence = (
            EvidenceItem(fact = "combined_same_shop_chf", value = float(combined_chf), comparator = comparator, threshold = float(limit_chf), source = COMBINED_AGAINST_LIMIT_SOURCE),
            EvidenceItem(fact = "same_shop_orders_in_window", value = len(nearby_entries), comparator = None, threshold = None, source = ORDER_COUNT_SOURCE),
            EvidenceItem(fact = "split_order_window_minutes", value = window_minutes, comparator = None, threshold = None, source = WINDOW_SOURCE),
            EvidenceItem(fact = "billing_amount_chf", value = float(amount_chf), comparator = None, threshold = None, source = AMOUNT_AGAINST_LIMIT_SOURCE),
        )



        # Pass when no such order exists or when all of them together still fit the limit, and ask the customer otherwise
        if not nearby_entries or not combined_is_over:
            return build_guard_result(self, GuardVerdict.PASS, evidence = split_evidence)
        time_since_last_order = snapshot.purchase_time - nearby_entries[-1].sim_time
        return build_guard_result(
            self,
            GuardVerdict.STEP_UP,
            reason_code = ReasonCode.SPLIT_ORDER_SUSPECTED,
            evidence = split_evidence,
            customer_message = build_question(amount_chf, nearby_entries, nearby_sum_chf, combined_chf, limit_chf, time_since_last_order),
        )
