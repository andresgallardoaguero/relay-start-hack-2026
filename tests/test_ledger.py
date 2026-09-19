# Script: test_ledger.py
# Purpose: Check that the memory of a run counts only its own purchases, sums exact amounts inside a period with exact edges, checks a budget again when a customer approves a question, and says whether an earlier purchase held the same cart
# Author: Andrés Gallardo
# Date: September 2026

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.events import read_purchase_message
from app.state.ledger import (
    LedgerSnapshot,
    build_ledger_snapshot,
    build_ledger_snapshot_for_event,
    build_ledger_snapshot_from_records,
    find_budget_objection,
    is_repeat_of_amount,
    read_event_cart_lines,
    sum_approved_in_window,
)









#### Step 1: Define the shared helpers ####

# State the time of the purchase being decided and the two shops of the tests
PURCHASE_TIME = datetime(2026, 8, 17, 9, 12, 0, tzinfo = timezone.utc)
OWN_SHOP = "SHOP_A"
OTHER_SHOP = "SHOP_B"



# Write a moment as ISO text with a trailing Z, as the messages write it
def format_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")



# Build one earlier purchase in the shape the replay keeps and the platform sends
def build_row(identifier, moment, amount, status = "approved", shop = OWN_SHOP):
    return {
        "authorization_id": identifier,
        "timestamp": format_time(moment),
        "merchant_id": shop,
        "billing_amount_chf": amount,
        "status": status,
    }



# Build one decision record in the shape the store keeps
def build_record(identifier, moment, amount_text, status = "approved", run_id = "run_1", mandate_id = "mandate_1", trace = None):
    return {
        "live_authorization_id": identifier,
        "run_id": run_id,
        "mandate_id": mandate_id,
        "sim_timestamp": format_time(moment),
        "amount_chf": amount_text,
        "merchant_id": OWN_SHOP,
        "status": status,
        "trace": trace,
    }



# Build the snapshot of the rows for the purchase being decided
def build_snapshot(rows, own_identifier = "OWN"):
    return build_ledger_snapshot(rows, PURCHASE_TIME, OWN_SHOP, own_identifier)



# Build the stored result of the budget guard the way the guard writes it, inside a stored decision record
def build_trace(total, limit = 50.0, period_days = 7, comparator = "<=", amount = None, verdict = "STEP_UP"):
    return {
        "guards": [
            {"guard_id": "per_order_limit", "verdict": "PASS", "evidence": []},
            {
                "guard_id": "period_budget",
                "verdict": verdict,
                "evidence": [
                    {"fact": "period_total_chf", "value": total, "comparator": comparator, "threshold": limit, "source": "test"},
                    {"fact": "period_days", "value": period_days, "comparator": None, "threshold": None, "source": "test"},
                    {"fact": "billing_amount_chf", "value": amount, "comparator": None, "threshold": None, "source": "test"},
                ],
            },
        ],
    }









#### Step 2: Check which purchases the snapshot keeps ####

# Check that a record of another run never counts, although it carries the same simulated dates
def test_record_of_another_run_never_counts():
    records = [
        build_record("LA_1", PURCHASE_TIME - timedelta(days = 1), "40.00", run_id = "run_1"),
        build_record("LA_2", PURCHASE_TIME - timedelta(days = 1), "99.00", run_id = "run_2"),
    ]
    snapshot = build_ledger_snapshot_from_records(records, "run_1", "mandate_1", PURCHASE_TIME, OWN_SHOP, "OWN")
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("40.00")]
    assert snapshot.is_complete is True



# Check that a record of the same run under another mandate never counts, and that None equals None
def test_record_of_another_mandate_never_counts():
    records = [
        build_record("LA_1", PURCHASE_TIME - timedelta(days = 1), "40.00", mandate_id = "mandate_1"),
        build_record("LA_2", PURCHASE_TIME - timedelta(days = 1), "99.00", mandate_id = "mandate_2"),
        build_record("LA_3", PURCHASE_TIME - timedelta(days = 1), "77.00", mandate_id = None),
    ]
    snapshot_of_the_mandate = build_ledger_snapshot_from_records(records, "run_1", "mandate_1", PURCHASE_TIME, OWN_SHOP, "OWN")
    snapshot_without_mandate = build_ledger_snapshot_from_records(records, "run_1", None, PURCHASE_TIME, OWN_SHOP, "OWN")
    assert [entry.amount_chf for entry in snapshot_of_the_mandate.entries] == [Decimal("40.00")]
    assert [entry.amount_chf for entry in snapshot_without_mandate.entries] == [Decimal("77.00")]



