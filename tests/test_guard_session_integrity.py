# Script: test_guard_session_integrity.py
# Purpose: Check that the session guard counts distinct signals against the numbers of the policy, asks at two, declines at three and lets one strong signal ask only under a watched session
# Author: Andrés Gallardo
# Date: September 2026

import copy

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_session_integrity import SessionIntegrityGuard
from app.engine.pipeline import decide
from app.models.decision import Decision, GuardFamily, GuardResult, GuardSignal, GuardVerdict, ReasonCode
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.policyc.compiler import build_policy_from_mandate
from app.state.familiarity import FamiliarityFacts
from app.state.ledger import build_empty_ledger_snapshot
from app.state.session import SessionFacts









#### Step 1: Define the shared helpers ####

# Build the guard as the registry does, from its number, its id and its family
SESSION_INTEGRITY_GUARD = SessionIntegrityGuard(guard_number = 14, guard_id = "session_integrity", family = GuardFamily.SESSION)



# Name the signals the earlier guards give, each with the guard that gives it
NEW_DEVICE = ("device", 11, GuardSignal(name = "new_device", strength = "strong"))
QUICK_SERIES = ("velocity", 12, GuardSignal(name = "quick_series", strength = "normal"))
NEW_COUNTRY = ("geo", 13, GuardSignal(name = "new_country", strength = "normal"))
UNFAMILIAR_MERCHANT = ("merchant_familiarity", 10, GuardSignal(name = "unfamiliar_merchant", strength = "normal"))



# State the two instructions of the tests through the whole engine, which differ only in the sentence that asks to watch the session
PLAIN_INSTRUCTION = "Buy one grocery item for CHF 20 or less."
WATCHING_INSTRUCTION = PLAIN_INSTRUCTION + " Pause anything that looks like someone other than me is driving the session."



# Build a policy by hand, without the compiler
def build_policy(session_sensitivity = "normal", ask_signal_count = 2, decline_signal_count = 3, hour_min_history_purchases = 20):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(
            session_sensitivity = session_sensitivity,
            session_ask_signal_count = ask_signal_count,
            session_decline_signal_count = decline_signal_count,
            hour_min_history_purchases = hour_min_history_purchases,
        ),
        open_questions = (),
    )



# Build the session facts of a known card by hand, at an evening hour the card uses unless a test hands in another hour
def build_session(local_hour = 20, local_time_text = "20.20", hour_purchase_count = 14, history_purchase_count = 139, is_recurring_channel = False, card_is_known = True):
    return SessionFacts(
        card_is_known = card_is_known,
        history_purchase_count = history_purchase_count,
        device_purchase_count = 44,
        local_hour = local_hour,
        local_time_text = local_time_text,
        hour_purchase_count = hour_purchase_count,
        shop_country = "CH",
        country_purchase_count = 117,
        recent_attempt_count = 0,
        is_recurring_channel = is_recurring_channel,
    )



# Build the session facts of a night purchase at 04.14, an hour without any purchase unless a test hands in a count
def build_night_session(hour_purchase_count = 0, history_purchase_count = 139, is_recurring_channel = False):
    return build_session(
        local_hour = 4,
        local_time_text = "04.14",
        hour_purchase_count = hour_purchase_count,
        history_purchase_count = history_purchase_count,
        is_recurring_channel = is_recurring_channel,
    )



# Build the result of an earlier guard that passed, with or without a signal
def build_earlier_result(guard_id, guard_number, signal = None, verdict = GuardVerdict.PASS):
    return GuardResult(guard_number = guard_number, guard_id = guard_id, family = GuardFamily.SESSION, verdict = verdict, signal = signal)



# Build the earlier results from the named signals, where the guards that are not named ran and noticed nothing
def build_earlier_results(*named_signals):
    results_without_signal = {
        guard_id: build_earlier_result(guard_id, guard_number)
        for guard_id, guard_number, signal in (NEW_DEVICE, QUICK_SERIES, NEW_COUNTRY, UNFAMILIAR_MERCHANT)
    }
    results_with_signal = {
        guard_id: build_earlier_result(guard_id, guard_number, signal)
        for guard_id, guard_number, signal in named_signals
    }
    return {**results_without_signal, **results_with_signal}



