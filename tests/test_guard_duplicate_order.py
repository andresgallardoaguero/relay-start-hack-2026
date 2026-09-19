# Script: test_guard_duplicate_order.py
# Purpose: Check that an order that repeats an approved order, with the same cart at the same shop for nearly the same amount shortly after, is put to the customer, with exact edges for the hours and for the amount, and that nothing else is
# Author: Andrés Gallardo
# Date: September 2026

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_duplicate_order import DuplicateOrderGuard, describe_time_ago
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.state.ledger import build_ledger_snapshot_for_event









#### Step 1: Define the shared helpers ####

# Locate the source file that must never name an identifier
GUARD_SOURCE_PATH = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine" / "guards" / "g_duplicate_order.py"
FORBIDDEN_WORDS = ("scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "card_id", "customer_id", "item_id", "item_details")



# Build the guard as the registry does, from its number, its id and its family
DUPLICATE_ORDER_GUARD = DuplicateOrderGuard(
    guard_number = 15,
    guard_id = "duplicate_order",
    family = GuardFamily.REPEATS_AND_MANIPULATION,
)



# State the simulated time of the purchase being decided, the name of another shop and a cart that differs from the cart of the example message
PURCHASE_TIME = datetime(2026, 8, 12, 10, 5, 0, tzinfo = timezone.utc)
ANOTHER_SHOP = "SHOP_ANOTHER"
ANOTHER_CART = [{"item_id": "ITEM_ANOTHER", "item_name": "Another item", "quantity": 1}]



# Write a moment as ISO text with a trailing Z, as the messages write it
def format_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")



# Copy the example message with another amount in Swiss francs, the purchase time of the tests and the status of a related order,
# and read it through the strict reader
def build_event(example_message, billing_amount_chf, related_status = None):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["billing_amount_chf"] = billing_amount_chf
    changed_message["authorization"]["amount"] = billing_amount_chf
    changed_message["authorization"]["timestamp"] = format_time(PURCHASE_TIME)
    changed_message["authorization"]["related_authorization_id"] = None if related_status is None else "LA_RELATED"
    changed_message["authorization"]["related_authorization_status"] = related_status
    return read_purchase_message(changed_message)



# Read the cart of the example message in the shape the memory of a run keeps a cart
def read_example_cart(example_message):
    return [
        {"item_id": cart_line["item_id"], "item_name": cart_line["item_name"], "quantity": cart_line["quantity"]}
        for cart_line in example_message["authorization"]["items"]
    ]



# Build one earlier order of the run, at the shop and with the cart of the example message unless another shop or another cart is handed in
def build_row(example_message, time_before_purchase, amount, status = "approved", shop = None, cart_lines = None):
    return {
        "authorization_id": "EARLIER_" + str(time_before_purchase.total_seconds()) + "_" + str(amount),
        "timestamp": format_time(PURCHASE_TIME - time_before_purchase),
        "merchant_id": example_message["authorization"]["merchant"]["merchant_id"] if shop is None else shop,
        "billing_amount_chf": amount,
        "status": status,
        "cart_lines": read_example_cart(example_message) if cart_lines is None else cart_lines,
    }



# Build a policy by hand, without the compiler, which states no limit and no budget, so the check for repeated orders is the only guard with something to say
def build_policy(window_hours = 48, amount_share = "0.10", uncertainty_policy = "ask"):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = uncertainty_policy,
        expectations = Expectations(
            duplicate_window_hours = window_hours,
            duplicate_amount_share = Decimal(amount_share),
        ),
        open_questions = (),
    )



# Let the guard alone judge one purchase against the earlier orders of its run
def check_purchase(event, policy, rows):
    snapshot = build_ledger_snapshot_for_event(rows, event)
    decision_input = DecisionInput(event = event, policy = policy, facts = build_fact_sheet(event), state = snapshot)
    return DUPLICATE_ORDER_GUARD.check(decision_input, {})



# Find one evidence item of a result by the name of its fact
def find_evidence(guard_result, fact_name):
    return next(evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name)









#### Step 2: Check the repeated order ####