# Check that the purchase being decided never counts against itself
def test_own_row_never_counts():
    rows = [
        build_row("EARLIER", PURCHASE_TIME - timedelta(hours = 1), 10.0),
        build_row("OWN", PURCHASE_TIME, 25.0, status = "pending"),
    ]
    snapshot = build_snapshot(rows, own_identifier = "OWN")
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("10.00")]



# Check that purchases that never went through are left out, and that they never make the snapshot incomplete
@pytest.mark.parametrize("status", ["declined", "expired", "cancelled"])
def test_purchases_that_never_went_through_are_left_out(status):
    rows = [
        build_row("KEPT", PURCHASE_TIME - timedelta(hours = 2), 10.0),
        build_row("LEFT_OUT", PURCHASE_TIME - timedelta(hours = 1), None, status = status),
    ]
    snapshot = build_snapshot(rows)
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("10.00")]
    assert snapshot.is_complete is True



# Check that a purchase after the one being decided is dropped, and that entries come in time order
def test_later_purchase_is_dropped_and_entries_are_in_time_order():
    rows = [
        build_row("SECOND", PURCHASE_TIME - timedelta(hours = 1), 20.0, status = "pending"),
        build_row("LATER", PURCHASE_TIME + timedelta(seconds = 1), 99.0),
        build_row("FIRST", PURCHASE_TIME - timedelta(hours = 2), 10.0),
        build_row("SAME_MOMENT", PURCHASE_TIME, 30.0),
    ]
    snapshot = build_snapshot(rows)
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("10.00"), Decimal("20.00"), Decimal("30.00")]
    assert [entry.status for entry in snapshot.entries] == ["approved", "pending", "approved"]



# Check that an approved purchase that cannot be read, and a purchase with an unknown status, make the snapshot incomplete and are left out
@pytest.mark.parametrize(
    "broken_row",
    [
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00Z", "merchant_id": OWN_SHOP, "billing_amount_chf": None, "status": "approved"},
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00Z", "merchant_id": OWN_SHOP, "billing_amount_chf": "twenty", "status": "approved"},
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00Z", "merchant_id": OWN_SHOP, "billing_amount_chf": True, "status": "approved"},
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00", "merchant_id": OWN_SHOP, "billing_amount_chf": 20.0, "status": "approved"},
        {"authorization_id": "BROKEN", "timestamp": "yesterday", "merchant_id": OWN_SHOP, "billing_amount_chf": 20.0, "status": "approved"},
        {"authorization_id": "BROKEN", "timestamp": None, "merchant_id": OWN_SHOP, "billing_amount_chf": 20.0, "status": "approved"},
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00Z", "merchant_id": None, "billing_amount_chf": 20.0, "status": "approved"},
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00Z", "merchant_id": OWN_SHOP, "billing_amount_chf": 20.0, "status": "unheard_of"},
    ],
    ids = ["no amount", "amount in words", "amount as boolean", "time without zone", "time in words", "no time", "no shop", "unknown status"],
)
def test_unreadable_purchase_makes_the_snapshot_incomplete(broken_row):
    rows = [build_row("KEPT", PURCHASE_TIME - timedelta(hours = 2), 10.0), broken_row]
    snapshot = build_snapshot(rows)
    assert isinstance(snapshot, LedgerSnapshot)
    assert snapshot.is_complete is False
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("10.00")]



# Check that a pending purchase that cannot be read is left out and leaves the snapshot complete, because a pending purchase has spent nothing
@pytest.mark.parametrize(
    "broken_pending_row",
    [
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00Z", "merchant_id": OWN_SHOP, "billing_amount_chf": None, "status": "pending"},
        {"authorization_id": "BROKEN", "timestamp": None, "merchant_id": OWN_SHOP, "billing_amount_chf": 20.0, "status": "pending"},
        {"authorization_id": "BROKEN", "timestamp": "2026-08-16T09:00:00Z", "merchant_id": None, "billing_amount_chf": 20.0, "status": "pending"},
    ],
    ids = ["no amount", "no time", "no shop"],
)
def test_unreadable_pending_purchase_is_left_out_and_keeps_the_snapshot_complete(broken_pending_row):
    rows = [build_row("KEPT", PURCHASE_TIME - timedelta(hours = 2), 10.0), broken_pending_row]
    snapshot = build_snapshot(rows)
    assert snapshot.is_complete is True
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("10.00")]