# Let the guard alone judge the example purchase with the given session facts and earlier results
def check_purchase(example_message, policy, earlier_results, session = "evening", familiarity = None):
    event = read_purchase_message(example_message)
    decision_input = DecisionInput(
        event = event,
        policy = policy,
        facts = build_fact_sheet(event),
        familiarity = familiarity,
        session = build_session() if session == "evening" else session,
    )
    return SESSION_INTEGRITY_GUARD.check(decision_input, earlier_results)



# Find the evidence items of a result by the name of their fact
def find_all_evidence(guard_result, fact_name):
    return [evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name]



# Copy the example message onto a card of the real history, with the shop, the device and the moment a test asks for, and decide it with the whole engine
def decide_on_real_history(example_message, instruction, shop, device, moment):
    changed_message = copy.deepcopy(example_message)
    changed_message["mandate"]["instruction"] = instruction
    changed_message["authorization"]["card_id"] = "CA0023"
    changed_message["authorization"]["merchant"]["merchant_id"] = shop
    changed_message["authorization"]["customer_device_id"] = device
    changed_message["authorization"]["timestamp"] = moment
    event = read_purchase_message(changed_message)
    return decide(event, build_policy_from_mandate(event.mandate), build_empty_ledger_snapshot(event))









#### Step 2: Check the counts that pass, ask and decline ####

# Check that no signal passes under both sensitivities, with the count against both numbers in the evidence
@pytest.mark.parametrize("session_sensitivity", ["normal", "high"])
def test_no_signal_passes(example_message, session_sensitivity):
    guard_result = check_purchase(example_message, build_policy(session_sensitivity), build_earlier_results())
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.reason_code is None
    assert guard_result.customer_message is None
    assert [(evidence_item.value, evidence_item.threshold) for evidence_item in find_all_evidence(guard_result, "signal_count")] == [(0, 2), (0, 3)]
    assert find_all_evidence(guard_result, "signal") == []



# Check that one normal signal passes under both sensitivities, whichever signal it is
@pytest.mark.parametrize("session_sensitivity", ["normal", "high"])
@pytest.mark.parametrize("named_signal", [QUICK_SERIES, NEW_COUNTRY, UNFAMILIAR_MERCHANT])
def test_one_normal_signal_passes(example_message, session_sensitivity, named_signal):
    guard_result = check_purchase(example_message, build_policy(session_sensitivity), build_earlier_results(named_signal))
    assert guard_result.verdict == GuardVerdict.PASS
    assert [evidence_item.value for evidence_item in find_all_evidence(guard_result, "signal")] == [named_signal[2].name]



# Check that the normal signal of an unusual hour alone passes under both sensitivities
@pytest.mark.parametrize("session_sensitivity", ["normal", "high"])
def test_unusual_hour_alone_passes(example_message, session_sensitivity):
    guard_result = check_purchase(example_message, build_policy(session_sensitivity), build_earlier_results(), session = build_night_session())
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal == GuardSignal(name = "unusual_hour", strength = "normal")



# Check that one strong signal passes under a normal sensitivity and asks under a high one, and never declines
@pytest.mark.parametrize(
    "session_sensitivity, expected_verdict, expected_reason_code",
    [("normal", GuardVerdict.PASS, None), ("high", GuardVerdict.STEP_UP, ReasonCode.SESSION_ANOMALY)],
)
def test_one_strong_signal_passes_under_normal_and_asks_under_high(example_message, session_sensitivity, expected_verdict, expected_reason_code):
    guard_result = check_purchase(example_message, build_policy(session_sensitivity), build_earlier_results(NEW_DEVICE))
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == expected_reason_code



