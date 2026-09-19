# Script: ledger.py
# Purpose: Build the frozen memory of one run that the engine decides on, sum the approved spend inside a period, and check a budget again when a customer approves a question
# Author: Andrés Gallardo
# Date: September 2026

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from app.engine.display import describe_francs
from app.engine.facts import to_money
from app.policyc.instruction_text import read_words









#### Step 1: Define the snapshot ####

# Hold one earlier purchase of the run, with its simulated time, its amount in Swiss francs and its status.
# same_shop says whether it was made at the shop of the purchase being decided, and same_cart whether it held exactly the same items
# in the same quantities. line_name_words holds the words of each item name of the earlier purchase, in lower case and in singular form,
# which is the form the fact sheet gives the names of the cart being decided. An earlier purchase whose cart is not known has same_cart False and no words.
# No identifier is kept, so no guard can decide on one.
@dataclass(frozen = True)
class LedgerEntry:
    sim_time: datetime
    amount_chf: Decimal
    status: str
    same_shop: bool
    same_cart: bool = False
    line_name_words: Tuple[Tuple[str, ...], ...] = ()



# Hold the earlier purchases of the run as they stand at the simulated time of the purchase being decided, in time order.
# is_complete is False when an earlier purchase that may count could not be read, and a guard must then never treat the memory as permission.
@dataclass(frozen = True)
class LedgerSnapshot:
    purchase_time: datetime
    entries: Tuple[LedgerEntry, ...]
    is_complete: bool



# Name the statuses that stay in the snapshot, and the statuses of purchases that never went through and are left out
KEPT_STATUSES = ("approved", "pending")
LEFT_OUT_STATUSES = ("declined", "expired", "cancelled")



# Name the guard whose stored result is read when a customer approves a question
PERIOD_BUDGET_GUARD_ID = "period_budget"









#### Step 2: Read one earlier purchase ####

