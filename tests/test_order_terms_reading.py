# Script: test_order_terms_reading.py
# Purpose: Check that the reader finds the number of return days an instruction asks for, asks about a requirement it cannot read, and that the reading reaches the policy
# Author: Andrés Gallardo
# Date: September 2026

import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from app.models.events import read_purchase_message
from app.policyc.compiler import build_policy_from_mandate
from app.policyc.order_terms import OrderTermsReading, read_order_terms









#### Step 1: Define the cases ####

# Point at the published table of instructions, which this test only reads
SCENARIO_CATALOGUE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "2026-09-18_viseca-2026" / "data" / "scenario_catalogue.csv"
)



# State the return days each published instruction gives, keyed by the name of its row in the table
EXPECTED_PUBLIC_RETURN_DAYS = {
    "Connection check": None,
    "Household budget": None,
    "Requested item and order terms": 14,
    "Session integrity": None,
    "Manipulated agent": None,
}



# List phrasings that appear nowhere in the published data, each with the return days it must give and whether it must ask the customer
PHRASING_CASES = [
    ("Buy a jacket, returnable for at least 30 days.", 30, False),
    ("Buy the shoes I picked only if I can return them within 30 days.", 30, False),
    ("Buy a helmet with a 14-day return window.", 14, False),
    ("Buy a coat that can be returned for no less than 21 days.", 21, False),
    ("Returns matter to me. Buy a monitor only if it can be returned within 30 days.", 30, False),
    ("Buy a phone charger, no returns needed.", None, False),
    ("Buy a lamp. I do not need returns.", None, False),
    ("Buy a monitor only if I can send it back.", None, True),
    ("Buy shoes that I can return.", None, True),
    ("Buy shoes I can return within 14 days, or better within 30 days if the shop allows returns that long.", None, True),
    ("Do not buy anything that cannot be returned within 14 days.", None, True),
    ("Buy a jacket that can be returned within 14.5 days.", None, True),
    ("Buy a jacket that can be returned within 0 days.", None, True),
    ("Buy one grocery item for CHF 20 or less within 3 days.", None, False),
]



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

# Check the five published instructions, where only the shoe instruction asks for return days and none leaves a question
def test_public_instructions_give_the_expected_return_days(public_instructions):
    observed_return_days = {row_name: read_order_terms(instruction).min_return_days for row_name, instruction in public_instructions.items()}
    assert observed_return_days == EXPECTED_PUBLIC_RETURN_DAYS
    assert [row_name for row_name, instruction in public_instructions.items() if read_order_terms(instruction).open_questions != ()] == []



# Check phrasings that are not in the published data
@pytest.mark.parametrize("instruction, expected_return_days, expects_a_question", PHRASING_CASES, ids = [case[0] for case in PHRASING_CASES])
def test_unseen_phrasing_gives_the_expected_return_days(instruction, expected_return_days, expects_a_question):
    reading = read_order_terms(instruction)
    assert reading.min_return_days == expected_return_days
    assert (len(reading.open_questions) == 1) == expects_a_question
    assert len(reading.open_questions) <= 1



# Check that the reading is frozen
def test_reading_cannot_be_changed():
    reading = read_order_terms("Buy a jacket, returnable for at least 30 days.")
    assert reading == OrderTermsReading(min_return_days = 30, open_questions = ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.min_return_days = 1









#### Step 3: Check that the reading reaches the policy ####

# Check that the compiler fills the return days of the shoe sentence and leaves no question
def test_compiler_fills_the_return_days(example_message):
    mandate = read_mandate(
        example_message,
        "Replace my worn road-running shoes in size 43. Buy only from a specialist sports retailer, only if the order can be returned within 14 days or more, and pay no more than CHF 200.",
    )
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.min_return_days == 14
    assert [open_question for open_question in policy.open_questions if "return" in open_question.lower()] == []



# Check that a return requirement without a number reaches the policy as an open question and as no minimum
def test_compiler_passes_the_question_on(example_message):
    mandate = read_mandate(example_message, "Buy a monitor for CHF 400 or less, only if I can send it back.")
    policy = build_policy_from_mandate(mandate)
    assert policy.expectations.min_return_days is None
    assert len([open_question for open_question in policy.open_questions if "number of days" in open_question]) == 1