# Check the same cart at the same shop for the same amount 25 minutes later, with the question word for word
def test_same_cart_at_the_same_shop_for_the_same_amount_25_minutes_later_asks(example_message):
    event = build_event(example_message, 289.00)
    guard_result = check_purchase(event, build_policy(), [build_row(example_message, timedelta(minutes = 25), 289.00)])
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert guard_result.reason_code == ReasonCode.DUPLICATE_SUSPECTED
    assert guard_result.customer_message == (
        "This looks like the same order again. You bought the same items at this shop 25 minutes ago for CHF 289.00. Approve it a second time?"
    )
    assert [(evidence_item.fact, evidence_item.value, evidence_item.comparator, evidence_item.threshold) for evidence_item in guard_result.evidence] == [
        ("repeated_orders_in_window", 1, "=", 0),
        ("duplicate_window_hours", 48, None, None),
        ("duplicate_amount_share", 0.1, None, None),
        ("billing_amount_chf", 289.0, None, None),
    ]
    assert guard_result.note is None
    assert guard_result.signal is None
    assert guard_result.guard_number == 15
    assert guard_result.guard_id == "duplicate_order"
    assert guard_result.family == GuardFamily.REPEATS_AND_MANIPULATION



# Check that a run without any earlier order passes, with the same four evidence items
def test_first_order_of_a_run_passes(example_message):
    guard_result = check_purchase(build_event(example_message, 289.00), build_policy(), [])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.customer_message is None
    assert find_evidence(guard_result, "repeated_orders_in_window").value == 0
    assert len(guard_result.evidence) == 4



# Check that the same order at another shop and another cart at the same shop pass
def test_another_shop_and_another_cart_pass(example_message):
    event = build_event(example_message, 289.00)
    at_another_shop = check_purchase(event, build_policy(), [build_row(example_message, timedelta(minutes = 25), 289.00, shop = ANOTHER_SHOP)])
    with_another_cart = check_purchase(event, build_policy(), [build_row(example_message, timedelta(minutes = 25), 289.00, cart_lines = ANOTHER_CART)])
    assert at_another_shop.verdict == GuardVerdict.PASS
    assert with_another_cart.verdict == GuardVerdict.PASS
    assert find_evidence(at_another_shop, "repeated_orders_in_window").value == 0
    assert find_evidence(with_another_cart, "repeated_orders_in_window").value == 0



# Check that an earlier order whose cart is not known passes, because a cart that is not known is never the same cart
def test_earlier_order_without_a_known_cart_passes(example_message):
    event = build_event(example_message, 289.00)
    row_without_a_cart = {**build_row(example_message, timedelta(minutes = 25), 289.00), "cart_lines": None}
    assert check_purchase(event, build_policy(), [row_without_a_cart]).verdict == GuardVerdict.PASS









#### Step 3: Check the exact edges ####

# Check that 10 percent more than the earlier amount asks and one cent beyond passes, and the same 10 percent below
def test_amount_has_exact_edges(example_message):
    rows = [build_row(example_message, timedelta(minutes = 25), 100.00)]
    assert check_purchase(build_event(example_message, 110.00), build_policy(), rows).verdict == GuardVerdict.STEP_UP
    assert check_purchase(build_event(example_message, 110.01), build_policy(), rows).verdict == GuardVerdict.PASS
    assert check_purchase(build_event(example_message, 90.00), build_policy(), rows).verdict == GuardVerdict.STEP_UP
    assert check_purchase(build_event(example_message, 89.99), build_policy(), rows).verdict == GuardVerdict.PASS



# Check that exactly 48 hours apart still asks and 48 hours and one second passes
def test_hours_have_an_exact_edge(example_message):
    event = build_event(example_message, 289.00)
    result_at_the_edge = check_purchase(event, build_policy(), [build_row(example_message, timedelta(hours = 48), 289.00)])
    result_past_the_edge = check_purchase(event, build_policy(), [build_row(example_message, timedelta(hours = 48, seconds = 1), 289.00)])
    assert result_at_the_edge.verdict == GuardVerdict.STEP_UP
    assert "2 days ago" in result_at_the_edge.customer_message
    assert result_past_the_edge.verdict == GuardVerdict.PASS



