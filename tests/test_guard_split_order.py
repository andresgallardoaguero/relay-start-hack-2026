# Script: test_guard_split_order.py
# Purpose: Check that an order split in two at the same shop is put to the customer, with exact edges for the minutes and for the combined amount, and that nothing else is
# Author: Andrés Gallardo
# Date: September 2026

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_split_order import SplitOrderGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.state.ledger import build_ledger_snapshot_for_event









#### Step 1: Define the shared helpers ####

# Locate the source file that must never name an identifier
GUARD_SOURCE_PATH = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine" / "guards" / "g_split_order.py"
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
SPLIT_ORDER_GUARD = SplitOrderGuard(
    guard_number = 5,
    guard_id = "split_order",
    family = GuardFamily.SPENDING_LIMITS,
)



# State the simulated time of the purchase being decided and the name of another shop
PURCHASE_TIME = datetime(2026, 8, 13, 17, 26, 0, tzinfo = timezone.utc)
ANOTHER_SHOP = "SHOP_ANOTHER"



# Write a moment as ISO text with a trailing Z, as the messages write it
def format_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")



# Copy the example message with another amount in Swiss francs and the purchase time of the tests, and read it through the strict reader
def build_event(example_message, billing_amount_chf):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["billing_amount_chf"] = billing_amount_chf
    changed_message["authorization"]["amount"] = billing_amount_chf
    changed_message["authorization"]["timestamp"] = format_time(PURCHASE_TIME)
    return read_purchase_message(changed_message)



# Build one earlier order of the run, at the shop of the example message unless another shop is handed in
def build_row(example_message, time_before_purchase, amount, status = "approved", shop = None):
    return {
        "authorization_id": "EARLIER_" + str(time_before_purchase.total_seconds()) + "_" + str(amount),
        "timestamp": format_time(PURCHASE_TIME - time_before_purchase),
        "merchant_id": example_message["authorization"]["merchant"]["merchant_id"] if shop is None else shop,
        "billing_amount_chf": amount,
        "status": status,
    }



# Build a policy by hand, without the compiler, where a limit of None leaves the limit per order empty
def build_policy(limit_text, inclusive = True, window_minutes = 120):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(
            per_order_limit_chf = None if limit_text is None else Decimal(limit_text),
            per_order_limit_inclusive = inclusive,
            per_order_limit_reading = "not_stated" if limit_text is None else "read",
            split_order_window_minutes = window_minutes,
        ),
        open_questions = (),
    )



# Let the guard alone judge one purchase against the earlier orders of its run
def check_purchase(event, policy, rows):
    snapshot = build_ledger_snapshot_for_event(rows, event)
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = snapshot)
    return SPLIT_ORDER_GUARD.check(decision_input, {})



# Find one evidence item of a result by the name of its fact
def find_evidence(guard_result, fact_name):
    return next(evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name)









#### Step 2: Check the split order and the other shop ####

# Check CHF 70.00 and then CHF 65.00 six minutes later at the same shop under a limit of CHF 120, with the question word for word
def test_two_orders_six_minutes_apart_at_the_same_shop_ask(example_message):
    event = build_event(example_message, 65.00)
    guard_result = check_purchase(event, build_policy("120"), [build_row(example_message, timedelta(minutes = 6), 70.00)])
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.reason_code == ReasonCode.SPLIT_ORDER_SUSPECTED
    assert guard_result.customer_message == (
        "CHF 65.00, 6 minutes after CHF 70.00 at the same shop. Together they come to CHF 135.00, above your limit of CHF 120.00 per order. "
        "This looks like one order split in two. Approve it?"
    )
    assert [(evidence_item.fact, evidence_item.value, evidence_item.comparator, evidence_item.threshold) for evidence_item in guard_result.evidence] == [
        ("combined_same_shop_chf", 135.0, "<=", 120.0),
        ("same_shop_orders_in_window", 1, None, None),
        ("split_order_window_minutes", 120, None, None),
        ("billing_amount_chf", 65.0, None, None),
    ]
    assert guard_result.guard_number == 5
    assert guard_result.guard_id == "split_order"
    assert guard_result.family == GuardFamily.SPENDING_LIMITS