# Read a simulated time from ISO text, where a trailing Z means UTC. A time without a zone cannot be placed and gives None.
def read_ledger_time(time_value):
    if isinstance(time_value, datetime):
        moment = time_value
    elif isinstance(time_value, str):
        try:
            moment = datetime.fromisoformat(time_value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.utcoffset() is None:
        return None
    return moment



# Read an amount through the text of the number into money with two places. Nothing, a boolean, a text that is no number
# and a negative number all give None.
def read_ledger_amount(amount_value):
    if amount_value is None or isinstance(amount_value, bool):
        return None
    try:
        amount_chf = to_money(amount_value)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if amount_chf < 0:
        return None
    return amount_chf



# Report whether one cart line can be read, which needs an item identifier and an item name as text that is not empty and a quantity as a whole number above zero
def cart_line_is_readable(cart_line):
    if not isinstance(cart_line, dict):
        return False
    item_identifier = cart_line.get("item_id")
    item_name = cart_line.get("item_name")
    quantity = cart_line.get("quantity")
    identifier_is_readable = isinstance(item_identifier, str) and item_identifier != ""
    name_is_readable = isinstance(item_name, str) and item_name != ""
    quantity_is_readable = isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0
    return identifier_is_readable and name_is_readable and quantity_is_readable



# Report whether a cart is known, which needs a list with at least one line where every line can be read.
# A cart with one line that cannot be read is not known as a whole, because a cart that is partly known can never be called the same as another.
def cart_is_known(cart_lines):
    if not isinstance(cart_lines, (list, tuple)) or len(cart_lines) == 0:
        return False
    return all(cart_line_is_readable(cart_line) for cart_line in cart_lines)



# Reduce a known cart to what it holds, which is every item with its whole quantity, in the order of the item identifiers.
# The order of the lines drops out, and an item on two lines counts with the sum of both.
def read_cart_content(cart_lines):
    item_identifiers = sorted({cart_line["item_id"] for cart_line in cart_lines})
    return tuple(
        (
            item_identifier,
            sum(cart_line["quantity"] for cart_line in cart_lines if cart_line["item_id"] == item_identifier),
        )
        for item_identifier in item_identifiers
    )



# Compare the cart of an earlier purchase with the cart of the purchase being decided. Only two known carts with the same items
# in the same quantities are the same, so a cart that is not known on either side gives False.
def carts_are_the_same(row_cart_lines, own_cart_lines):
    if not cart_is_known(row_cart_lines) or not cart_is_known(own_cart_lines):
        return False
    return read_cart_content(row_cart_lines) == read_cart_content(own_cart_lines)



# Read the words of each item name of a cart in line order, with the function the fact sheet reads the names of the cart being decided with.
# A cart that is not known gives no words.
def read_line_name_words(cart_lines):
    if not cart_is_known(cart_lines):
        return ()
    return tuple(read_words(cart_line["item_name"]) for cart_line in cart_lines)



# Report whether an amount repeats an earlier amount, which it does when the two differ by at most the share of the earlier amount.
# The share is multiplied and never divided by, so an earlier amount of zero is repeated by another zero only. A difference of exactly the share still counts.
def is_repeat_of_amount(entry_amount_chf, amount_chf, share):
    allowed_difference_chf = entry_amount_chf * Decimal(str(share))
    return abs(amount_chf - entry_amount_chf) <= allowed_difference_chf



# Describe what one row gave, which is an entry, nothing on purpose, or nothing because the row could not be read
@dataclass(frozen = True)
class LedgerRowReading:
    entry: Optional[LedgerEntry]
    is_readable: bool



# State the two readings without an entry
ROW_LEFT_OUT = LedgerRowReading(entry = None, is_readable = True)
ROW_UNREADABLE = LedgerRowReading(entry = None, is_readable = False)



# Read one row. A purchase that never went through is left out, and so is a purchase after the one being decided,
# unless the caller asks to keep the later ones.
# An approved row whose time, amount or shop cannot be read is unreadable, and so is a row with an unknown status. Both make the snapshot incomplete,
# because money may have been spent that cannot be counted. A pending row that cannot be read is left out, because a pending purchase has spent nothing.
# A row may carry its cart under the key cart_lines. A row without a cart that can be read stays readable, because its money can still be counted.
def read_ledger_row(row, purchase_time, shop_identifier, keep_later_rows, own_cart_lines = None):
    status = row.get("status")
    if status in LEFT_OUT_STATUSES:
        return ROW_LEFT_OUT
    if status not in KEPT_STATUSES:
        return ROW_UNREADABLE



    # Read the time, the amount and the shop, and set the row aside when one of them is missing
    sim_time = read_ledger_time(row.get("timestamp"))
    amount_chf = read_ledger_amount(row.get("billing_amount_chf"))
    row_shop_identifier = row.get("merchant_id")
    shop_is_readable = isinstance(row_shop_identifier, str) and row_shop_identifier != ""
    if sim_time is None or amount_chf is None or not shop_is_readable:
        return ROW_LEFT_OUT if status == "pending" else ROW_UNREADABLE
    if sim_time > purchase_time and not keep_later_rows:
        return ROW_LEFT_OUT



    # Compare the shops and the carts here, so the entry carries the answers and not the identifiers
    row_cart_lines = row.get("cart_lines")
    return LedgerRowReading(
        entry = LedgerEntry(
            sim_time = sim_time,
            amount_chf = amount_chf,
            status = status,
            same_shop = row_shop_identifier == shop_identifier,
            same_cart = carts_are_the_same(row_cart_lines, own_cart_lines),
            line_name_words = read_line_name_words(row_cart_lines),
        ),
        is_readable = True,
    )









#### Step 3: Build the snapshot ####

# Build the snapshot from the earlier purchases of one run, each a dictionary with the keys authorization_id, timestamp,
# merchant_id, billing_amount_chf and status, and with its cart under the key cart_lines where the cart is known.
# The row of the purchase being decided is dropped, so it never counts against itself.
# Rows after the purchase time are dropped, which is what every guard gets. Only the check of a customer's approval keeps them,
# because a purchase that was approved meanwhile may share a period with the one being approved.
# own_cart_lines is the cart of the purchase being decided, as a list of dictionaries with item_id, item_name and quantity.
# Without it no earlier purchase can be said to have held the same cart.
def build_ledger_snapshot(earlier_purchases, purchase_time, shop_identifier, own_identifier, keep_later_rows = False, own_cart_lines = None):
    assert isinstance(purchase_time, datetime) and purchase_time.utcoffset() is not None, "The purchase time must carry a time zone"
    rows_of_other_purchases = [
        row
        for row in earlier_purchases
        if own_identifier is None or row.get("authorization_id") != own_identifier
    ]
    row_readings = [
        read_ledger_row(row, purchase_time, shop_identifier, keep_later_rows, own_cart_lines)
        for row in rows_of_other_purchases
    ]
    entries = [row_reading.entry for row_reading in row_readings if row_reading.entry is not None]
    return LedgerSnapshot(
        purchase_time = purchase_time,
        entries = tuple(sorted(entries, key = lambda entry: entry.sim_time)),
        is_complete = all(row_reading.is_readable for row_reading in row_readings),
    )



# Read the cart of one decision record of the store, which its decision record keeps among its facts. A record without a decision record,
# such as one about a message that could not be read, has no cart.
def read_record_cart_lines(record):
    trace = record.get("trace")
    if not isinstance(trace, dict):
        return None
    facts = trace.get("facts")
    if not isinstance(facts, dict):
        return None
    return facts.get("cart_lines")



# Build the snapshot from the decision records of the store. Only the records of the same run and the same mandate count,
# where None equals None. Every live run replays the same simulated dates, so a record of another run must never count.
def build_ledger_snapshot_from_records(records, run_id, mandate_id, purchase_time, shop_identifier, own_identifier, keep_later_rows = False, own_cart_lines = None):
    earlier_purchases = [
        {
            "authorization_id": record.get("live_authorization_id"),
            "timestamp": record.get("sim_timestamp"),
            "merchant_id": record.get("merchant_id"),
            "billing_amount_chf": record.get("amount_chf"),
            "status": record.get("status"),
            "cart_lines": read_record_cart_lines(record),
        }
        for record in records
        if record.get("run_id") == run_id and record.get("mandate_id") == mandate_id
    ]
    return build_ledger_snapshot(earlier_purchases, purchase_time, shop_identifier, own_identifier, keep_later_rows, own_cart_lines)



# Read the cart of one purchase message in line order, in the shape the rows and the records carry a cart
def read_event_cart_lines(event):
    return [
        {
            "item_id": cart_item.item_id,
            "item_name": cart_item.item_name,
            "quantity": cart_item.quantity,
        }
        for cart_item in event.authorization.items
    ]



# Build the snapshot for one purchase message, which gives the time, the shop, the own identifier and the own cart
def build_ledger_snapshot_for_event(earlier_purchases, event, keep_later_rows = False):
    return build_ledger_snapshot(
        earlier_purchases = earlier_purchases,
        purchase_time = event.authorization.timestamp,
        shop_identifier = event.authorization.merchant.merchant_id,
        own_identifier = event.authorization.authorization_id,
        keep_later_rows = keep_later_rows,
        own_cart_lines = read_event_cart_lines(event),
    )



# Build the snapshot of a run without any earlier purchase, which is complete and empty
def build_empty_ledger_snapshot(event):
    return build_ledger_snapshot_for_event([], event)









#### Step 4: Sum the approved spend inside a period ####

# Describe the approved spend inside a period, with the earliest purchase that was counted, which is the next amount to leave the period
@dataclass(frozen = True)
class ApprovedSpend:
    total_chf: Decimal
    earliest_counted_entry: Optional[LedgerEntry]



# Sum the approved purchases inside one period, which ends at window_end and starts period_days days before it.
# Without a window end the period ends at the purchase being decided. A purchase at the end itself counts,
# and a purchase exactly period_days days before the end, to the second, has left the period.
# Without a number of days every approved purchase of the snapshot counts.
# A pending purchase never counts. The sum starts at an exact zero and adds exact decimals only.
def sum_approved_in_window(snapshot, period_days, window_end = None):
    if window_end is None:
        window_end = snapshot.purchase_time
    approved_entries = [entry for entry in snapshot.entries if entry.status == "approved"]
    if period_days is None:
        counted_entries = approved_entries
    else:
        window_start = window_end - timedelta(days = period_days)
        counted_entries = [entry for entry in approved_entries if window_start < entry.sim_time <= window_end]
    return ApprovedSpend(
        total_chf = sum((entry.amount_chf for entry in counted_entries), Decimal("0")),
        earliest_counted_entry = counted_entries[0] if counted_entries else None,
    )









#### Step 5: Check the budget again when a customer approves a question ####

# Find the stored result of one guard in a stored decision record, or None
def find_guard_entry(trace, guard_id):
    guard_entries = trace.get("guards") or []
    return next(
        (guard_entry for guard_entry in guard_entries if isinstance(guard_entry, dict) and guard_entry.get("guard_id") == guard_id),
        None,
    )



# Find one evidence item of a stored guard result by the name of its fact, or None
def find_evidence_item(guard_entry, fact_name):
    evidence_items = guard_entry.get("evidence") or []
    return next(
        (evidence_item for evidence_item in evidence_items if isinstance(evidence_item, dict) and evidence_item.get("fact") == fact_name),
        None,
    )



# Read a whole number of days from the evidence, where anything else means the budget runs over the whole mandate, the widest reading
def read_evidence_days(days_value):
    if isinstance(days_value, int) and not isinstance(days_value, bool) and days_value > 0:
        return days_value
    return None



# Name the period of a budget for the customer
def describe_budget_period(period_days):
    if period_days is None:
        return "under this instruction"
    if period_days == 1:
        return "over 1 day"
    return "over " + str(period_days) + " days"



# Work out the largest total of any period the questioned purchase falls into, with its own amount included.
# The purchase falls into the period that ends at its own time, and into the period that ends at every approved purchase
# later than it and less than period_days days after it. A purchase exactly period_days days later shares no period with it.
# This is what catches two open questions approved in reverse order, where the later one was approved first.
# Without a number of days there is one period, the whole mandate, and every approved purchase counts.
def find_largest_period_total(snapshot, period_days, amount_chf):
    if period_days is None:
        return sum_approved_in_window(snapshot, None).total_chf + amount_chf
    last_shared_moment = snapshot.purchase_time + timedelta(days = period_days)
    window_ends = [snapshot.purchase_time] + [
        entry.sim_time
        for entry in snapshot.entries
        if entry.status == "approved" and snapshot.purchase_time < entry.sim_time < last_shared_moment
    ]
    period_totals = [sum_approved_in_window(snapshot, period_days, window_end).total_chf + amount_chf for window_end in window_ends]
    return max(period_totals)



# Say why an approval cannot go through, or return None when nothing speaks against it.
# trace is the stored decision record of the questioned purchase. snapshot holds the run as it stands now, around the time of that purchase,
# and should keep the purchases after it, so an approval given meanwhile to a later purchase is seen.
# The question named a total. An approval of that total always goes through, also above the budget, because the customer was told.
# An approval is refused only when the total has grown since, which happens when the customer approved another open question meanwhile.
def find_budget_objection(trace, snapshot):
    if not isinstance(trace, dict):
        return None
    guard_entry = find_guard_entry(trace, PERIOD_BUDGET_GUARD_ID)
    if guard_entry is None or guard_entry.get("verdict") == "SKIP":
        return None



    # Read the budget the guard enforced, where a result without a limit means no budget could be read and nothing can be checked here
    total_evidence = find_evidence_item(guard_entry, "period_total_chf") or {}
    amount_evidence = find_evidence_item(guard_entry, "billing_amount_chf") or {}
    days_evidence = find_evidence_item(guard_entry, "period_days") or {}
    limit_chf = read_ledger_amount(total_evidence.get("threshold"))
    if limit_chf is None:
        return None
    limit_is_inclusive = total_evidence.get("comparator") != "<"
    named_total_chf = read_ledger_amount(total_evidence.get("value"))
    amount_chf = read_ledger_amount(amount_evidence.get("value"))
    period_days = read_evidence_days(days_evidence.get("value"))



    # Refuse when the memory of the run or the amount cannot be read, because a missing fact is never permission
    if not isinstance(snapshot, LedgerSnapshot) or not snapshot.is_complete or amount_chf is None:
        return (
            "Your earlier spending could not be read completely, so this order cannot be checked against your budget of "
            + describe_francs(limit_chf) + ". Nothing was approved."
        )



    # Work the total out again, and object only when it breaks the budget and is larger than the total the question named.
    # A question that named no total, because the memory was missing when it was asked, told the customer nothing about the budget.
    new_total_chf = find_largest_period_total(snapshot, period_days, amount_chf)
    total_is_over = new_total_chf > limit_chf or (new_total_chf == limit_chf and not limit_is_inclusive)
    total_has_grown = named_total_chf is None or new_total_chf > named_total_chf
    if not (total_is_over and total_has_grown):
        return None
    if named_total_chf is None:
        opening_sentence = "When this question was asked your earlier spending could not be read."
    else:
        opening_sentence = "Since this question was asked you approved another order."
    if new_total_chf == limit_chf:
        return (
            opening_sentence + " This one would now bring your spending " + describe_budget_period(period_days) + " to exactly your budget of "
            + describe_francs(limit_chf) + ", and your instruction asked to stay under it."
        )
    return (
        opening_sentence + " This one would now bring your spending " + describe_budget_period(period_days) + " to "
        + describe_francs(new_total_chf) + ", above your budget of " + describe_francs(limit_chf) + "."
    )