# Check that later purchases stay in the snapshot only when the caller asks for them, and that the own row is dropped either way
def test_later_purchases_are_kept_on_request_only():
    rows = [
        build_row("EARLIER", PURCHASE_TIME - timedelta(hours = 1), 10.0),
        build_row("OWN", PURCHASE_TIME, 25.0, status = "pending"),
        build_row("LATER", PURCHASE_TIME + timedelta(hours = 1), 30.0),
    ]
    snapshot_for_a_guard = build_ledger_snapshot(rows, PURCHASE_TIME, OWN_SHOP, "OWN")
    snapshot_for_an_approval = build_ledger_snapshot(rows, PURCHASE_TIME, OWN_SHOP, "OWN", keep_later_rows = True)
    assert [entry.amount_chf for entry in snapshot_for_a_guard.entries] == [Decimal("10.00")]
    assert [entry.amount_chf for entry in snapshot_for_an_approval.entries] == [Decimal("10.00"), Decimal("30.00")]



# Check that records of the store keep their later purchases on request as well, still within one run only
def test_records_keep_later_purchases_on_request():
    records = [
        build_record("LA_1", PURCHASE_TIME + timedelta(hours = 1), "40.00", run_id = "run_1"),
        build_record("LA_2", PURCHASE_TIME + timedelta(hours = 1), "99.00", run_id = "run_2"),
    ]
    snapshot = build_ledger_snapshot_from_records(records, "run_1", "mandate_1", PURCHASE_TIME, OWN_SHOP, "OWN", keep_later_rows = True)
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("40.00")]
    assert build_ledger_snapshot_from_records(records, "run_1", "mandate_1", PURCHASE_TIME, OWN_SHOP, "OWN").entries == ()



# Check that the shops are compared while the snapshot is built, and that an entry keeps no identifier
def test_entry_says_whether_the_shop_is_the_same_and_keeps_no_identifier():
    rows = [
        build_row("AT_OWN_SHOP", PURCHASE_TIME - timedelta(hours = 2), 10.0, shop = OWN_SHOP),
        build_row("AT_OTHER_SHOP", PURCHASE_TIME - timedelta(hours = 1), 20.0, shop = OTHER_SHOP),
    ]
    snapshot = build_snapshot(rows)
    assert [entry.same_shop for entry in snapshot.entries] == [True, False]
    assert sorted(vars(snapshot.entries[0])) == ["amount_chf", "line_name_words", "same_cart", "same_shop", "sim_time", "status"]



# Check that a time with an offset is placed correctly next to a time in UTC
def test_time_with_an_offset_is_read():
    rows = [{"authorization_id": "OFFSET", "timestamp": "2026-08-17T10:12:00+02:00", "merchant_id": OWN_SHOP, "billing_amount_chf": 5.0, "status": "approved"}]
    snapshot = build_snapshot(rows)
    assert snapshot.entries[0].sim_time == PURCHASE_TIME - timedelta(hours = 1)









#### Step 3: Check the sum inside a period ####

# Check that a purchase seven days minus one second earlier counts and one exactly seven days earlier has left the period
def test_period_of_seven_days_has_an_exact_edge():
    rows = [
        build_row("EXACTLY_SEVEN_DAYS", PURCHASE_TIME - timedelta(days = 7), 44.5),
        build_row("ONE_SECOND_INSIDE", PURCHASE_TIME - timedelta(days = 7) + timedelta(seconds = 1), 120.0),
        build_row("YESTERDAY", PURCHASE_TIME - timedelta(days = 1), 70.0),
    ]
    approved_spend = sum_approved_in_window(build_snapshot(rows), 7)
    assert approved_spend.total_chf == Decimal("190.00")
    assert approved_spend.earliest_counted_entry.amount_chf == Decimal("120.00")



# Check that without a number of days every approved purchase counts
def test_no_number_of_days_counts_everything():
    rows = [
        build_row("LONG_AGO", PURCHASE_TIME - timedelta(days = 400), 100.0),
        build_row("YESTERDAY", PURCHASE_TIME - timedelta(days = 1), 70.0),
    ]
    approved_spend = sum_approved_in_window(build_snapshot(rows), None)
    assert approved_spend.total_chf == Decimal("170.00")
    assert approved_spend.earliest_counted_entry.amount_chf == Decimal("100.00")



# Check that a pending purchase never counts as spent
def test_pending_purchase_is_not_spent():
    rows = [
        build_row("APPROVED", PURCHASE_TIME - timedelta(days = 1), 70.0),
        build_row("PENDING", PURCHASE_TIME - timedelta(hours = 1), 100.0, status = "pending"),
    ]
    assert sum_approved_in_window(build_snapshot(rows), 7).total_chf == Decimal("70.00")



