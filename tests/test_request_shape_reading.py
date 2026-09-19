# Script: test_request_shape_reading.py
# Purpose: Check that the reader tells an instruction for one single thing from an open one, finds the words that forbid extras, and that both reach the policy
# Author: Andrés Gallardo
# Date: September 2026

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from app.models.events import read_purchase_message
from app.models.policy import RequestedItem
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.request_shape import RequestShapeReading, read_request_shape









#### Step 1: Define the cases ####

# Point at the published table of instructions, which this test only reads
SCENARIO_CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_catalogue.csv"
)



# State what each published instruction gives, keyed by the name of its row in the table, as one thing first and no extras second
EXPECTED_PUBLIC_SHAPES = {
    "Connection check": (True, False),
    "Household budget": (False, False),
    "Requested item and order terms": (True, False),
    "Session integrity": (False, False),
    "Manipulated agent": (True, True),
}



# Describe one phrasing with the two answers it must give
@dataclass(frozen = True)
class PhrasingCase:
    instruction: str
    expected_one_thing: bool
    expected_no_addons: bool



# List phrasings that appear nowhere in the published data
PHRASING_CASES = [
    PhrasingCase("Renew my learning subscription each month for no more than CHF 19. No upgrades.", False, True),
    PhrasingCase("Book a hotel room for my trip for at most CHF 220 per night, cancellable.", True, False),
    PhrasingCase("Buy the materials for one room for at most CHF 900 in total.", False, False),
    PhrasingCase("Get me a monthly transit pass for up to CHF 90.", True, False),
    PhrasingCase("Order our groceries for the week from the supermarket, nothing else.", False, True),
    PhrasingCase("Buy a phone charger from an electronics store, no extras.", True, True),
    PhrasingCase("Buy a few groceries for tonight.", False, False),
    PhrasingCase("Don't add anything to the order.", False, True),
    PhrasingCase("Don’t add anything to the order.", False, True),
    PhrasingCase("Order a couple of paperbacks for the holidays.", False, False),
    PhrasingCase("Purchase a single desk lamp, without extras.", True, True),
    PhrasingCase("Reserve an airport transfer for Friday.", True, False),
    PhrasingCase("Buy the headphones I picked for at most CHF 150, only what I asked.", True, True),
    PhrasingCase("Buy the paint, and tell me what else I want.", False, False),
    PhrasingCase("Replace my broken kettle for at most CHF 60. Never add a warranty.", True, True),
    PhrasingCase("Buy clothing for me, up to CHF 250 per order.", False, False),
    PhrasingCase("BUY ONE NOTEBOOK. NO ADD-ONS.", True, True),
]









#### Step 2: Check the published instructions ####

# Check that each of the five published instructions gives its two answers and no open question
def test_published_instructions_give_their_shapes():
    scenario_catalogue = pd.read_csv(SCENARIO_CATALOGUE_PATH, dtype = str, keep_default_na = False)
    readings_by_name = {
        scenario_name: read_request_shape(instruction)
        for scenario_name, instruction in zip(scenario_catalogue["scenario_name"], scenario_catalogue["cardholder_instruction"])
    }
    observed_shapes = {scenario_name: (reading.asks_for_one_thing, reading.no_addons) for scenario_name, reading in readings_by_name.items()}
    assert observed_shapes == EXPECTED_PUBLIC_SHAPES
    assert {reading.open_questions for reading in readings_by_name.values()} == {()}









#### Step 3: Check the unseen phrasings ####

# Check both answers of every phrasing, as real booleans
@pytest.mark.parametrize("case", PHRASING_CASES, ids = [case.instruction for case in PHRASING_CASES])
def test_phrasing_gives_the_expected_shape(case):
    reading = read_request_shape(case.instruction)
    assert isinstance(reading, RequestShapeReading)
    assert reading.asks_for_one_thing is case.expected_one_thing
    assert reading.no_addons is case.expected_no_addons
    assert reading.open_questions == ()



# Check that a verb counts only with a word for a single thing directly after it, so a number later in the sentence does not count
def test_single_thing_word_must_follow_the_verb_directly():
    assert read_request_shape("Buy paint for one room.").asks_for_one_thing is False
    assert read_request_shape("Buy me one tin of paint.").asks_for_one_thing is True
    assert read_request_shape("Get me something nice.").asks_for_one_thing is False



# Check that the result cannot be changed
def test_reading_is_frozen():
    reading = read_request_shape("Buy one notebook.")
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.no_addons = True









#### Step 4: Check that the reading reaches the policy ####

# Read the example message with another instruction and return its mandate
def read_mandate(example_message, instruction):
    example_message["mandate"]["instruction"] = instruction
    return read_purchase_message(example_message).mandate



# Check that an instruction for one thing gives a requested item with one unit and the word that names the thing, and that the words that forbid extras reach the policy
def test_compiler_fills_the_requested_item_and_no_addons(example_message):
    mandate = read_mandate(example_message, "Buy the 27-inch monitor I chose for CHF 400 or less. Do not add anything I did not ask for.")
    expectations = build_policy_from_mandate(mandate).expectations
    assert expectations.requested_item == RequestedItem(kind_keywords = ("monitor",), max_quantity = 1, goal_quantity = 1)
    assert expectations.no_addons is True



# Check that an open instruction leaves the requested item empty
def test_compiler_leaves_the_requested_item_empty_for_an_open_instruction(example_message):
    mandate = read_mandate(example_message, "Order our household groceries for delivery, at most CHF 120 per order.")
    expectations = build_policy_from_mandate(mandate).expectations
    assert expectations.requested_item is None
    assert expectations.no_addons is False
