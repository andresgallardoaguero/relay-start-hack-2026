# Script: g_period_budget.py
# Purpose: Compare what the customer has spent inside the budget period, plus the amount of one purchase, with the customer's budget
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.engine.display import describe_francs
from app.engine.guards.base import build_guard_result
from app.engine.guards.g_per_order_limit import describe_purchase_amount, describe_purchase_amount_as_subject
from app.models.decision import EvidenceItem, GuardFamily, GuardVerdict, ReasonCode
from app.state.ledger import LedgerSnapshot, sum_approved_in_window









#### Step 1: Name the sources of the evidence ####

# Name the fields of the message, of the policy and of the memory of the run that the evidence points to
TOTAL_AGAINST_LIMIT_SOURCE = "approved spend of the run inside the period plus authorization.billing_amount_chf against policy.expectations.period_limit_chf"
SPENT_BEFORE_SOURCE = "approved purchases of the run inside the period, on simulated time"
PERIOD_DAYS_SOURCE = "policy.expectations.period_days"
AMOUNT_SOURCE = "authorization.billing_amount_chf"
PLATFORM_SPEND_SOURCE = "context.approved_spend_in_period_chf, a cross-check that decides nothing"
TOLERANCE_SOURCE = "policy.expectations.overshoot_tolerance_share"
READING_SOURCE = "policy.expectations.period_limit_reading"



# Round the overshoot share in the evidence to four places
FOUR_PLACES = Decimal("0.0001")



# Tell the customer times on the Swiss clock, and name the months in words
SWISS_TIME_ZONE = ZoneInfo("Europe/Zurich")
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)









#### Step 2: Write the sentences for the customer ####

# Name the period of the budget, where no number of days means everything bought under the instruction
def describe_period(period_days):
    if period_days is None:
        return "under this instruction"
    if period_days == 1:
        return "over the last day"
    return "over the last " + str(period_days) + " days"



# Write a moment on the Swiss clock, as in "17 August at 11.12"
def describe_swiss_moment(moment):
    swiss_moment = moment.astimezone(SWISS_TIME_ZONE)
    return str(swiss_moment.day) + " " + MONTH_NAMES[swiss_moment.month - 1] + " at " + f"{swiss_moment.hour:02d}.{swiss_moment.minute:02d}"



# Say how the total stands against the budget, either exactly at a budget the customer wanted to stay under or above it by an amount
def describe_overshoot(facts, period_days, total_chf, limit_chf, overshoot_chf):
    opening = "This order of " + describe_purchase_amount_as_subject(facts) + " brings your spending " + describe_period(period_days) + " to " + describe_francs(total_chf)
    if overshoot_chf == 0:
        return opening + ", which is exactly your budget of " + describe_francs(limit_chf) + ", and your instruction asked to stay under it."
    return opening + ", which is " + describe_francs(overshoot_chf) + " above your budget of " + describe_francs(limit_chf) + "."



# Say when the next amount leaves the period, which is the earliest counted purchase plus the days of the period.
# A budget over the whole mandate never frees anything, and neither does a period without an earlier purchase.
def describe_next_release(earliest_counted_entry, period_days):
    if period_days is None or earliest_counted_entry is None:
        return ""
    release_moment = earliest_counted_entry.sim_time + timedelta(days = period_days)
    return " " + describe_francs(earliest_counted_entry.amount_chf) + " frees up on " + describe_swiss_moment(release_moment) + "."









#### Step 3: Define the guard ####