# Check that amounts with binary noise sum to the exact money they stand for
def test_ten_and_twenty_cents_sum_to_exactly_thirty():
    rows = [
        build_row("TEN_CENTS", PURCHASE_TIME - timedelta(hours = 2), 0.1),
        build_row("TWENTY_CENTS", PURCHASE_TIME - timedelta(hours = 1), 0.2),
    ]
    approved_spend = sum_approved_in_window(build_snapshot(rows), 7)
    assert 0.1 + 0.2 != 0.3
    assert approved_spend.total_chf == Decimal("0.30")
    assert isinstance(approved_spend.total_chf, Decimal)



# Check that a period can end later than the purchase, where a purchase at the end counts and one exactly seven days before the end does not
def test_period_with_its_own_end_has_exact_edges():
    window_end = PURCHASE_TIME + timedelta(days = 2)
    rows = [
        build_row("EXACTLY_SEVEN_DAYS_BEFORE_THE_END", window_end - timedelta(days = 7), 44.5),
        build_row("ONE_SECOND_INSIDE", window_end - timedelta(days = 7) + timedelta(seconds = 1), 120.0),
        build_row("AT_THE_END", window_end, 62.0),
        build_row("AFTER_THE_END", window_end + timedelta(seconds = 1), 99.0),
    ]
    snapshot = build_ledger_snapshot(rows, PURCHASE_TIME, OWN_SHOP, "OWN", keep_later_rows = True)
    assert sum_approved_in_window(snapshot, 7, window_end = window_end).total_chf == Decimal("182.00")
    assert sum_approved_in_window(snapshot, 7).total_chf == Decimal("164.50")
    assert sum_approved_in_window(snapshot, None).total_chf == Decimal("325.50")



# Check that an empty period sums to an exact zero and names no earliest purchase
def test_empty_period_sums_to_zero():
    approved_spend = sum_approved_in_window(build_snapshot([]), 7)
    assert approved_spend.total_chf == Decimal("0")
    assert approved_spend.earliest_counted_entry is None









#### Step 4: Check the budget again when a customer approves a question ####

# State the run of the two open questions, under a budget of CHF 50 over seven days.
# CHF 20.00 and CHF 18.00 are approved, then CHF 15.00 asks at a total of 53.00 and CHF 14.00 asks at a total of 52.00.
FIRST_QUESTION_TIME = PURCHASE_TIME - timedelta(hours = 6)
SECOND_QUESTION_TIME = PURCHASE_TIME - timedelta(hours = 3)



# Build the rows of that run, with the status of the first question as handed in
def build_rows_with_two_questions(first_question_status):
    return [
        build_row("APPROVED_20", PURCHASE_TIME - timedelta(hours = 12), 20.0),
        build_row("APPROVED_18", PURCHASE_TIME - timedelta(hours = 9), 18.0),
        build_row("QUESTION_15", FIRST_QUESTION_TIME, 15.0, status = first_question_status),
        build_row("QUESTION_14", SECOND_QUESTION_TIME, 14.0, status = "pending"),
    ]



# Check that a customer who approves a stated overshoot is never refused
def test_approving_the_first_of_two_open_questions_raises_no_objection():
    snapshot = build_ledger_snapshot(build_rows_with_two_questions("pending"), FIRST_QUESTION_TIME, OWN_SHOP, "QUESTION_15")
    assert find_budget_objection(build_trace(total = 53.0, amount = 15.0), snapshot) is None



# Check that the second approval is refused once the first one has used the room, and that the sentence names the new total
def test_approving_the_second_question_after_the_first_raises_an_objection():
    snapshot = build_ledger_snapshot(build_rows_with_two_questions("approved"), SECOND_QUESTION_TIME, OWN_SHOP, "QUESTION_14")
    objection = find_budget_objection(build_trace(total = 52.0, amount = 14.0), snapshot)
    assert objection == (
        "Since this question was asked you approved another order. "
        "This one would now bring your spending over 7 days to CHF 67.00, above your budget of CHF 50.00."
    )



# Check that the second question can still be approved while the first one is open, because the total it named still holds
def test_approving_the_second_question_first_raises_no_objection():
    snapshot = build_ledger_snapshot(build_rows_with_two_questions("pending"), SECOND_QUESTION_TIME, OWN_SHOP, "QUESTION_14")
    assert find_budget_objection(build_trace(total = 52.0, amount = 14.0), snapshot) is None



# Check that a question about something else, under a total that still fits the budget, is never refused
def test_total_inside_the_budget_raises_no_objection():
    snapshot = build_ledger_snapshot(build_rows_with_two_questions("pending"), FIRST_QUESTION_TIME, OWN_SHOP, "QUESTION_15")
    assert find_budget_objection(build_trace(total = 48.0, limit = 60.0, amount = 15.0, verdict = "PASS"), snapshot) is None