# Check that the same two orders at different shops pass
def test_same_orders_at_another_shop_pass(example_message):
    event = build_event(example_message, 65.00)
    guard_result = check_purchase(event, build_policy("120"), [build_row(example_message, timedelta(minutes = 6), 70.00, shop = ANOTHER_SHOP)])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.customer_message is None
    assert find_evidence(guard_result, "same_shop_orders_in_window").value == 0



# Check that a run without any earlier order passes
def test_first_order_of_a_run_passes(example_message):
    guard_result = check_purchase(build_event(example_message, 65.00), build_policy("120"), [])
    assert guard_result.verdict == GuardVerdict.PASS
    assert find_evidence(guard_result, "combined_same_shop_chf").value == 65.0









#### Step 3: Check the exact edges ####

# Check that exactly 120 minutes apart still asks and 120 minutes and one second passes
def test_minutes_have_an_exact_edge(example_message):
    event = build_event(example_message, 65.00)
    result_at_the_edge = check_purchase(event, build_policy("120"), [build_row(example_message, timedelta(minutes = 120), 70.00)])
    result_past_the_edge = check_purchase(event, build_policy("120"), [build_row(example_message, timedelta(minutes = 120, seconds = 1), 70.00)])
    assert result_at_the_edge.verdict == GuardVerdict.STEP_UP
    assert "120 minutes after" in result_at_the_edge.customer_message
    assert result_past_the_edge.verdict == GuardVerdict.PASS



# Check that the minutes come from the policy, so a shorter setting lets the same orders pass
def test_minutes_come_from_the_policy(example_message):
    event = build_event(example_message, 65.00)
    rows = [build_row(example_message, timedelta(minutes = 45), 70.00)]
    assert check_purchase(event, build_policy("120", window_minutes = 120), rows).verdict == GuardVerdict.STEP_UP
    assert check_purchase(event, build_policy("120", window_minutes = 30), rows).verdict == GuardVerdict.PASS



# Check that a combined total of exactly CHF 120.00 passes under an inclusive limit and asks under an exclusive one
def test_combined_total_equal_to_the_limit(example_message):
    event = build_event(example_message, 50.00)
    rows = [build_row(example_message, timedelta(minutes = 6), 70.00)]
    inclusive_result = check_purchase(event, build_policy("120", inclusive = True), rows)
    exclusive_result = check_purchase(event, build_policy("120", inclusive = False), rows)
    assert inclusive_result.verdict == GuardVerdict.PASS
    assert exclusive_result.verdict == GuardVerdict.STEP_UP
    assert find_evidence(exclusive_result, "combined_same_shop_chf").comparator == "<"
    assert "Together they come to exactly your limit of CHF 120.00 per order, and your instruction asked to stay under it." in exclusive_result.customer_message



# Check that one cent above the limit asks, with amounts that carry binary noise as floats
def test_one_cent_above_the_limit_asks(example_message):
    event = build_event(example_message, 0.2)
    assert check_purchase(event, build_policy("0.30"), [build_row(example_message, timedelta(minutes = 6), 0.1)]).verdict == GuardVerdict.PASS
    assert check_purchase(event, build_policy("0.29"), [build_row(example_message, timedelta(minutes = 6), 0.1)]).verdict == GuardVerdict.STEP_UP









#### Step 4: Check which earlier orders count ####

# Check that an earlier order that was declined, expired or cancelled never counts, and that an open question does
@pytest.mark.parametrize(
    "status, expected_verdict",
    [
        ("approved", GuardVerdict.STEP_UP),
        ("pending", GuardVerdict.STEP_UP),
        ("declined", GuardVerdict.PASS),
        ("expired", GuardVerdict.PASS),
        ("cancelled", GuardVerdict.PASS),
    ],
)
def test_status_of_the_earlier_order_decides_whether_it_counts(example_message, status, expected_verdict):
    event = build_event(example_message, 65.00)
    guard_result = check_purchase(event, build_policy("120"), [build_row(example_message, timedelta(minutes = 6), 70.00, status = status)])
    assert guard_result.verdict == expected_verdict