# Check that the hours and the share come from the policy, so a shorter setting and a smaller share let the same orders pass
def test_hours_and_share_come_from_the_policy(example_message):
    rows = [build_row(example_message, timedelta(hours = 30), 100.00)]
    event = build_event(example_message, 105.00)
    assert check_purchase(event, build_policy(window_hours = 48), rows).verdict == GuardVerdict.STEP_UP
    assert check_purchase(event, build_policy(window_hours = 24), rows).verdict == GuardVerdict.PASS
    assert check_purchase(event, build_policy(amount_share = "0.04"), rows).verdict == GuardVerdict.PASS
    assert check_purchase(event, build_policy(amount_share = "0.05"), rows).verdict == GuardVerdict.STEP_UP



# Check that the time is written in minutes below two hours, in hours below two days and in days from there on
@pytest.mark.parametrize(
    "time_since_earlier_order, expected_text",
    [
        (timedelta(seconds = 40), "less than a minute ago"),
        (timedelta(minutes = 1), "1 minute ago"),
        (timedelta(minutes = 25), "25 minutes ago"),
        (timedelta(minutes = 119, seconds = 59), "119 minutes ago"),
        (timedelta(hours = 2), "2 hours ago"),
        (timedelta(hours = 30, minutes = 15), "30 hours ago"),
        (timedelta(hours = 47, minutes = 59), "47 hours ago"),
        (timedelta(hours = 48), "2 days ago"),
    ],
)
def test_time_ago_is_written_in_minutes_hours_and_days(time_since_earlier_order, expected_text):
    assert describe_time_ago(time_since_earlier_order) == expected_text









#### Step 4: Check which earlier orders count ####

# Check that only an approved earlier order counts, because only an order the customer already has can be bought twice
@pytest.mark.parametrize(
    "status, expected_verdict",
    [
        ("approved", GuardVerdict.STEP_UP),
        ("pending", GuardVerdict.PASS),
        ("declined", GuardVerdict.PASS),
        ("expired", GuardVerdict.PASS),
        ("cancelled", GuardVerdict.PASS),
    ],
)
def test_status_of_the_earlier_order_decides_whether_it_counts(example_message, status, expected_verdict):
    event = build_event(example_message, 289.00)
    guard_result = check_purchase(event, build_policy(), [build_row(example_message, timedelta(minutes = 25), 289.00, status = status)])
    assert guard_result.verdict == expected_verdict



# Check that the same cart for the same amount shortly after an order that is still an open question passes here,
# because nothing was bought yet, and the check for an order split in two asks about such a pair under a limit per order
def test_same_cart_for_the_same_amount_after_a_pending_order_passes(example_message):
    event = build_event(example_message, 65.00)
    guard_result = check_purchase(event, build_policy(), [build_row(example_message, timedelta(minutes = 6), 65.00, status = "pending")])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert find_evidence(guard_result, "repeated_orders_in_window").value == 0



# Check that the question names the latest of several repeated orders and the evidence counts them all
def test_question_names_the_latest_repeated_order(example_message):
    event = build_event(example_message, 289.00)
    rows = [
        build_row(example_message, timedelta(hours = 5), 280.00),
        build_row(example_message, timedelta(minutes = 90), 295.00),
    ]
    guard_result = check_purchase(event, build_policy(), rows)
    assert guard_result.verdict == GuardVerdict.STEP_UP
    assert find_evidence(guard_result, "repeated_orders_in_window").value == 2
    assert "90 minutes ago for CHF 295.00" in guard_result.customer_message



# Check that the guard never declines, however many times the order was already bought
def test_guard_never_declines(example_message):
    event = build_event(example_message, 289.00)
    rows = [build_row(example_message, timedelta(minutes = position), 289.00) for position in range(1, 8)]
    assert check_purchase(event, build_policy(), rows).verdict == GuardVerdict.STEP_UP









#### Step 5: Check the related order ####

# Check that a new attempt after a declined or a cancelled order passes with its informative reason and compares nothing,
# also when an approved repeat exists
@pytest.mark.parametrize("related_status", ["declined", "cancelled"])
def test_related_order_that_was_refused_passes_as_a_new_attempt(example_message, related_status):
    event = build_event(example_message, 289.00, related_status = related_status)
    guard_result = check_purchase(event, build_policy(), [build_row(example_message, timedelta(minutes = 25), 289.00)])
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code == ReasonCode.RE_QUOTE_COMPLIANT
    assert guard_result.customer_message is None
    assert [(evidence_item.fact, evidence_item.value) for evidence_item in guard_result.evidence] == [("related_order_status", related_status)]