# Check the budget over a period, where only approved purchases count and an open question counts only once the customer approves it.
# The guard reads the amount in Swiss francs, the policy and the frozen memory of the run. It sees no store and no clock,
# the period runs on the simulated time of the purchases, and the decision never rests on a division.
@dataclass(frozen = True)
class PeriodBudgetGuard:
    guard_number: int
    guard_id: str
    family: GuardFamily

    def check(self, decision_input, earlier_results):
        facts = decision_input.facts
        expectations = decision_input.policy.expectations
        snapshot = decision_input.state
        limit_chf = expectations.period_limit_chf
        limit_reading = expectations.period_limit_reading
        reading_evidence = EvidenceItem(
            fact = "period_limit_reading",
            value = limit_reading,
            comparator = None,
            threshold = None,
            source = READING_SOURCE,
        )



        # Ask about the purchase when the instruction seems to set a budget that could not be read
        if limit_reading == "unclear" or (limit_reading == "read" and limit_chf is None):
            return build_guard_result(
                self,
                GuardVerdict.UNCERTAIN,
                reason_code = ReasonCode.PERIOD_LIMIT_UNCLEAR,
                evidence = (reading_evidence,),
                customer_message = "Your instruction seems to set a budget that could not be read. This purchase costs " + describe_purchase_amount(facts) + ". Approve it?",
            )



        # Skip when the instruction states no budget. A budget that is present is always enforced, whatever the reading says.
        if limit_chf is None:
            return build_guard_result(self, GuardVerdict.SKIP, evidence = (reading_evidence,))



        # Collect what is known without the memory of the run
        amount_chf = facts.billing_amount_chf
        period_days = expectations.period_days
        limit_is_inclusive = expectations.period_limit_inclusive
        comparator = "<=" if limit_is_inclusive else "<"
        platform_spend = decision_input.event.context.approved_spend_in_period_chf
        period_days_evidence = EvidenceItem(fact = "period_days", value = period_days, comparator = None, threshold = None, source = PERIOD_DAYS_SOURCE)
        amount_evidence = EvidenceItem(fact = "billing_amount_chf", value = float(amount_chf), comparator = None, threshold = None, source = AMOUNT_SOURCE)
        platform_spend_evidence = EvidenceItem(
            fact = "platform_approved_spend_in_period_chf",
            value = None if platform_spend is None else float(platform_spend),
            comparator = None,
            threshold = None,
            source = PLATFORM_SPEND_SOURCE,
        )



        # Refuse to judge without a complete memory of the run, because a missing memory is never permission
        memory_is_usable = isinstance(snapshot, LedgerSnapshot) and snapshot.is_complete
        if not memory_is_usable:
            return build_guard_result(
                self,
                GuardVerdict.UNCERTAIN,
                reason_code = ReasonCode.LEDGER_UNAVAILABLE,
                evidence = (
                    EvidenceItem(fact = "period_total_chf", value = None, comparator = comparator, threshold = float(limit_chf), source = TOTAL_AGAINST_LIMIT_SOURCE),
                    EvidenceItem(fact = "period_spent_before_chf", value = None, comparator = None, threshold = None, source = SPENT_BEFORE_SOURCE),
                    period_days_evidence,
                    amount_evidence,
                    platform_spend_evidence,
                ),
                customer_message = (
                    "Your earlier spending could not be read, so this order of " + describe_purchase_amount(facts)
                    + " cannot be checked against your budget of " + describe_francs(limit_chf) + ". Approve it?"
                ),
            )



        # Add the amount to the approved spend inside the period, where an exclusive budget is already broken by a total equal to it
        approved_spend = sum_approved_in_window(snapshot, period_days)
        total_chf = approved_spend.total_chf + amount_chf
        total_is_over = total_chf > limit_chf or (total_chf == limit_chf and not limit_is_inclusive)
        budget_evidence = (
            EvidenceItem(fact = "period_total_chf", value = float(total_chf), comparator = comparator, threshold = float(limit_chf), source = TOTAL_AGAINST_LIMIT_SOURCE),
            EvidenceItem(fact = "period_spent_before_chf", value = float(approved_spend.total_chf), comparator = None, threshold = None, source = SPENT_BEFORE_SOURCE),
            period_days_evidence,
            amount_evidence,
            platform_spend_evidence,
        )
        if not total_is_over:
            return build_guard_result(self, GuardVerdict.PASS, evidence = budget_evidence)



        # Measure the overshoot and compare it with the tolerated part of the budget by multiplication.
        # The share is divided out for the evidence only, and a budget of zero has no share.
        tolerance_share = expectations.overshoot_tolerance_share
        overshoot_chf = total_chf - limit_chf
        overshoot_is_tolerated = tolerance_share > 0 and overshoot_chf <= limit_chf * tolerance_share
        overshoot_share = None if limit_chf == 0 else float((overshoot_chf / limit_chf).quantize(FOUR_PLACES))
        overshoot_evidence = EvidenceItem(
            fact = "overshoot_share",
            value = overshoot_share,
            comparator = "<=",
            threshold = float(tolerance_share),
            source = TOLERANCE_SOURCE,
        )



        # Ask the customer about a small overshoot and decline every other one, which is every overshoot when the share is zero.
        # Both sentences say how far over the budget the order is and when the next amount frees up.
        overshoot_sentences = (
            describe_overshoot(facts, period_days, total_chf, limit_chf, overshoot_chf)
            + describe_next_release(approved_spend.earliest_counted_entry, period_days)
        )
        if overshoot_is_tolerated:
            return build_guard_result(
                self,
                GuardVerdict.STEP_UP,
                reason_code = ReasonCode.SMALL_OVERSHOOT,
                evidence = budget_evidence + (overshoot_evidence,),
                customer_message = overshoot_sentences + " Approve anyway?",
            )
        return build_guard_result(
            self,
            GuardVerdict.DECLINE,
            reason_code = ReasonCode.OVER_PERIOD_LIMIT,
            evidence = budget_evidence + (overshoot_evidence,),
            customer_message = "Declined. " + overshoot_sentences,
        )