# Check that two signals ask and three decline under both sensitivities, with the reason of a session that does not look like the customer
@pytest.mark.parametrize("session_sensitivity", ["normal", "high"])
@pytest.mark.parametrize(
    "named_signals, expected_verdict",
    [
        ((QUICK_SERIES, NEW_COUNTRY), GuardVerdict.STEP_UP),
        ((NEW_DEVICE, UNFAMILIAR_MERCHANT), GuardVerdict.STEP_UP),
        ((QUICK_SERIES, NEW_COUNTRY, UNFAMILIAR_MERCHANT), GuardVerdict.DECLINE),
        ((NEW_DEVICE, QUICK_SERIES, NEW_COUNTRY, UNFAMILIAR_MERCHANT), GuardVerdict.DECLINE),
    ],
)
def test_two_signals_ask_and_three_decline(example_message, session_sensitivity, named_signals, expected_verdict):
    guard_result = check_purchase(example_message, build_policy(session_sensitivity), build_earlier_results(*named_signals))
    assert guard_result.verdict == expected_verdict
    assert guard_result.reason_code == ReasonCode.SESSION_ANOMALY
    assert guard_result.customer_message is not None
    assert sorted(evidence_item.value for evidence_item in find_all_evidence(guard_result, "signal")) == sorted(named_signal[2].name for named_signal in named_signals)



# Check that the own signal of the hour counts like any other, so the hour and one more signal ask and the hour and two more decline
def test_unusual_hour_counts_like_any_other_signal(example_message):
    asking_result = check_purchase(example_message, build_policy(), build_earlier_results(UNFAMILIAR_MERCHANT), session = build_night_session())
    declining_result = check_purchase(example_message, build_policy(), build_earlier_results(UNFAMILIAR_MERCHANT, NEW_DEVICE), session = build_night_session())
    assert asking_result.verdict == GuardVerdict.STEP_UP
    assert declining_result.verdict == GuardVerdict.DECLINE



# Check that exactly the numbers of the policy are used, so under 3 and 4 two signals pass, three ask and four decline
@pytest.mark.parametrize(
    "named_signals, expected_verdict",
    [
        ((QUICK_SERIES, NEW_COUNTRY), GuardVerdict.PASS),
        ((QUICK_SERIES, NEW_COUNTRY, UNFAMILIAR_MERCHANT), GuardVerdict.STEP_UP),
        ((NEW_DEVICE, QUICK_SERIES, NEW_COUNTRY, UNFAMILIAR_MERCHANT), GuardVerdict.DECLINE),
    ],
)
def test_numbers_of_the_policy_are_used(example_message, named_signals, expected_verdict):
    policy = build_policy(ask_signal_count = 3, decline_signal_count = 4)
    guard_result = check_purchase(example_message, policy, build_earlier_results(*named_signals))
    assert guard_result.verdict == expected_verdict
    assert [evidence_item.threshold for evidence_item in find_all_evidence(guard_result, "signal_count")] == [3, 4]



# Check that more signals never give a milder answer, so a strong signal that asks alone under a watched session still asks next to a normal one under 3 and 4
def test_strong_signal_below_the_asking_count_still_asks_under_a_watched_session(example_message):
    policy = build_policy("high", ask_signal_count = 3, decline_signal_count = 4)
    assert check_purchase(example_message, policy, build_earlier_results(NEW_DEVICE)).verdict == GuardVerdict.STEP_UP
    assert check_purchase(example_message, policy, build_earlier_results(NEW_DEVICE, QUICK_SERIES)).verdict == GuardVerdict.STEP_UP
    assert check_purchase(example_message, policy, build_earlier_results(NEW_COUNTRY, QUICK_SERIES)).verdict == GuardVerdict.PASS



# Check that the same signal from two guards counts once, and that the evidence names both guards
def test_same_signal_from_two_guards_counts_once(example_message):
    repeated_signal = ("usual_purchase_pattern", 22, GuardSignal(name = "unfamiliar_merchant", strength = "normal"))
    guard_result = check_purchase(example_message, build_policy("high"), build_earlier_results(UNFAMILIAR_MERCHANT, repeated_signal))
    assert guard_result.verdict == GuardVerdict.PASS
    assert [evidence_item.value for evidence_item in find_all_evidence(guard_result, "signal")] == ["unfamiliar_merchant"]
    assert find_all_evidence(guard_result, "signal_count")[0].value == 1
    assert "merchant_familiarity, usual_purchase_pattern" in find_all_evidence(guard_result, "signal")[0].source



