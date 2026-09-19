# Script: test_guard_session_signals.py
# Purpose: Check that the device, the velocity and the country guard give their signal against the history of the card and never another verdict than a pass
# Author: Andrés Gallardo
# Date: September 2026

import itertools
from pathlib import Path

import pytest

from app.engine.facts import build_fact_sheet
from app.engine.guards.base import DecisionInput
from app.engine.guards.g_device import DeviceGuard
from app.engine.guards.g_geo import GeoGuard
from app.engine.guards.g_velocity import VelocityGuard
from app.models.decision import GuardFamily, GuardSignal, GuardVerdict
from app.models.events import read_purchase_message
from app.models.policy import Expectations, InternalPolicy
from app.state.session import SessionFacts









#### Step 1: Define the shared helpers ####

# Locate the four source files that must never name an identifier
GUARDS_FOLDER = Path(__file__).resolve().parent.parent / "backend" / "app" / "engine" / "guards"
GUARD_SOURCE_NAMES = ("g_device.py", "g_velocity.py", "g_geo.py", "g_session_integrity.py")
FORBIDDEN_WORDS = (
    "scenario", "request_id", "replay_order", "authorization_id", "merchant_id", "card_id", "customer_id", "device_id", "item_id", "item_details",
)



# Build the three guards as the registry does, from their number, their id and their family
DEVICE_GUARD = DeviceGuard(guard_number = 11, guard_id = "device", family = GuardFamily.SESSION)
VELOCITY_GUARD = VelocityGuard(guard_number = 12, guard_id = "velocity", family = GuardFamily.SESSION)
GEO_GUARD = GeoGuard(guard_number = 13, guard_id = "geo", family = GuardFamily.SESSION)



# Build a policy by hand, without the compiler
def build_policy(velocity_min_recent_attempts = 2, session_sensitivity = "normal"):
    return InternalPolicy(
        instruction = "A policy built by hand.",
        hard_rules = (),
        uncertainty_policy = "ask",
        expectations = Expectations(
            velocity_min_recent_attempts = velocity_min_recent_attempts,
            session_sensitivity = session_sensitivity,
        ),
        open_questions = (),
    )



# Build the session facts of a known card by hand, at an evening hour the card uses, from a device and in a country it uses
def build_session(device_purchase_count = 44, country_purchase_count = 117, recent_attempt_count = 0, shop_country = "CH"):
    return SessionFacts(
        card_is_known = True,
        history_purchase_count = 139,
        device_purchase_count = device_purchase_count,
        local_hour = 20,
        local_time_text = "20.20",
        hour_purchase_count = 14,
        shop_country = shop_country,
        country_purchase_count = country_purchase_count,
        recent_attempt_count = recent_attempt_count,
        is_recurring_channel = False,
    )



# Build the session facts of a card the history does not hold, which still carry the recent attempts of the message
def build_unknown_card_session(recent_attempt_count = 0):
    return SessionFacts(
        card_is_known = False,
        history_purchase_count = None,
        device_purchase_count = None,
        local_hour = 4,
        local_time_text = "04.14",
        hour_purchase_count = None,
        shop_country = "GB",
        country_purchase_count = None,
        recent_attempt_count = recent_attempt_count,
        is_recurring_channel = False,
    )



# Let one guard alone judge the example purchase with the given session facts
def check_purchase(guard, example_message, session, policy = None):
    event = read_purchase_message(example_message)
    decision_input = DecisionInput(
        event = event,
        policy = build_policy() if policy is None else policy,
        facts = build_fact_sheet(event),
        session = session,
    )
    return guard.check(decision_input, {})



# Find one evidence item of a result by the name of its fact
def find_evidence(guard_result, fact_name):
    return next(evidence_item for evidence_item in guard_result.evidence if evidence_item.fact == fact_name)









#### Step 2: Check the device guard ####

# Check that a device the card never used gives the strong signal new_device and still passes
def test_new_device_gives_the_strong_signal(example_message):
    guard_result = check_purchase(DEVICE_GUARD, example_message, build_session(device_purchase_count = 0))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal == GuardSignal(name = "new_device", strength = "strong")
    assert guard_result.reason_code is None
    assert guard_result.customer_message is None
    assert find_evidence(guard_result, "device_purchase_count").value == 0



# Check that a device the card used gives no signal, also when it used it only once
@pytest.mark.parametrize("device_purchase_count", [1, 2, 44])
def test_known_device_gives_no_signal(example_message, device_purchase_count):
    guard_result = check_purchase(DEVICE_GUARD, example_message, build_session(device_purchase_count = device_purchase_count))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None
    assert find_evidence(guard_result, "device_purchase_count").value == device_purchase_count



# Check that a device that is not stated gives no signal, and that the evidence says so
def test_device_that_is_not_stated_gives_no_signal(example_message):
    guard_result = check_purchase(DEVICE_GUARD, example_message, build_session(device_purchase_count = None))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None
    assert find_evidence(guard_result, "device_is_stated").value is False



# Check that missing session facts and an unknown card give no signal, and that the evidence says so
@pytest.mark.parametrize("session", [None, build_unknown_card_session()])
def test_device_guard_without_a_history_gives_no_signal(example_message, session):
    guard_result = check_purchase(DEVICE_GUARD, example_message, session)
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None
    assert find_evidence(guard_result, "card_history_is_available").value is False









#### Step 3: Check the velocity guard ####

