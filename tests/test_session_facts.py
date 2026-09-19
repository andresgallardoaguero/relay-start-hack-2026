# Script: test_session_facts.py
# Purpose: Check the session facts of a purchase on the real card history, which are the device, the Swiss local hour, the country and the recent attempts
# Author: Andrés Gallardo
# Date: September 2026

import copy
import dataclasses

import pytest

from app.models.events import read_purchase_message
from app.state.baselines import load_baselines
from app.state.session import SessionFacts, build_session_facts









#### Step 1: Define the shared helpers ####

# State the names of the fields that session facts without identifiers may carry
EXPECTED_FIELD_NAMES = [
    "card_is_known", "history_purchase_count", "device_purchase_count", "local_hour", "local_time_text", "hour_purchase_count",
    "shop_country", "country_purchase_count", "recent_attempt_count", "is_recurring_channel",
]



# State a card the history holds with 139 purchases outside the recurring channel, its most used device and a device it never used,
# and a night moment and an evening moment of August 2026 in UTC
KNOWN_CARD = "CA0023"
USUAL_DEVICE = "DVC-B73E47"
NEW_DEVICE = "DVC-4C0E9B"
NIGHT_MOMENT = "2026-08-18T02:14:00Z"
EVENING_MOMENT = "2026-08-14T18:20:00Z"



# Load the real counts once for all tests
@pytest.fixture(scope = "module")
def baselines():
    return load_baselines()



# Copy the example message onto another card, device, moment, shop country, channel and count of recent attempts, and read it through the strict reader
def build_event(example_message, card = KNOWN_CARD, device = USUAL_DEVICE, moment = EVENING_MOMENT, shop_country = "CH", channel = "ecommerce", recent_attempt_count = 0):
    changed_message = copy.deepcopy(example_message)
    changed_message["authorization"]["card_id"] = card
    changed_message["authorization"]["customer_device_id"] = device
    changed_message["authorization"]["timestamp"] = moment
    changed_message["authorization"]["merchant"]["merchant_country"] = shop_country
    changed_message["authorization"]["channel"] = channel
    changed_message["authorization"]["recent_attempt_count_10m"] = recent_attempt_count
    return read_purchase_message(changed_message)









#### Step 2: Check the hour ####

# Check that 02.14 in UTC on an August night is hour 4 in Swiss local time, an hour without any purchase in a history of 139 purchases
def test_night_moment_gives_local_hour_4_without_any_purchase(example_message, baselines):
    facts = build_session_facts(build_event(example_message, moment = NIGHT_MOMENT), baselines)
    assert facts.card_is_known is True
    assert facts.local_hour == 4
    assert facts.local_time_text == "04.14"
    assert facts.hour_purchase_count == 0
    assert facts.history_purchase_count == 139



# Check that 18.20 in UTC on an August evening is hour 20 in Swiss local time, with 14 purchases of the card
def test_evening_moment_gives_local_hour_20_with_14_purchases(example_message, baselines):
    facts = build_session_facts(build_event(example_message, moment = EVENING_MOMENT), baselines)
    assert facts.local_hour == 20
    assert facts.local_time_text == "20.20"
    assert facts.hour_purchase_count == 14
    assert facts.history_purchase_count == 139



# Check that the history count is the sum of the 24 hour counts of the card
def test_history_count_is_the_sum_of_the_hour_counts(example_message, baselines):
    facts = build_session_facts(build_event(example_message), baselines)
    assert facts.history_purchase_count == sum(baselines.get_card(KNOWN_CARD).local_hour_purchase_counts)



# Check that a winter moment converts with one hour of offset and a summer moment with two
@pytest.mark.parametrize(
    "moment, expected_hour, expected_time_text",
    [
        ("2026-01-15T02:14:00Z", 3, "03.14"),
        ("2026-08-18T02:14:00Z", 4, "04.14"),
        ("2026-01-15T23:30:00Z", 0, "00.30"),
        ("2026-08-18T22:30:00Z", 0, "00.30"),
    ],
)
def test_winter_converts_with_one_hour_and_summer_with_two(example_message, baselines, moment, expected_hour, expected_time_text):
    facts = build_session_facts(build_event(example_message, moment = moment), baselines)
    assert facts.local_hour == expected_hour
    assert facts.local_time_text == expected_time_text