# Check that a guard that did not run adds nothing, and that the signal of a guard outside the combined ones is not counted
def test_missing_result_and_foreign_guard_add_nothing(example_message):
    foreign_result = build_earlier_result("item_shop_consistency", 21, GuardSignal(name = "item_category_differs_from_shop", strength = "strong"))
    guard_result = check_purchase(example_message, build_policy("high"), {"item_shop_consistency": foreign_result})
    assert guard_result.verdict == GuardVerdict.PASS
    assert find_all_evidence(guard_result, "signal") == []









#### Step 3: Check the unusual hour ####

# Check that an hour without any purchase needs a history of at least 20 purchases, where 19 give no signal and exactly 20 give one
@pytest.mark.parametrize("history_purchase_count, expects_signal", [(19, False), (20, True), (139, True)])
def test_unusual_hour_needs_a_history_of_20_purchases(example_message, history_purchase_count, expects_signal):
    session = build_night_session(history_purchase_count = history_purchase_count)
    guard_result = check_purchase(example_message, build_policy(), build_earlier_results(), session = session)
    assert (guard_result.signal is not None) is expects_signal
    assert ([evidence_item.value for evidence_item in find_all_evidence(guard_result, "signal")] == ["unusual_hour"]) is expects_signal



# Check that the number of purchases comes from the policy, so a history of 19 gives the signal under a number of 19
def test_history_number_comes_from_the_policy(example_message):
    session = build_night_session(history_purchase_count = 19)
    guard_result = check_purchase(example_message, build_policy(hour_min_history_purchases = 19), build_earlier_results(), session = session)
    assert guard_result.signal == GuardSignal(name = "unusual_hour", strength = "normal")



# Check that an hour without any purchase is no signal on the recurring channel, because a scheduled payment runs by itself at any hour
def test_unusual_hour_on_the_recurring_channel_is_no_signal(example_message):
    session = build_night_session(is_recurring_channel = True)
    guard_result = check_purchase(example_message, build_policy(), build_earlier_results(UNFAMILIAR_MERCHANT), session = session)
    assert guard_result.signal is None
    assert guard_result.verdict == GuardVerdict.PASS



# Check that a customer whose card shops at night passes at night, also with one purchase at that hour only
@pytest.mark.parametrize("hour_purchase_count", [1, 5, 30])
def test_customer_who_shops_at_night_passes_at_night(example_message, hour_purchase_count):
    session = build_night_session(hour_purchase_count = hour_purchase_count)
    guard_result = check_purchase(example_message, build_policy("high"), build_earlier_results(), session = session)
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None



# Check that missing session facts and an unknown card give no signal of the hour, and that the signals of the earlier guards still count
@pytest.mark.parametrize("session", [None, build_session(local_hour = 4, local_time_text = "04.14", hour_purchase_count = None, history_purchase_count = None, card_is_known = False)])
def test_no_history_gives_no_signal_of_the_hour(example_message, session):
    passing_result = check_purchase(example_message, build_policy(), build_earlier_results(), session = session)
    asking_result = check_purchase(example_message, build_policy(), build_earlier_results(QUICK_SERIES, NEW_COUNTRY), session = session)
    assert passing_result.verdict == GuardVerdict.PASS
    assert passing_result.signal is None
    assert asking_result.verdict == GuardVerdict.STEP_UP









#### Step 4: Check the messages ####

# Check the decline on the hour, the device and the shop word for word, with the hour in Swiss local time
def test_decline_message_names_the_local_hour_the_device_and_the_shop(example_message):
    guard_result = check_purchase(example_message, build_policy(), build_earlier_results(UNFAMILIAR_MERCHANT, NEW_DEVICE), session = build_night_session())
    assert guard_result.customer_message == (
        "Declined. This purchase does not look like you. It was made at 04.14, an hour you never shop at, "
        "from a device you have never used, at a shop you have not bought from."
    )



