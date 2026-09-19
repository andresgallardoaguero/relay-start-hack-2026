# Script: test_requested_item_reading.py
# Purpose: Check that the reader finds the words and the size of the one thing an instruction asks for, leaves category words and opening verbs out, and that the reading reaches the policy
# Author: Andrés Gallardo
# Date: September 2026

import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from app.engine.facts import build_fact_sheet
from app.models.events import read_purchase_message
from app.models.policy import RequestedItem
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.instruction_text import read_words
from app.policyc.request_shape import read_request_shape
from app.policyc.requested_item import RequestedItemReading, read_requested_item
from app.state.item_catalogue import get_default_item_catalogue









#### Step 1: Define the cases ####

# Point at the published table of instructions, which this test only reads
SCENARIO_CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_catalogue.csv"
)



# State what each published instruction gives, keyed by the name of its row in the table, as the keywords first and the attributes second.
# The household and the clothing instruction give nothing, because they do not ask for one thing.
EXPECTED_PUBLIC_READINGS = {
    "Connection check": ((), {}),
    "Household budget": ((), {}),
    "Requested item and order terms": (("road", "running", "shoe"), {"size": "43"}),
    "Session integrity": ((), {}),
    "Manipulated agent": (("monitor",), {}),
}



# List phrasings that appear nowhere in the published data, each with the keywords and the attributes it must give
PHRASING_CASES = [
    ("Buy a cycling helmet in size M for at most CHF 150.", ("cycling", "helmet"), {"size": "M"}),
    ("Book a hotel room in Zurich for at most CHF 300.", (), {}),
    ("Order a paperback book for under CHF 30.", ("paperback",), {}),
    ("Get me a phone charger and nothing else.", ("phone", "charger"), {}),
    ("Buy one pair of hiking boots in size 44.5 for no more than CHF 250.", ("hiking", "boot"), {"size": "44.5"}),
    ("Buy a helmet, not shoes, in size L.", ("helmet",), {"size": "L"}),
    ("Buy a monitor without a tablet accessory.", ("monitor",), {}),
    ("Get me a regional rail ticket for up to CHF 40.", ("rail",), {}),
    ("Buy one gift for my team for CHF 50 or less.", (), {}),
]



# Read an instruction as the compiler does, with the shape of the request first
def read_instruction(instruction):
    asks_for_one_thing = read_request_shape(instruction).asks_for_one_thing
    return read_requested_item(instruction, get_default_item_catalogue(), asks_for_one_thing)



# Load the published instructions once, keyed by the name of their row
@pytest.fixture(scope = "module")
def public_instructions():
    scenario_catalogue = pd.read_csv(SCENARIO_CATALOGUE_PATH, dtype = str, keep_default_na = False)
    return dict(zip(scenario_catalogue["scenario_name"], scenario_catalogue["cardholder_instruction"]))



# Read the example message with another instruction and return its mandate
def read_mandate(example_message, instruction):
    example_message["mandate"]["instruction"] = instruction
    return read_purchase_message(example_message).mandate









#### Step 2: Check the readings ####

# Check the five published instructions
def test_public_instructions_give_the_expected_readings(public_instructions):
    assert sorted(public_instructions) == sorted(EXPECTED_PUBLIC_READINGS)
    observed_readings = {
        row_name: (read_instruction(instruction).kind_keywords, dict(read_instruction(instruction).attributes))
        for row_name, instruction in public_instructions.items()
    }
    assert observed_readings == EXPECTED_PUBLIC_READINGS
    assert [row_name for row_name, instruction in public_instructions.items() if read_instruction(instruction).open_questions != ()] == []



# Check phrasings that are not in the published data
@pytest.mark.parametrize("instruction, expected_keywords, expected_attributes", PHRASING_CASES, ids = [case[0] for case in PHRASING_CASES])
def test_unseen_phrasing_gives_the_expected_reading(instruction, expected_keywords, expected_attributes):
    reading = read_instruction(instruction)
    assert reading.kind_keywords == expected_keywords
    assert dict(reading.attributes) == expected_attributes
    assert reading.open_questions == ()