# Check three orders of CHF 50.00 inside the minutes, where the second passes and the third asks
def test_three_orders_ask_on_the_third(example_message):
    event = build_event(example_message, 50.00)
    first_row = build_row(example_message, timedelta(minutes = 20), 50.00)
    second_row = build_row(example_message, timedelta(minutes = 10), 50.00)
    result_of_the_second = check_purchase(event, build_policy("120"), [first_row])
    result_of_the_third = check_purchase(event, build_policy("120"), [first_row, second_row])
    assert result_of_the_second.verdict == GuardVerdict.PASS
    assert result_of_the_third.verdict == GuardVerdict.STEP_UP
    assert find_evidence(result_of_the_third, "combined_same_shop_chf").value == 150.0
    assert find_evidence(result_of_the_third, "same_shop_orders_in_window").value == 2
    assert result_of_the_third.customer_message == (
        "CHF 50.00, 10 minutes after 2 other orders of CHF 100.00 in total at the same shop. "
        "Together they come to CHF 150.00, above your limit of CHF 120.00 per order. This looks like one order split into several. Approve it?"
    )



# Check that the question speaks of two with one earlier order and of several with more than one
def test_question_says_two_or_several(example_message):
    event = build_event(example_message, 65.00)
    one_earlier_order = [build_row(example_message, timedelta(minutes = 6), 70.00)]
    two_earlier_orders = [build_row(example_message, timedelta(minutes = 12), 30.00), build_row(example_message, timedelta(minutes = 6), 40.00)]
    message_with_one = check_purchase(event, build_policy("120"), one_earlier_order).customer_message
    message_with_two = check_purchase(event, build_policy("120"), two_earlier_orders).customer_message
    assert "This looks like one order split in two. Approve it?" in message_with_one
    assert "split into several" not in message_with_one
    assert "This looks like one order split into several. Approve it?" in message_with_two
    assert "split in two" not in message_with_two



# Check that an open question that cannot be read is left out and does not stop the guard from judging
def test_unreadable_pending_order_does_not_make_the_memory_unusable(example_message):
    event = build_event(example_message, 65.00)
    rows = [{"authorization_id": "BROKEN", "timestamp": None, "merchant_id": "SHOP", "billing_amount_chf": None, "status": "pending"}]
    assert check_purchase(event, build_policy("120"), rows).verdict == GuardVerdict.PASS



# Check that the guard never declines, however far the combined amount is above the limit
def test_guard_never_declines(example_message):
    event = build_event(example_message, 119.00)
    rows = [build_row(example_message, timedelta(minutes = position), 119.00) for position in range(1, 6)]
    assert check_purchase(event, build_policy("120"), rows).verdict == GuardVerdict.STEP_UP



# Check that an order over the limit on its own passes here, and carries the small overshoot from the limit per order alone
def test_order_over_the_limit_on_its_own_is_left_to_the_limit_per_order(example_message):
    event = build_event(example_message, 21.50)
    rows = [build_row(example_message, timedelta(minutes = 6), 20.00)]
    guard_result = check_purchase(event, build_policy("20"), rows)
    decision_trace = decide(event, build_policy("20"), build_ledger_snapshot_for_event(rows, event))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.SMALL_OVERSHOOT]
    assert decision_trace.aggregation.raised_by == ["per_order_limit"]



# Build one earlier order that held exactly the cart of the example message
def build_row_with_the_same_cart(example_message, time_before_purchase, amount, status = "approved"):
    cart_lines = [
        {"item_id": cart_line["item_id"], "item_name": cart_line["item_name"], "quantity": cart_line["quantity"]}
        for cart_line in example_message["authorization"]["items"]
    ]
    return {**build_row(example_message, time_before_purchase, amount, status = status), "cart_lines": cart_lines}



# Check that the same cart for the same amount shortly after an order that is still an open question asks here,
# because the check for repeated orders counts approved orders only, and an identical second order must not pass both checks
def test_same_cart_for_the_same_amount_after_a_pending_order_asks(example_message):
    event = build_event(example_message, 65.00)
    rows = [build_row_with_the_same_cart(example_message, timedelta(minutes = 6), 65.00, status = "pending")]
    guard_result = check_purchase(event, build_policy("120"), rows)
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.reason_code == ReasonCode.SPLIT_ORDER_SUSPECTED
    assert find_evidence(guard_result, "same_shop_orders_in_window").value == 1
    assert find_evidence(guard_result, "combined_same_shop_chf").value == 130.0