# Check that every signal is named in the order hour, device, shop, country and quick series, whatever the order of the guards
def test_message_names_every_signal_in_the_stated_order(example_message):
    earlier_results = build_earlier_results(QUICK_SERIES, NEW_COUNTRY, NEW_DEVICE, UNFAMILIAR_MERCHANT)
    guard_result = check_purchase(example_message, build_policy(), earlier_results, session = build_night_session())
    assert guard_result.verdict == GuardVerdict.DECLINE
    assert guard_result.customer_message == (
        "Declined. This purchase does not look like you. It was made at 04.14, an hour you never shop at, "
        "from a device you have never used, at a shop you have not bought from, in a country you have never bought in, "
        "in a quick series of purchase attempts."
    )



# Check the question about a new device alone word for word
def test_question_about_a_new_device_alone(example_message):
    guard_result = check_purchase(example_message, build_policy("high"), build_earlier_results(NEW_DEVICE))
    assert guard_result.customer_message == "This purchase was made from a device you have never used. Is this you?"



# Check that a shop the customer bought from with another card is not called a shop the customer has not bought from
def test_shop_used_with_another_card_is_worded_as_rarely_used(example_message):
    familiarity = FamiliarityFacts(
        card_is_known = True,
        card_purchase_count = 0,
        customer_purchase_count = 2,
        issuer_card_count = 9,
        issuer_customer_count = 5,
        resembled_shop_name = None,
        resemblance_score = None,
        similarity_threshold = 0.85,
    )
    guard_result = check_purchase(example_message, build_policy(), build_earlier_results(UNFAMILIAR_MERCHANT, NEW_DEVICE), familiarity = familiarity)
    assert "at a shop you have rarely used with this card" in guard_result.customer_message
    assert "have not bought from" not in guard_result.customer_message









#### Step 5: Check the guard inside the whole engine, on the real card history ####

# Check that a purchase at a shop the card uses, from its usual device in the evening, is approved without any signal
def test_usual_purchase_is_approved_by_the_whole_engine(example_message):
    decision_trace = decide_on_real_history(example_message, WATCHING_INSTRUCTION, shop = "ME0025", device = "DVC-B73E47", moment = "2026-08-14T18:20:00Z")
    assert decision_trace.decision == Decision.APPROVE
    assert [guard_result.guard_id for guard_result in decision_trace.guards if guard_result.signal is not None] == []
    assert decision_trace.facts.familiarity["session"]["local_hour"] == 20
    assert decision_trace.facts.familiarity["session"]["device_purchase_count"] == 44



# Check that a new device alone asks under the instruction that watches the session and is approved under the instruction that does not
def test_new_device_alone_asks_only_under_the_watching_instruction(example_message):
    watched_trace = decide_on_real_history(example_message, WATCHING_INSTRUCTION, shop = "ME0025", device = "DVC-4C0E9B", moment = "2026-08-17T19:40:00Z")
    plain_trace = decide_on_real_history(example_message, PLAIN_INSTRUCTION, shop = "ME0025", device = "DVC-4C0E9B", moment = "2026-08-17T19:40:00Z")
    assert watched_trace.decision == Decision.STEP_UP
    assert watched_trace.reason_codes == [ReasonCode.SESSION_ANOMALY]
    assert watched_trace.aggregation.raised_by == ["session_integrity"]
    assert watched_trace.customer_message == "This purchase was made from a device you have never used. Is this you?"
    assert plain_trace.decision == Decision.APPROVE
    assert plain_trace.reason_codes == []



# Check that a night purchase from a new device at a shop the customer never used declines on three signals, under an instruction that says nothing about the session
def test_night_purchase_from_a_new_device_at_a_new_shop_declines(example_message):
    decision_trace = decide_on_real_history(example_message, PLAIN_INSTRUCTION, shop = "ME_EXAMPLE_0001", device = "DVC-4C0E9B", moment = "2026-08-18T02:14:00Z")
    assert decision_trace.decision == Decision.DECLINE
    assert decision_trace.reason_codes == [ReasonCode.SESSION_ANOMALY]
    assert decision_trace.aggregation.raised_by == ["session_integrity"]
    assert decision_trace.customer_message == (
        "Declined. This purchase does not look like you. It was made at 04.14, an hour you never shop at, "
        "from a device you have never used, at a shop you have not bought from."
    )
    assert decision_trace.facts.familiarity["session"]["local_hour"] == 4
    assert decision_trace.facts.familiarity["session"]["hour_purchase_count"] == 0