# Check that a grown total equal to an exclusive budget is refused
def test_grown_total_equal_to_an_exclusive_budget_raises_an_objection():
    snapshot = build_ledger_snapshot(build_rows_with_two_questions("approved"), SECOND_QUESTION_TIME, OWN_SHOP, "QUESTION_14")
    objection = find_budget_objection(build_trace(total = 52.0, limit = 67.0, comparator = "<", amount = 14.0), snapshot)
    assert objection is not None
    assert "exactly your budget of CHF 67.00" in objection
    assert find_budget_objection(build_trace(total = 52.0, limit = 67.0, comparator = "<=", amount = 14.0), snapshot) is None



# Check that a memory that cannot be read completely refuses the approval
def test_incomplete_snapshot_raises_an_objection():
    rows = build_rows_with_two_questions("pending") + [
        {"authorization_id": "BROKEN", "timestamp": format_time(PURCHASE_TIME - timedelta(hours = 10)), "merchant_id": OWN_SHOP, "billing_amount_chf": None, "status": "approved"},
    ]
    snapshot = build_ledger_snapshot(rows, FIRST_QUESTION_TIME, OWN_SHOP, "QUESTION_15")
    objection = find_budget_objection(build_trace(total = 53.0, amount = 15.0), snapshot)
    assert snapshot.is_complete is False
    assert objection is not None
    assert "CHF 50.00" in objection



# Check that a question asked without a memory of the run is refused when the total it never named breaks the budget
def test_question_without_a_named_total_is_checked_against_the_budget():
    snapshot = build_ledger_snapshot(build_rows_with_two_questions("pending"), FIRST_QUESTION_TIME, OWN_SHOP, "QUESTION_15")
    breaking_trace = build_trace(total = None, amount = 15.0, verdict = "UNCERTAIN")
    fitting_trace = build_trace(total = None, limit = 60.0, amount = 15.0, verdict = "UNCERTAIN")
    assert "CHF 53.00" in find_budget_objection(breaking_trace, snapshot)
    assert find_budget_objection(fitting_trace, snapshot) is None



# Check that no stored record, a record without the budget guard and a guard that skipped all raise no objection
def test_no_trace_no_guard_and_a_skip_raise_no_objection():
    snapshot = build_ledger_snapshot(build_rows_with_two_questions("approved"), SECOND_QUESTION_TIME, OWN_SHOP, "QUESTION_14")
    trace_without_the_guard = {"guards": [{"guard_id": "per_order_limit", "verdict": "STEP_UP", "evidence": []}]}
    trace_with_a_skip = build_trace(total = 52.0, amount = 14.0, verdict = "SKIP")
    trace_without_a_limit = {"guards": [{"guard_id": "period_budget", "verdict": "UNCERTAIN", "evidence": [{"fact": "period_limit_reading", "value": "unclear", "comparator": None, "threshold": None, "source": "test"}]}]}
    assert find_budget_objection(None, snapshot) is None
    assert find_budget_objection({"guards": []}, snapshot) is None
    assert find_budget_objection(trace_without_the_guard, snapshot) is None
    assert find_budget_objection(trace_with_a_skip, snapshot) is None
    assert find_budget_objection(trace_without_a_limit, snapshot) is None









#### Step 5: Check approvals given in any order ####

# State a run under a budget of CHF 300 over seven days with CHF 234.50 approved.
# A question of CHF 65.00 named a total of 299.50, and a question of CHF 62.00 one day later named 296.50.
EARLIER_QUESTION_TIME = PURCHASE_TIME - timedelta(days = 2)
LATER_QUESTION_TIME = PURCHASE_TIME - timedelta(days = 1)



# Build the rows of that run, with the status of each question as handed in
def build_household_rows(earlier_question_status, later_question_status):
    return [
        build_row("APPROVED_BEFORE", EARLIER_QUESTION_TIME - timedelta(days = 1), 234.5),
        build_row("QUESTION_65", EARLIER_QUESTION_TIME, 65.0, status = earlier_question_status),
        build_row("QUESTION_62", LATER_QUESTION_TIME, 62.0, status = later_question_status),
    ]



# Build the snapshot the approval of one of the two questions is checked against, which keeps the later purchases
def build_approval_snapshot(rows, question_time, question_identifier):
    return build_ledger_snapshot(rows, question_time, OWN_SHOP, question_identifier, keep_later_rows = True)



# Check that approving the later question first raises no objection, because the total it named still holds
def test_approving_the_later_question_first_raises_no_objection():
    snapshot = build_approval_snapshot(build_household_rows("pending", "pending"), LATER_QUESTION_TIME, "QUESTION_62")
    assert find_budget_objection(build_trace(total = 296.5, limit = 300.0, amount = 62.0), snapshot) is None