# Check that the same cart for the same amount shortly after passes here, because the same order twice is a repeat and no half of a split order
def test_same_cart_for_the_same_amount_is_left_to_the_check_for_repeated_orders(example_message):
    event = build_event(example_message, 65.00)
    rows = [build_row_with_the_same_cart(example_message, timedelta(minutes = 6), 65.00)]
    guard_result = check_purchase(event, build_policy("120"), rows)
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert find_evidence(guard_result, "same_shop_orders_in_window").value == 0
    assert find_evidence(guard_result, "combined_same_shop_chf").value == 65.0



# Check that the same cart for a clearly different amount, more than the share of the earlier amount apart, still counts as a half,
# with the edge at exactly the share, where CHF 72.00 against CHF 80.00 is a repeat and CHF 71.99 is a half
def test_same_cart_for_a_clearly_different_amount_still_counts_as_a_half(example_message):
    rows = [build_row_with_the_same_cart(example_message, timedelta(minutes = 6), 80.00)]
    result_far_apart = check_purchase(build_event(example_message, 50.00), build_policy("120"), rows)
    result_at_the_share = check_purchase(build_event(example_message, 72.00), build_policy("120"), rows)
    result_past_the_share = check_purchase(build_event(example_message, 71.99), build_policy("120"), rows)
    assert result_far_apart.verdict == GuardVerdict.STEP_UP
    assert result_far_apart.reason_code == ReasonCode.SPLIT_ORDER_SUSPECTED
    assert find_evidence(result_far_apart, "combined_same_shop_chf").value == 130.0
    assert result_at_the_share.verdict == GuardVerdict.PASS
    assert result_past_the_share.verdict == GuardVerdict.STEP_UP









#### Step 5: Check the missing limit and the missing memory ####

# Check that an instruction without a limit per order skips the guard, also without any memory of the run
def test_no_limit_gives_skip(example_message):
    event = build_event(example_message, 65.00)
    decision_input = DecisionInput(event = event, policy = build_policy(None), facts = build_fact_sheet(event), state = None)
    guard_result = SPLIT_ORDER_GUARD.check(decision_input, {})
    assert guard_result.verdict == GuardVerdict.SKIP
    assert guard_result.reason_code is None



# Check that a limit without a memory of the run, with something that is no memory, and with an incomplete memory is never judged
def test_limit_without_a_usable_memory_gives_uncertain(example_message):
    event = build_event(example_message, 65.00)
    incomplete_snapshot = build_ledger_snapshot_for_event(
        [{"authorization_id": "BROKEN", "timestamp": None, "merchant_id": "SHOP", "billing_amount_chf": 70.0, "status": "approved"}],
        event,
    )
    assert incomplete_snapshot.is_complete is False
    for unusable_state in (None, [], incomplete_snapshot):
        decision_input = DecisionInput(event = event, policy = build_policy("120"), facts = build_fact_sheet(event), state = unusable_state)
        guard_result = SPLIT_ORDER_GUARD.check(decision_input, {})
        assert guard_result.verdict == GuardVerdict.UNCERTAIN
        assert guard_result.reason_code == ReasonCode.LEDGER_UNAVAILABLE
        assert guard_result.customer_message.endswith("Approve it?")



# Check that a missing memory under a limit per order asks through the whole pipeline and names the reason once,
# raised by this guard and by the check for repeated orders, which needs the memory under every instruction
def test_missing_memory_asks_through_the_pipeline(example_message):
    event = build_event(example_message, 65.00)
    decision_trace = decide(event, build_policy("120"), None)
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.LEDGER_UNAVAILABLE]
    assert decision_trace.aggregation.raised_by == ["split_order", "duplicate_order"]









#### Step 6: Check that the guard never names an identifier ####

# Check the source text of the guard for the words that would tie a decision to a test case or to an identifier
def test_source_names_no_identifier():
    source_text = GUARD_SOURCE_PATH.read_text(encoding = "utf-8").lower()
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text]
    assert words_found == []