# Check that a moment stated with another offset gives the same Swiss hour as the same moment in UTC
def test_moment_with_another_offset_gives_the_same_hour(example_message, baselines):
    facts = build_session_facts(build_event(example_message, moment = "2026-08-17T22:14:00-04:00"), baselines)
    assert facts.local_hour == 4









#### Step 3: Check the device ####

# Check that a device the card never used gives 0 and the most used device of the card gives 44
def test_new_device_gives_0_and_usual_device_gives_44(example_message, baselines):
    facts_on_new_device = build_session_facts(build_event(example_message, device = NEW_DEVICE), baselines)
    facts_on_usual_device = build_session_facts(build_event(example_message, device = USUAL_DEVICE), baselines)
    assert facts_on_new_device.device_purchase_count == 0
    assert facts_on_usual_device.device_purchase_count == 44



# Check that a device that is not stated gives None on a known card, because a missing fact is no new device
@pytest.mark.parametrize("device", ["", "   "])
def test_empty_device_gives_none(example_message, baselines, device):
    facts = build_session_facts(build_event(example_message, device = device), baselines)
    assert facts.card_is_known is True
    assert facts.device_purchase_count is None









#### Step 4: Check the country ####

# Check that the card gives 0 in a country it never bought in and 22 in a foreign country it uses
def test_new_country_gives_0_and_used_country_gives_22(example_message, baselines):
    facts_in_new_country = build_session_facts(build_event(example_message, shop_country = "GB"), baselines)
    facts_in_used_country = build_session_facts(build_event(example_message, shop_country = "IT"), baselines)
    assert (facts_in_new_country.shop_country, facts_in_new_country.country_purchase_count) == ("GB", 0)
    assert (facts_in_used_country.shop_country, facts_in_used_country.country_purchase_count) == ("IT", 22)



# Check that a card with a favorite shop abroad gives 21 purchases in that foreign country
def test_card_with_a_shop_abroad_gives_21_purchases_there(example_message, baselines):
    facts = build_session_facts(build_event(example_message, card = "CA0039", shop_country = "US"), baselines)
    assert facts.country_purchase_count == 21









#### Step 5: Check the unknown card, the recent attempts and the channel ####

# Check that an unknown card gives None for every count and still carries the hour, the country, the attempts and the channel
def test_unknown_card_gives_none_counts(example_message, baselines):
    facts = build_session_facts(build_event(example_message, card = "CA_UNKNOWN", moment = NIGHT_MOMENT, shop_country = "GB", recent_attempt_count = 3), baselines)
    assert facts.card_is_known is False
    assert facts.history_purchase_count is None
    assert facts.device_purchase_count is None
    assert facts.hour_purchase_count is None
    assert facts.country_purchase_count is None
    assert facts.local_hour == 4
    assert facts.shop_country == "GB"
    assert facts.recent_attempt_count == 3
    assert facts.is_recurring_channel is False



# Check that the recent attempts are copied from the message
@pytest.mark.parametrize("recent_attempt_count", [0, 1, 2, 7])
def test_recent_attempts_are_copied_from_the_message(example_message, baselines, recent_attempt_count):
    facts = build_session_facts(build_event(example_message, recent_attempt_count = recent_attempt_count), baselines)
    assert facts.recent_attempt_count == recent_attempt_count



# Check that only the recurring channel is a recurring channel
@pytest.mark.parametrize(
    "channel, expected_flag",
    [("recurring", True), ("ecommerce", False), ("mobile_wallet", False), ("in_store", False)],
)
def test_only_the_recurring_channel_is_recurring(example_message, baselines, channel, expected_flag):
    facts = build_session_facts(build_event(example_message, channel = channel), baselines)
    assert facts.is_recurring_channel is expected_flag









#### Step 6: Check the shape of the facts ####

# Check that the facts carry exactly the expected fields, none of which is an identifier, and cannot be changed
def test_facts_carry_no_identifier_and_are_frozen(example_message, baselines):
    facts = build_session_facts(build_event(example_message), baselines)
    assert isinstance(facts, SessionFacts)
    assert [field.name for field in dataclasses.fields(facts)] == EXPECTED_FIELD_NAMES
    values_as_text = [str(field_value) for field_value in dataclasses.asdict(facts).values()]
    assert KNOWN_CARD not in values_as_text
    assert USUAL_DEVICE not in values_as_text
    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.local_hour = 12