# Check that approving the earlier question afterwards is refused, because it shares a period with the later one that is approved by now
def test_approving_the_earlier_question_after_the_later_one_raises_an_objection():
    snapshot = build_approval_snapshot(build_household_rows("pending", "approved"), EARLIER_QUESTION_TIME, "QUESTION_65")
    objection = find_budget_objection(build_trace(total = 299.5, limit = 300.0, amount = 65.0), snapshot)
    assert objection == (
        "Since this question was asked you approved another order. "
        "This one would now bring your spending over 7 days to CHF 361.50, above your budget of CHF 300.00."
    )



# Check that the same pair in time order still objects on the second approval
def test_approving_in_time_order_still_objects_on_the_second():
    first_snapshot = build_approval_snapshot(build_household_rows("pending", "pending"), EARLIER_QUESTION_TIME, "QUESTION_65")
    second_snapshot = build_approval_snapshot(build_household_rows("approved", "pending"), LATER_QUESTION_TIME, "QUESTION_62")
    assert find_budget_objection(build_trace(total = 299.5, limit = 300.0, amount = 65.0), first_snapshot) is None
    objection = find_budget_objection(build_trace(total = 296.5, limit = 300.0, amount = 62.0), second_snapshot)
    assert objection is not None
    assert "CHF 361.50" in objection



# Check that a snapshot without the later purchases misses the approval given meanwhile, which is why the approval check keeps them
def test_snapshot_without_later_purchases_misses_the_later_approval():
    snapshot = build_ledger_snapshot(build_household_rows("pending", "approved"), EARLIER_QUESTION_TIME, OWN_SHOP, "QUESTION_65")
    assert find_budget_objection(build_trace(total = 299.5, limit = 300.0, amount = 65.0), snapshot) is None



# Check that a later approved purchase exactly seven days after the questioned one shares no period with it, and one second earlier does
def test_later_purchase_exactly_seven_days_on_shares_no_period():
    rows_at_seven_days = [
        build_row("APPROVED_BEFORE", EARLIER_QUESTION_TIME - timedelta(days = 1), 234.5),
        build_row("LATER_APPROVED", EARLIER_QUESTION_TIME + timedelta(days = 7), 250.0),
    ]
    rows_one_second_earlier = [
        build_row("APPROVED_BEFORE", EARLIER_QUESTION_TIME - timedelta(days = 1), 234.5),
        build_row("LATER_APPROVED", EARLIER_QUESTION_TIME + timedelta(days = 7) - timedelta(seconds = 1), 250.0),
    ]
    trace = build_trace(total = 299.5, limit = 300.0, amount = 65.0)
    assert find_budget_objection(trace, build_approval_snapshot(rows_at_seven_days, EARLIER_QUESTION_TIME, "QUESTION_65")) is None
    objection = find_budget_objection(trace, build_approval_snapshot(rows_one_second_earlier, EARLIER_QUESTION_TIME, "QUESTION_65"))
    assert objection is not None
    assert "CHF 315.00" in objection



# Check that a customer who approves a stated overshoot of CHF 324.00 with nothing approved since is never refused
def test_stated_overshoot_with_nothing_approved_since_raises_no_objection():
    rows = [
        build_row("APPROVED_BEFORE", EARLIER_QUESTION_TIME - timedelta(days = 1), 300.0),
        build_row("PENDING_LATER", LATER_QUESTION_TIME, 40.0, status = "pending"),
    ]
    snapshot = build_approval_snapshot(rows, EARLIER_QUESTION_TIME, "QUESTION_24")
    assert find_budget_objection(build_trace(total = 324.0, limit = 300.0, amount = 24.0), snapshot) is None



# Check that a budget over the whole mandate counts an approval given to a later purchase as well
def test_budget_over_the_whole_mandate_sees_a_later_approval():
    snapshot = build_approval_snapshot(build_household_rows("pending", "approved"), EARLIER_QUESTION_TIME, "QUESTION_65")
    objection = find_budget_objection(build_trace(total = 299.5, limit = 300.0, period_days = None, amount = 65.0), snapshot)
    assert objection == (
        "Since this question was asked you approved another order. "
        "This one would now bring your spending under this instruction to CHF 361.50, above your budget of CHF 300.00."
    )









#### Step 6: Check the carts and the repeated amount ####

# State the cart of the purchase being decided, with two items on two lines
OWN_CART = [
    {"item_id": "ITEM_MONITOR", "item_name": "27-inch computer monitors", "quantity": 1},
    {"item_id": "ITEM_CABLE", "item_name": "Display cable", "quantity": 2},
]