# Check that an instruction which does not ask for one thing gives nothing, even when it names goods and a size
def test_instruction_for_no_single_thing_gives_nothing():
    reading = read_requested_item("Order running shoes in size 43 whenever mine wear out.", get_default_item_catalogue(), False)
    assert reading == RequestedItemReading(kind_keywords = (), attributes = {}, open_questions = ())



# Check that the verb "Book" never becomes a keyword, and that whatever the hotel sentence gives matches the catalogue item "Hotel room"
def test_hotel_sentence_never_gives_book_and_matches_the_hotel_room():
    reading = read_instruction("Book a hotel room in Zurich for at most CHF 300.")
    assert "book" not in reading.kind_keywords
    assert all(kind_keyword in read_words("Hotel room") for kind_keyword in reading.kind_keywords)



# Check that a word of a category phrase is never a keyword, compared in singular form
def test_category_words_are_never_keywords():
    assert read_instruction("Buy one ordinary grocery item for CHF 20 or less from a shop I use regularly.").kind_keywords == ()
    assert "book" not in read_instruction("Buy the paperback books I saved.").kind_keywords
    assert "subscription" not in read_instruction("Get me a monthly media subscription.").kind_keywords



# Check that two different sizes give no size and one open question, because a size that cannot be read exactly is missing
def test_two_sizes_give_no_size_and_an_open_question():
    reading = read_instruction("Buy a cycling helmet in size M or size L.")
    assert reading.kind_keywords == ("cycling", "helmet")
    assert dict(reading.attributes) == {}
    assert len(reading.open_questions) == 1
    assert "L and M" in reading.open_questions[0]



# Check that the reading is frozen and that its attributes cannot be changed
def test_reading_cannot_be_changed():
    reading = read_instruction("Buy a cycling helmet in size M.")
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.kind_keywords = ("monitor",)
    with pytest.raises(TypeError):
        reading.attributes["size"] = "L"









#### Step 3: Check that the reading reaches the policy and meets the cart ####

# Check that the compiler carries the keywords and the attributes and keeps the two quantities
def test_compiler_carries_keywords_and_attributes(example_message):
    mandate = read_mandate(example_message, "Replace my worn road-running shoes in size 43. Pay no more than CHF 200.")
    expectations = build_policy_from_mandate(mandate).expectations
    assert expectations.requested_item == RequestedItem(
        kind_keywords = ("road", "running", "shoe"),
        attributes = {"size": "43"},
        max_quantity = 1,
        goal_quantity = 1,
    )



# Check that an open instruction keeps the requested item empty, and that the question about two sizes follows those of the request shape
def test_compiler_keeps_open_instructions_empty_and_passes_the_question_on(example_message):
    open_mandate = read_mandate(example_message, "Order our household groceries for delivery, at most CHF 120 per order.")
    assert build_policy_from_mandate(open_mandate).expectations.requested_item is None
    two_sizes_mandate = read_mandate(example_message, "Buy a cycling helmet in size M or size L for at most CHF 150.")
    two_sizes_policy = build_policy_from_mandate(two_sizes_mandate)
    assert two_sizes_policy.expectations.requested_item.attributes == {}
    assert len([open_question for open_question in two_sizes_policy.open_questions if "more than one size" in open_question]) == 1



# Check that the fact sheet carries the words of an item name in the same form as the keywords, and the facts of the shop text
def test_fact_sheet_carries_name_words_and_text_facts(example_message):
    example_message["authorization"]["items"][0]["item_name"] = "Road-running shoes"
    example_message["authorization"]["items"][0]["item_details"] = "Road-running shoe, size 43; returns accepted within 30 days"
    line = build_fact_sheet(read_purchase_message(example_message)).lines[0]
    assert line.name_words == ("road", "running", "shoe")
    assert line.text_facts.size == "43"
    assert line.text_facts.return_days == 30
