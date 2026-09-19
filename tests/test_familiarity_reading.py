# Script: test_familiarity_reading.py
# Purpose: Check that the reader finds in an instruction whether the shop has to be used before or used regularly, as a condition or as a wish
# Author: Andrés Gallardo
# Date: September 2026

from pathlib import Path

import pandas as pd
import pytest

from app.models.events import read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.familiarity import FamiliarityReading, read_familiarity









#### Step 1: Define the instructions and what each must give ####

# Locate the published instructions, which the tests only ever read
CASE_DATA_FOLDER = Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data"
INSTRUCTION_TABLE_PATH = CASE_DATA_FOLDER / "scenario_catalogue.csv"



# State what each of the five published instructions must give, in the order of the file
EXPECTED_PUBLIC_READINGS = [
    ("required", "regularly"),
    ("any", None),
    ("any", None),
    ("required", "before"),
    ("required", "before"),
]



# State phrasings outside the published data that make a familiar shop a condition
REQUIRED_PHRASINGS = [
    ("Buy coffee from a shop I use regularly.", "regularly"),
    ("Get the groceries at my usual shop.", "regularly"),
    ("Order vegetables from my regular grocer.", "regularly"),
    ("Buy bread where I normally shop.", "regularly"),
    ("Order the refill from a store we often order from.", "regularly"),
    ("Only buy from shops I have used before.", "before"),
    ("Buy the lamp from a seller I have bought from before.", "before"),
    ("Book it somewhere I've shopped before.", "before"),
    ("Buy a charger from a shop I know.", "before"),
    ("Buy the cable from a trusted seller.", "before"),
    ("Buy socks, ideally black, from a shop I have used before.", "before"),
]



# State phrasings that make a familiar shop a wish, with the softening word in the clause or alone in the clause after it
PREFERRED_PHRASINGS = [
    ("Buy a charger, preferably from a shop I know.", "before"),
    ("Buy socks from a shop I use regularly if possible.", "regularly"),
    ("Buy socks from a shop I use regularly, if possible.", "regularly"),
    ("Ideally order from a store we have ordered from before.", "before"),
]



# State phrasings that say nothing about how familiar the shop has to be
OPEN_PHRASINGS = [
    "Buy only from a specialist sports retailer.",
    "Order our household groceries for delivery and keep each order at or below CHF 120.",
    "Buy my usual coffee beans for CHF 30 or less.",
    "Buy from the shop I told you about.",
    "Buy shoes anywhere I can get them for CHF 90 or less.",
    "Buy me something nice.",
]



# Read the example message with another instruction and return its mandate
def read_mandate(example_message, instruction):
    example_message["mandate"]["instruction"] = instruction
    return read_purchase_message(example_message).mandate









#### Step 2: Check the published instructions ####

# Check that the five published instructions give regularly, any, any, before and before
def test_five_public_instructions():
    instruction_table = pd.read_csv(INSTRUCTION_TABLE_PATH, dtype = str, keep_default_na = False)
    instructions = instruction_table.sort_values("scenario_id")["cardholder_instruction"].tolist()
    assert len(instructions) == 5
    readings = [read_familiarity(instruction) for instruction in instructions]
    assert [(reading.merchant_familiarity, reading.familiarity_bar) for reading in readings] == EXPECTED_PUBLIC_READINGS
    assert [reading.open_questions for reading in readings] == [()] * 5









#### Step 3: Check phrasings outside the published data ####

# Check the phrasings that make a familiar shop a condition
@pytest.mark.parametrize("instruction, expected_bar", REQUIRED_PHRASINGS)
def test_phrasing_makes_a_familiar_shop_a_condition(instruction, expected_bar):
    assert read_familiarity(instruction) == FamiliarityReading(merchant_familiarity = "required", familiarity_bar = expected_bar, open_questions = ())



# Check the phrasings that make a familiar shop a wish
@pytest.mark.parametrize("instruction, expected_bar", PREFERRED_PHRASINGS)
def test_softened_phrasing_makes_a_familiar_shop_a_wish(instruction, expected_bar):
    assert read_familiarity(instruction) == FamiliarityReading(merchant_familiarity = "preferred", familiarity_bar = expected_bar, open_questions = ())



# Check the phrasings that state nothing about familiarity
@pytest.mark.parametrize("instruction", OPEN_PHRASINGS)
def test_phrasing_without_familiarity_gives_any(instruction):
    assert read_familiarity(instruction) == FamiliarityReading(merchant_familiarity = "any", familiarity_bar = None, open_questions = ())



# Check that upper case and extra spaces change nothing
def test_case_and_spacing_do_not_matter():
    reading = read_familiarity("BUY COFFEE FROM A SHOP   I USE REGULARLY.")
    assert (reading.merchant_familiarity, reading.familiarity_bar) == ("required", "regularly")



# Check that regular use wins over one earlier purchase when an instruction states both
def test_stricter_bar_wins():
    reading = read_familiarity("Buy from a shop I know. It has to be a shop I use regularly.")
    assert (reading.merchant_familiarity, reading.familiarity_bar) == ("required", "regularly")



# Check that a condition wins over a wish when an instruction states both
def test_condition_wins_over_wish():
    reading = read_familiarity("Preferably a shop I use regularly, but it must be a shop I know.")
    assert (reading.merchant_familiarity, reading.familiarity_bar) == ("required", "before")



# Check that a clause about shops the customer has not used states no bar and asks the customer what was meant
@pytest.mark.parametrize(
    "instruction",
    ["Try a shop I have never used.", "Buy the gift from a store I haven't used before."],
)
def test_negated_phrasing_gives_any_and_an_open_question(instruction):
    reading = read_familiarity(instruction)
    assert (reading.merchant_familiarity, reading.familiarity_bar) == ("any", None)
    assert len(reading.open_questions) == 1
    assert "shops you have not used" in reading.open_questions[0]









#### Step 4: Check the compiler ####

# Check that the compiler fills the three fields, with the purchases of regular use from the settings
def test_compiler_fills_the_familiarity_fields(example_message):
    mandate = read_mandate(example_message, "Buy one grocery item for CHF 20 or less from a shop I use regularly.")
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.merchant_familiarity == "required"
    assert policy.expectations.familiarity_bar == "regularly"
    assert policy.expectations.familiarity_regular_min_purchases == 3
    assert policy.open_questions == ()



# Check that an instruction without familiarity leaves the fields at their defaults
def test_compiler_leaves_the_defaults_without_familiarity(example_message):
    mandate = read_mandate(example_message, "Buy one grocery item for CHF 20 or less.")
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.merchant_familiarity == "any"
    assert policy.expectations.familiarity_bar is None



# Check that the question about familiarity comes last, after the question about the order limit
def test_compiler_puts_the_familiarity_question_last(example_message):
    mandate = read_mandate(example_message, "Buy clothing for at most EUR 150 from a shop I have never used.")
    policy = build_policy_from_mandate(mandate)
    assert len(policy.open_questions) == 2
    assert "EUR 150.00" in policy.open_questions[0]
    assert "shops you have not used" in policy.open_questions[1]