# Build the snapshot of one earlier approved purchase with the given cart, for the purchase being decided with its own cart
def build_snapshot_with_carts(row_cart_lines, own_cart_lines = OWN_CART):
    row = {**build_row("EARLIER", PURCHASE_TIME - timedelta(hours = 1), 10.0), "cart_lines": row_cart_lines}
    return build_ledger_snapshot([row], PURCHASE_TIME, OWN_SHOP, "OWN", own_cart_lines = own_cart_lines)



# Check that the same items in the same quantities are the same cart, whatever the order of the lines and whatever the names say
def test_same_items_in_another_line_order_are_the_same_cart():
    cart_in_another_order = [
        {"item_id": "ITEM_CABLE", "item_name": "Cable under another name", "quantity": 2},
        {"item_id": "ITEM_MONITOR", "item_name": "Monitor under another name", "quantity": 1},
    ]
    snapshot = build_snapshot_with_carts(cart_in_another_order)
    assert [entry.same_cart for entry in snapshot.entries] == [True]
    assert snapshot.is_complete is True



# Check that another quantity, one more line, one line less and another item are not the same cart
@pytest.mark.parametrize(
    "other_cart",
    [
        [{"item_id": "ITEM_MONITOR", "item_name": "Monitor", "quantity": 2}, {"item_id": "ITEM_CABLE", "item_name": "Cable", "quantity": 2}],
        OWN_CART + [{"item_id": "ITEM_PLAN", "item_name": "Protection plan", "quantity": 1}],
        OWN_CART[:1],
        [{"item_id": "ITEM_MONITOR", "item_name": "Monitor", "quantity": 1}, {"item_id": "ITEM_STAND", "item_name": "Stand", "quantity": 2}],
    ],
    ids = ["another quantity", "one more line", "one line less", "another item"],
)
def test_a_cart_that_differs_is_not_the_same_cart(other_cart):
    snapshot = build_snapshot_with_carts(other_cart)
    assert [entry.same_cart for entry in snapshot.entries] == [False]
    assert snapshot.is_complete is True



# Check that an item on two lines counts with the sum of both
def test_item_on_two_lines_counts_with_its_whole_quantity():
    cart_with_a_split_line = [
        {"item_id": "ITEM_MONITOR", "item_name": "Monitor", "quantity": 1},
        {"item_id": "ITEM_CABLE", "item_name": "Cable", "quantity": 1},
        {"item_id": "ITEM_CABLE", "item_name": "Cable", "quantity": 1},
    ]
    assert [entry.same_cart for entry in build_snapshot_with_carts(cart_with_a_split_line).entries] == [True]



# Check that a row without a cart, and a row whose cart cannot be read, give False and no words and leave the snapshot complete,
# because the money of such a purchase can still be counted
@pytest.mark.parametrize(
    "unknown_cart",
    [
        None,
        [],
        "ITEM_MONITOR",
        [{"item_id": "ITEM_MONITOR", "item_name": "Monitor"}],
        [{"item_id": "", "item_name": "Monitor", "quantity": 1}],
        [{"item_id": "ITEM_MONITOR", "item_name": "Monitor", "quantity": True}],
        [{"item_id": "ITEM_MONITOR", "item_name": "Monitor", "quantity": "1"}],
        OWN_CART + ["not a line"],
    ],
    ids = ["no cart", "empty cart", "cart as text", "no quantity", "empty identifier", "quantity as boolean", "quantity as text", "one broken line"],
)
def test_row_without_a_known_cart_gives_false_and_keeps_the_snapshot_complete(unknown_cart):
    snapshot = build_snapshot_with_carts(unknown_cart)
    assert [entry.same_cart for entry in snapshot.entries] == [False]
    assert [entry.line_name_words for entry in snapshot.entries] == [()]
    assert [entry.amount_chf for entry in snapshot.entries] == [Decimal("10.00")]
    assert snapshot.is_complete is True



# Check that a row without the key cart_lines reads like a row without a cart
def test_row_without_the_cart_key_gives_false():
    rows = [build_row("EARLIER", PURCHASE_TIME - timedelta(hours = 1), 10.0)]
    snapshot = build_ledger_snapshot(rows, PURCHASE_TIME, OWN_SHOP, "OWN", own_cart_lines = OWN_CART)
    assert [entry.same_cart for entry in snapshot.entries] == [False]
    assert snapshot.is_complete is True



# Check that no earlier purchase holds the same cart when the cart of the purchase being decided is not handed in, while the name words are still read
def test_without_the_own_cart_no_cart_is_the_same():
    snapshot = build_snapshot_with_carts(OWN_CART, own_cart_lines = None)
    assert [entry.same_cart for entry in snapshot.entries] == [False]
    assert [entry.line_name_words for entry in snapshot.entries] == [(("27", "inch", "computer", "monitor"), ("display", "cable"))]