# Check that a related order that was approved or is still open is compared like any other order
@pytest.mark.parametrize("related_status", ["approved", "pending"])
def test_related_order_that_was_not_refused_is_compared_like_any_other(example_message, related_status):
    event = build_event(example_message, 289.00, related_status = related_status)
    result_with_a_repeat = check_purchase(event, build_policy(), [build_row(example_message, timedelta(minutes = 25), 289.00)])
    result_without_a_repeat = check_purchase(event, build_policy(), [])
    assert result_with_a_repeat.verdict == GuardVerdict.STEP_UP
    assert result_with_a_repeat.reason_code == ReasonCode.DUPLICATE_SUSPECTED
    assert result_without_a_repeat.verdict == GuardVerdict.PASS
    assert result_without_a_repeat.reason_code is None



# Check that the informative reason of a new attempt stays out of the reasons of the decision, which is an approval
def test_new_attempt_is_approved_through_the_pipeline(example_message):
    event = build_event(example_message, 289.00, related_status = "declined")
    rows = [build_row(example_message, timedelta(minutes = 25), 289.00)]
    decision_trace = decide(event, build_policy(), build_ledger_snapshot_for_event(rows, event))
    assert decision_trace.decision == Decision.APPROVE
    assert decision_trace.reason_codes == []









#### Step 6: Check the missing memory and the whole pipeline ####

# Check that the guard never judges without a memory of the run, with something that is no memory, and with an incomplete memory,
# also for a new attempt after a refusal
@pytest.mark.parametrize("related_status", [None, "declined"])
def test_missing_memory_gives_uncertain(example_message, related_status):
    event = build_event(example_message, 289.00, related_status = related_status)
    incomplete_snapshot = build_ledger_snapshot_for_event(
        [{"authorization_id": "BROKEN", "timestamp": None, "merchant_id": "SHOP", "billing_amount_chf": 70.0, "status": "approved"}],
        event,
    )
    assert incomplete_snapshot.is_complete is False
    for unusable_state in (None, [], incomplete_snapshot):
        decision_input = DecisionInput(event = event, policy = build_policy(), facts = build_fact_sheet(event), state = unusable_state)
        guard_result = DUPLICATE_ORDER_GUARD.check(decision_input, {})
        assert guard_result.verdict == GuardVerdict.UNCERTAIN
        assert guard_result.reason_code == ReasonCode.LEDGER_UNAVAILABLE
        assert guard_result.customer_message.endswith("Approve it?")



# Check that a missing memory asks under ask, declines under decline and still asks under approve, so it never approves
@pytest.mark.parametrize(
    "uncertainty_policy, expected_decision",
    [("ask", Decision.STEP_UP), ("decline", Decision.DECLINE), ("approve", Decision.STEP_UP)],
)
def test_missing_memory_never_approves(example_message, uncertainty_policy, expected_decision):
    event = build_event(example_message, 289.00)
    decision_trace = decide(event, build_policy(uncertainty_policy = uncertainty_policy), None)
    assert decision_trace.decision == expected_decision
    assert decision_trace.reason_codes == [ReasonCode.LEDGER_UNAVAILABLE]
    assert decision_trace.aggregation.raised_by == ["duplicate_order"]



# Check that a repeated order asks through the whole pipeline, raised by this guard alone
def test_repeated_order_asks_through_the_pipeline(example_message):
    event = build_event(example_message, 289.00)
    rows = [build_row(example_message, timedelta(minutes = 25), 289.00)]
    decision_trace = decide(event, build_policy(), build_ledger_snapshot_for_event(rows, event))
    assert decision_trace.decision == Decision.STEP_UP
    assert decision_trace.reason_codes == [ReasonCode.DUPLICATE_SUSPECTED]
    assert decision_trace.aggregation.raised_by == ["duplicate_order"]
    assert decision_trace.customer_message.startswith("This looks like the same order again.")









#### Step 7: Check that the guard never names an identifier ####

# Check the source text of the guard for the words that would tie a decision to a test case or to an identifier
def test_source_names_no_identifier():
    source_text = GUARD_SOURCE_PATH.read_text(encoding = "utf-8").lower()
    words_found = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text]
    assert words_found == []