# Check that recent attempts at or above the number of the policy give the normal signal quick_series, where exactly the number reaches it
@pytest.mark.parametrize(
    "recent_attempt_count, expected_signal",
    [
        (0, None),
        (1, None),
        (2, GuardSignal(name = "quick_series", strength = "normal")),
        (3, GuardSignal(name = "quick_series", strength = "normal")),
    ],
)
def test_recent_attempts_at_or_above_the_number_give_the_signal(example_message, recent_attempt_count, expected_signal):
    guard_result = check_purchase(VELOCITY_GUARD, example_message, build_session(recent_attempt_count = recent_attempt_count))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal == expected_signal
    assert guard_result.reason_code is None
    assert find_evidence(guard_result, "recent_attempt_count").value == recent_attempt_count
    assert find_evidence(guard_result, "recent_attempt_count").threshold == 2



# Check that the number comes from the policy, so 3 attempts give no signal under a number of 4 and 4 attempts give one
@pytest.mark.parametrize("recent_attempt_count, expects_signal", [(3, False), (4, True)])
def test_number_of_recent_attempts_comes_from_the_policy(example_message, recent_attempt_count, expects_signal):
    guard_result = check_purchase(
        VELOCITY_GUARD,
        example_message,
        build_session(recent_attempt_count = recent_attempt_count),
        policy = build_policy(velocity_min_recent_attempts = 4),
    )
    assert (guard_result.signal is not None) is expects_signal
    assert find_evidence(guard_result, "recent_attempt_count").threshold == 4



# Check that a quick series on an unknown card still gives the signal, because the count comes from the message and not from the history
def test_quick_series_on_an_unknown_card_gives_the_signal(example_message):
    guard_result = check_purchase(VELOCITY_GUARD, example_message, build_unknown_card_session(recent_attempt_count = 3))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal == GuardSignal(name = "quick_series", strength = "normal")



# Check that missing session facts give no signal, and that the evidence says so
def test_velocity_guard_without_session_facts_gives_no_signal(example_message):
    guard_result = check_purchase(VELOCITY_GUARD, example_message, None)
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None
    assert find_evidence(guard_result, "session_facts_are_available").value is False









#### Step 4: Check the country guard ####

# Check that a country the card never bought in gives the normal signal new_country and still passes
def test_new_country_gives_the_normal_signal(example_message):
    guard_result = check_purchase(GEO_GUARD, example_message, build_session(country_purchase_count = 0, shop_country = "GB"))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal == GuardSignal(name = "new_country", strength = "normal")
    assert guard_result.reason_code is None
    assert guard_result.customer_message is None
    assert find_evidence(guard_result, "country_purchase_count").value == 0
    assert find_evidence(guard_result, "country_purchase_count").source.endswith(" GB")



# Check that a foreign country the card uses gives no signal, so a favorite shop abroad is never a signal
@pytest.mark.parametrize("shop_country, country_purchase_count", [("US", 21), ("IT", 22), ("FR", 1), ("CH", 117)])
def test_country_the_card_uses_gives_no_signal(example_message, shop_country, country_purchase_count):
    guard_result = check_purchase(GEO_GUARD, example_message, build_session(country_purchase_count = country_purchase_count, shop_country = shop_country))
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None



# Check that missing session facts and an unknown card give no signal, also in a foreign country, and that the evidence says so
@pytest.mark.parametrize("session", [None, build_unknown_card_session()])
def test_geo_guard_without_a_history_gives_no_signal(example_message, session):
    guard_result = check_purchase(GEO_GUARD, example_message, session)
    assert guard_result.verdict == GuardVerdict.PASS
    assert guard_result.signal is None
    assert find_evidence(guard_result, "card_history_is_available").value is False









#### Step 5: Check that no input gives another verdict than a pass ####

# Check every combination of device count, country count, recent attempts and sensitivity on all three guards, which all pass without a reason and without a message
def test_no_input_gives_another_verdict_than_a_pass(example_message):
    device_counts = (None, 0, 1, 44)
    country_counts = (0, 1, 117)
    recent_attempt_counts = (0, 1, 2, 3, 50)
    sensitivities = ("normal", "high")
    sessions_and_policies = [
        (build_session(device_purchase_count = device_count, country_purchase_count = country_count, recent_attempt_count = recent_attempt_count), build_policy(session_sensitivity = sensitivity))
        for device_count, country_count, recent_attempt_count, sensitivity in itertools.product(device_counts, country_counts, recent_attempt_counts, sensitivities)
    ] + [
        (None, build_policy(session_sensitivity = "high")),
        (build_unknown_card_session(recent_attempt_count = 50), build_policy(session_sensitivity = "high")),
    ]
    guard_results = [
        check_purchase(guard, example_message, session, policy)
        for guard in (DEVICE_GUARD, VELOCITY_GUARD, GEO_GUARD)
        for session, policy in sessions_and_policies
    ]
    assert len(guard_results) == 3 * (4 * 3 * 5 * 2 + 2)
    assert {guard_result.verdict for guard_result in guard_results} == {GuardVerdict.PASS}
    assert {guard_result.reason_code for guard_result in guard_results} == {None}
    assert {guard_result.customer_message for guard_result in guard_results} == {None}
    assert all(len(guard_result.evidence) >= 1 for guard_result in guard_results)









#### Step 6: Check that no guard file names an identifier ####

# Check that none of the four guard files of the session names an identifier or the text a shop wrote about an item
@pytest.mark.parametrize("source_name", GUARD_SOURCE_NAMES)
def test_guard_file_names_no_identifier(source_name):
    source_text = (GUARDS_FOLDER / source_name).read_text(encoding = "utf-8").lower()
    found_words = [forbidden_word for forbidden_word in FORBIDDEN_WORDS if forbidden_word in source_text]
    assert found_words == []