# Check that the words of the item names are kept per line, in lower case and in singular form, and that the entry still keeps no identifier
def test_entry_keeps_the_name_words_of_each_line_and_no_identifier():
    snapshot = build_snapshot_with_carts(OWN_CART)
    assert snapshot.entries[0].line_name_words == (("27", "inch", "computer", "monitor"), ("display", "cable"))
    assert "ITEM_MONITOR" not in repr(snapshot.entries[0])
    assert "ITEM_CABLE" not in repr(snapshot.entries[0])



# Check that a record of the store reads its cart from its decision record, and that a record without one has no cart
def test_record_reads_its_cart_from_its_trace():
    records = [
        build_record("LA_1", PURCHASE_TIME - timedelta(hours = 3), "10.00", trace = {"facts": {"cart_lines": list(reversed(OWN_CART))}}),
        build_record("LA_2", PURCHASE_TIME - timedelta(hours = 2), "10.00", trace = {"facts": {"cart_lines": OWN_CART[:1]}}),
        build_record("LA_3", PURCHASE_TIME - timedelta(hours = 1), "10.00", trace = None),
        build_record("LA_4", PURCHASE_TIME - timedelta(minutes = 30), "10.00", trace = {"facts": {}}),
        build_record("LA_5", PURCHASE_TIME - timedelta(minutes = 20), "10.00", trace = {"guards": []}),
    ]
    snapshot = build_ledger_snapshot_from_records(records, "run_1", "mandate_1", PURCHASE_TIME, OWN_SHOP, "OWN", own_cart_lines = OWN_CART)
    snapshot_without_own_cart = build_ledger_snapshot_from_records(records, "run_1", "mandate_1", PURCHASE_TIME, OWN_SHOP, "OWN")
    assert [entry.same_cart for entry in snapshot.entries] == [True, False, False, False, False]
    assert [len(entry.line_name_words) for entry in snapshot.entries] == [2, 1, 0, 0, 0]
    assert snapshot.is_complete is True
    assert [entry.same_cart for entry in snapshot_without_own_cart.entries] == [False] * 5
    assert [len(entry.line_name_words) for entry in snapshot_without_own_cart.entries] == [2, 1, 0, 0, 0]



# Check that the snapshot built for a purchase message reads the own cart from the message
def test_snapshot_for_an_event_reads_the_own_cart_from_the_message(example_message):
    event = read_purchase_message(example_message)
    example_cart = [{"item_id": "IT_EXAMPLE_0001", "item_name": "Example grocery item", "quantity": 1}]
    rows = [
        {**build_row("SAME", event.authorization.timestamp - timedelta(hours = 2), 10.0), "cart_lines": example_cart},
        {**build_row("OTHER", event.authorization.timestamp - timedelta(hours = 1), 10.0), "cart_lines": OWN_CART},
    ]
    snapshot = build_ledger_snapshot_for_event(rows, event)
    assert [entry.same_cart for entry in snapshot.entries] == [True, False]
    assert read_event_cart_lines(event) == example_cart



# Check the repeated amount at exactly 10 percent of the earlier amount in both directions, one cent beyond, and with an earlier amount of zero
def test_repeated_amount_has_exact_edges():
    share = Decimal("0.10")
    assert is_repeat_of_amount(Decimal("289.00"), Decimal("289.00"), share) is True
    assert is_repeat_of_amount(Decimal("100.00"), Decimal("110.00"), share) is True
    assert is_repeat_of_amount(Decimal("100.00"), Decimal("110.01"), share) is False
    assert is_repeat_of_amount(Decimal("100.00"), Decimal("90.00"), share) is True
    assert is_repeat_of_amount(Decimal("100.00"), Decimal("89.99"), share) is False
    assert is_repeat_of_amount(Decimal("0.00"), Decimal("0.00"), share) is True
    assert is_repeat_of_amount(Decimal("0.00"), Decimal("0.01"), share) is False



# Check that the share applies to the earlier amount and not to the new one, and that a share handed in as a float or as text reads the same
def test_repeated_amount_takes_the_share_of_the_earlier_amount():
    assert is_repeat_of_amount(Decimal("110.00"), Decimal("99.00"), Decimal("0.10")) is True
    assert is_repeat_of_amount(Decimal("110.00"), Decimal("98.99"), Decimal("0.10")) is False
    assert is_repeat_of_amount(Decimal("100.00"), Decimal("110.00"), 0.1) is True
    assert is_repeat_of_amount(Decimal("100.00"), Decimal("110.01"), "0.10") is False
    assert is_repeat_of_amount(Decimal("100.00"), Decimal("100.01"), Decimal("0")) is False
